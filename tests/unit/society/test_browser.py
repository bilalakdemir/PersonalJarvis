"""The agent's browser: provider mapping, out-of-process jobs, the tool, the board."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.core import config as cfg
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.tool.society_browser import LeadSocietyBrowserTool
from jarvis.society.browser.bridge import (
    _complete_with_capacity_retry,
    _is_simple_page_title_request,
)
from jarvis.society.browser.llm import LLMUnavailable, llm_spec_for
from jarvis.society.browser.session import (
    BrowserJobs,
    BrowserUnavailable,
    profile_dir,
    profile_has_logins,
)
from jarvis.society.browser.tool import BrowserTool, task_needs_approval
from jarvis.society.events import MsgType
from jarvis.society.failure_reasons import FailureReason
from jarvis.society.runtime import SocietyRuntime

CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)

#: A stand-in for runner.py: answers the protocol without browser-use.
FAKE_RUNNER = """
import json, sys, time
req = json.loads(sys.stdin.readline())
mode = req.get("mode")
def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
if mode == "probe":
    emit({"kind": "done", "ok": True, "version": "fake"})
elif mode == "login":
    emit({"kind": "login_open", "start_url": req.get("start_url")})
    line = sys.stdin.readline()
    emit({"kind": "done", "ok": True})
elif mode == "run":
    if "explode" in req["task"]:
        emit({"kind": "done", "ok": False, "error": "RuntimeError: boom"})
        sys.exit(1)
    if "hang" in req["task"]:
        time.sleep(30)
    for n in range(1, 7):
        emit({"kind": "step", "n": n, "url": f"https://example.com/{n}"})
    emit({"kind": "done", "ok": True, "final_result": "The answer is 42.",
          "urls": ["https://example.com/6"], "errors": [], "steps": 6, "seconds": 0.2,
          "cost_usd": 0.03, "headless": req.get("headless"), "llm": req.get("llm"),
          "profile_dir": req.get("profile_dir"), "cdp_url": req.get("cdp_url")})
"""


@pytest.fixture
def fake_runner(tmp_path: Path) -> Path:
    path = tmp_path / "fake_runner.py"
    path.write_text(FAKE_RUNNER, encoding="utf-8")
    return path


def _jobs(tmp_path: Path, fake_runner: Path, *, installed: bool = True) -> BrowserJobs:
    return BrowserJobs(
        tmp_path, python=Path(sys.executable), runner=fake_runner, installed=lambda: installed
    )


# ------------------------------------------------------------------ llm


def test_llm_mapping_with_explicit_keys():
    spec = llm_spec_for("anthropic", "claude-x", secret=lambda p: "sk-ant")
    assert spec.cls == "ChatAnthropic" and spec.model == "claude-x" and spec.api_key == "sk-ant"
    grok = llm_spec_for("grok", secret=lambda p: "xai")
    assert grok.cls == "ChatOpenAI" and grok.base_url and "x.ai" in grok.base_url
    nvidia = llm_spec_for("nvidia", secret=lambda p: "nvapi-test")
    assert nvidia.cls == "ChatOpenAI"
    assert nvidia.model == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    assert nvidia.base_url == "https://integrate.api.nvidia.com/v1"
    ollama = llm_spec_for("ollama", "qwen3:8b", secret=lambda p: None)
    assert ollama.cls == "ChatOllama" and ollama.api_key is None
    assert "api_key" not in ollama.to_request()
    with pytest.raises(LLMUnavailable) as no_key:
        llm_spec_for("openai", secret=lambda p: None)
    assert no_key.value.reason is FailureReason.AUTH_FAILED
    with pytest.raises(LLMUnavailable) as unknown:
        llm_spec_for("deepseek-harness", secret=lambda p: "k")
    assert unknown.value.reason is FailureReason.BLOCKED_BY_POLICY


def test_router_browser_moves_blind_nvidia_main_to_omni(monkeypatch, tmp_path):
    class Manager:
        active_provider = "nvidia"

        @staticmethod
        def _fast_model(_provider):
            return "nvidia/nemotron-3-super-120b-a12b"

    from jarvis.core import runtime_refs

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: Manager())

    assert LeadSocietyBrowserTool._model_pick() == (
        "nvidia",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    )


async def test_live_browser_retries_known_nvidia_worker_saturation(monkeypatch):
    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("jarvis.society.browser.bridge.asyncio.sleep", _no_sleep)

    class SaturatedThenHealthyBrain:
        def __init__(self) -> None:
            self.attempts = 0

        async def complete(self, _request):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError(
                    "ResourceExhausted: Worker local total request limit reached (16/16)"
                )
            yield BrainDelta(content="Example Domain", usage={"output_tokens": 2})

    brain = SaturatedThenHealthyBrain()
    request = BrainRequest(messages=(BrainMessage(role="user", content="read title"),))
    text, usage = await _complete_with_capacity_retry(
        brain, request, provider="nvidia", overrides={}
    )

    assert brain.attempts == 3
    assert sleeps == [1.0, 2.0]
    assert text == "Example Domain"
    assert usage == {"output_tokens": 2}


async def test_live_browser_does_not_retry_unrelated_provider_errors(monkeypatch):
    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("jarvis.society.browser.bridge.asyncio.sleep", _no_sleep)

    class BrokenBrain:
        def __init__(self) -> None:
            self.attempts = 0

        async def complete(self, _request):
            self.attempts += 1
            raise RuntimeError("401 invalid API key")
            yield  # pragma: no cover - make this an async generator

    brain = BrokenBrain()
    request = BrainRequest(messages=(BrainMessage(role="user", content="read title"),))
    with pytest.raises(RuntimeError, match="401 invalid API key"):
        await _complete_with_capacity_retry(
            brain, request, provider="nvidia", overrides={}
        )

    assert brain.attempts == 1
    assert sleeps == []


async def test_live_browser_does_not_apply_nvidia_saturation_retry_to_other_providers(
    monkeypatch,
):
    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("jarvis.society.browser.bridge.asyncio.sleep", _no_sleep)

    class SaturatedBrain:
        def __init__(self) -> None:
            self.attempts = 0

        async def complete(self, _request):
            self.attempts += 1
            raise RuntimeError(
                "ResourceExhausted: Worker local total request limit reached (16/16)"
            )
            yield  # pragma: no cover - make this an async generator

    brain = SaturatedBrain()
    request = BrainRequest(messages=(BrainMessage(role="user", content="read title"),))
    with pytest.raises(RuntimeError, match="Worker local total request limit reached"):
        await _complete_with_capacity_retry(
            brain, request, provider="openai", overrides={}
        )

    assert brain.attempts == 1
    assert sleeps == []


def test_simple_page_title_request_is_narrow_and_http_only():
    assert _is_simple_page_title_request(
        "Navigate to https://example.com and return the page title",
        "https://example.com",
    )
    assert not _is_simple_page_title_request("Tell me the page title", "")
    assert not _is_simple_page_title_request(
        "Click Login and tell me the page title",
        "https://example.com",
    )
    assert not _is_simple_page_title_request(
        "Tell me the page title",
        "file:///C:/secret.txt",
    )


def test_task_needs_approval_words():
    assert task_needs_approval("Send the invoice to Bob")
    assert task_needs_approval("Bitte den Newsletter kündigen")  # i18n-allow: sample task
    assert not task_needs_approval("Read today's headlines and summarize them")


# ----------------------------------------------------------------- jobs


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout", provider="openai", model="gpt-5")
    try:
        yield runtime
    finally:
        await runtime.close()


async def test_own_browser_request_uses_preferred_browser(rt, tmp_path, fake_runner, monkeypatch):
    preferred = tmp_path / "brave.exe"
    monkeypatch.setattr(
        "jarvis.society.browser.install.preferred_browser_executable",
        lambda *_: preferred,
    )
    jobs = _jobs(tmp_path, fake_runner)
    scout = await rt.roster.get("scout")

    request = jobs._request_base(scout, headless=True)  # noqa: SLF001 — selection contract

    assert request["executable_path"] == str(preferred)
    assert "cdp_url" not in request


async def test_run_streams_steps_and_returns_the_outcome(rt, tmp_path, fake_runner):
    jobs = _jobs(tmp_path, fake_runner)
    scout = await rt.roster.get("scout")
    seen: list[dict] = []
    outcome = await jobs.run(
        scout, task="find it", llm={"class": "ChatOpenAI", "model": "m"}, on_step=seen.append
    )
    assert outcome.ok and outcome.final_result == "The answer is 42."
    assert outcome.steps == 6 and outcome.cost_usd == 0.03
    assert [e["n"] for e in seen if e["kind"] == "step"] == [1, 2, 3, 4, 5, 6]
    assert not jobs.running_for("scout")
    assert profile_dir(tmp_path, "scout").is_dir()


async def test_run_failure_and_timeout(rt, tmp_path, fake_runner):
    jobs = _jobs(tmp_path, fake_runner)
    scout = await rt.roster.get("scout")
    bad = await jobs.run(scout, task="explode now", llm={})
    assert bad.ok is False and "boom" in (bad.error or "")
    slow = await jobs.run(scout, task="hang", llm={}, wall_s=1.5)
    assert slow.ok is False and "exceeded" in (slow.error or "")
    assert not jobs.running_for("scout")


async def test_not_installed_and_attach_mode(rt, tmp_path, fake_runner):
    scout = await rt.roster.get("scout")
    with pytest.raises(BrowserUnavailable) as exc:
        await _jobs(tmp_path, fake_runner, installed=False).run(scout, task="x", llm={})
    assert exc.value.reason is FailureReason.BLOCKED_BY_POLICY
    attached = await rt.roster.update("scout", {"browser_mode": "attach"})
    jobs = _jobs(tmp_path, fake_runner)
    status = jobs.status_for(attached)
    assert status["mode"] == "attach" and status["cdp_url"]
    assert (await jobs.login(attached))["skipped"]


async def test_login_session_closes_on_done(rt, tmp_path, fake_runner):
    import asyncio

    jobs = _jobs(tmp_path, fake_runner)
    scout = await rt.roster.get("scout")
    task = asyncio.create_task(jobs.login(scout, start_url="https://example.com/login"))
    for _ in range(50):
        await asyncio.sleep(0.05)
        if jobs.running_for("scout"):
            break
    assert await jobs.end_login("scout") is True
    result = await asyncio.wait_for(task, timeout=10)
    assert result["ok"] is True
    assert profile_has_logins(tmp_path, "scout") is False


# ----------------------------------------------------------------- tool


async def test_tool_runs_and_writes_digests(rt, tmp_path, fake_runner, monkeypatch):
    jobs = _jobs(tmp_path, fake_runner)
    monkeypatch.setattr(
        "jarvis.society.browser.tool.llm_spec_for",
        lambda provider, model="", **kw: llm_spec_for(provider, model, secret=lambda p: "k"),
    )
    tool = BrowserTool(rt, "scout", jobs)
    res = await tool.execute({"task": "read the headlines", "url": "https://news.example"}, CTX)
    assert res.success, res.error
    assert res.output["final_result"] == "The answer is 42."
    assert res.output["provider"] == "openai"
    digests = [e for e in await rt.store.events_since(0) if e.msg_type is MsgType.DIGEST]
    kinds = [e.payload["kind"] for e in digests]
    assert kinds == ["browser_steps", "browser_done"]
    assert digests[-1].cost_usd == 0.03
    assert (await rt.store.agent_stats("scout"))["total_cost_usd"] == pytest.approx(0.03)


async def test_tool_gates(rt, tmp_path, fake_runner):
    jobs = _jobs(tmp_path, fake_runner)
    tool = BrowserTool(rt, "scout", jobs)
    assert tool.risk_tier_for_args({"task": "delete the old posts"}) == "ask"
    # An explicit Ask agent queues an acting task for the person.
    await rt.roster.update("scout", {"approval_mode": "ask"})
    queued = await tool.execute({"task": "send the report to the team"}, CTX)
    assert queued.success is False and queued.output["reason"] == "approval_required"
    assert len(await rt.approvals.pending()) == 1
    # Bypass still honors an explicit require-approval rule.
    await rt.roster.update(
        "scout",
        {
            "approval_mode": "bypass",
            "approval_rules": {"require_approval": ["core:browser:act"], "always_allow": []},
        },
    )
    forced = await tool.execute({"task": "send the report to the team"}, CTX)
    assert forced.success is False and forced.output["reason"] == "approval_required"
    assert len(await rt.approvals.pending()) == 2
    # Bypass under a lower inherited ceiling denies instead of escalating.
    await rt.roster.update(
        "scout",
        {"permission_ceiling": "monitor", "approval_rules": {"require_approval": []}},
    )
    denied = await tool.execute({"task": "send the report to the team"}, CTX)
    assert denied.success is False and denied.output["reason"] == "blocked_by_policy"
    assert len(await rt.approvals.pending()) == 2
    off = BrowserTool(rt, "scout", _jobs(tmp_path, fake_runner, installed=False))
    not_ready = await off.execute({"task": "read"}, CTX)
    assert not_ready.success is False and "install_action" in not_ready.output
    await rt.store.set_kill_switch(True)
    assert (await tool.execute({"task": "read"}, CTX)).output["reason"] == "kill_switch"


async def test_briefing_and_surface_reflect_the_browser(rt, tmp_path, fake_runner):
    from jarvis.society.surface import society_system_extra, society_tools

    session = SimpleNamespace(
        session_id="society:scout", cwd=str(tmp_path / "ws"), permission_mode="ask"
    )
    cfg = SimpleNamespace(
        wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
        memory=SimpleNamespace(data_dir=str(tmp_path)),
    )
    briefing = await society_system_extra(cfg, None, session)
    assert "## Your browser\nNot set up" in briefing
    assert "society_browser" not in society_tools(cfg, None, session)
    rt.browser = _jobs(tmp_path, fake_runner)
    briefing = await society_system_extra(cfg, None, session)
    assert "runs in your own persistent browser profile" in briefing
    assert "society_browser" in society_tools(cfg, None, session)

@pytest.mark.parametrize("task", [
    "Tell me the page title and summarize the article",
    "Change the title to Hello",
    "Read the title of the second tab",
    "Tell me the page title and then click Save",
    "Navigate to https://other.example and return the page title",
])
def test_title_shortcut_rejects_other_work(task):
    assert not _is_simple_page_title_request(task, "https://example.com")


def test_title_shortcut_supports_exact_browser_prompt():
    assert _is_simple_page_title_request(
        "Open Brave and go to https://example.com. Tell me the page title.",
        "https://example.com",
    )


@pytest.fixture
def title_worker(monkeypatch):
    # The standalone worker redirects stdout at import time.
    stdout = sys.stdout
    from jarvis.society.browser.live_runner import Worker
    monkeypatch.setattr(sys, "stdout", stdout)
    return Worker()


@pytest.mark.parametrize("manual,pending", [(True, False), (False, True)])
async def test_page_title_refuses_manual_or_pending_takeover(title_worker, manual, pending):
    worker = title_worker
    worker.manual = manual
    if pending:
        worker.agent_gate.clear()
    with pytest.raises(RuntimeError, match="Return browser control"):
        await worker.command("page_title", {"url": "https://example.com"})
    assert worker.step_idle.is_set()


@pytest.mark.parametrize("cancel", [False, True])
async def test_page_title_takeover_waits_for_navigation(title_worker, cancel):
    worker = title_worker
    entered = asyncio.Event()
    release = asyncio.Event()

    class Page:
        url = "https://example.com"

        async def goto(self, url, **kwargs):
            entered.set()
            await release.wait()
            assert not worker.manual

        async def title(self):
            assert not worker.manual
            return "Example Domain"

    async def focused():
        return Page()

    async def cursor(_enabled):
        pass

    worker.focused = focused
    worker.show_page_cursor = cursor
    worker.job = asyncio.create_task(worker.command("page_title", {"url": Page.url}))
    await asyncio.wait_for(entered.wait(), 1)
    takeover = asyncio.create_task(worker.command("takeover", {"enabled": True}))
    await asyncio.sleep(0)
    assert not worker.step_idle.is_set()
    assert not takeover.done()
    assert not worker.manual
    if cancel:
        worker.job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker.job
    else:
        release.set()
        assert (await worker.job)["title"] == "Example Domain"
    assert (await asyncio.wait_for(takeover, 1))["manual"]
    assert worker.step_idle.is_set()


@pytest.mark.parametrize("decision", [
    "approve", "deny", "stop", "stop_late_approve", "cancel",
    "kill", "inactive", "denied_grant", "allowlist", "block", "executor_deny",
    "browser_domains", "browser_mode",
])
async def test_title_approval_is_inside_owned_run(tmp_path, monkeypatch, decision):
    from jarvis.society.approvals import Verdict
    from jarvis.society.browser.bridge import execute_live
    from jarvis.society.browser.live import LiveSessions

    queued = asyncio.Event()
    events = []
    commands = []
    approval = SimpleNamespace(id="approval-1", state="pending")
    caller = SimpleNamespace(agent_id="scout", browser_mode="own", browser_allowed_domains=[],
                             state="active", denies=[], grants=[], grant_mode="all")
    session = SimpleNamespace(
        agent_id="scout", run_lock=asyncio.Lock(), control_owner="", active_trace="",
        active_chat="", closed=False, subscribers={"viewer"}, attention={},
    )

    def publish(event):
        events.append(event)
        if event["kind"] == "approval":
            session.attention["approval"] = event
            queued.set()
        elif event["kind"] == "approval_cleared":
            session.attention.pop("approval", None)

    async def command(op, args=None, **kwargs):
        commands.append(op)
        return {"title": "Example Domain", "url": "https://example.com"}

    session.publish = publish
    session.command = command

    class Approvals:
        async def enqueue(self, **kwargs):
            assert session.run_lock.locked()
            assert session.active_trace == str(CTX.trace_id)
            return approval

        async def get(self, _id):
            return approval

        async def resolve(self, _id, *, approve, note):
            approval.state = "approved" if approve else "denied"

    async def get_agent(_id):
        return caller

    async def kill_switch():
        return decision == "kill"

    async def ensure(_agent):
        assert decision not in {
            "kill", "inactive", "denied_grant", "allowlist", "block",
            "browser_domains", "browser_mode",
        }
        return session

    class Executor:
        async def execute(self, tool, args, **kwargs):
            assert session.run_lock.locked()
            assert tool.risk_tier == "monitor" and tool.is_action_tool
            if decision == "executor_deny":
                from jarvis.core.protocols import ToolResult
                return ToolResult(False, None, "Executor denied")
            return await tool.execute(args, CTX)

    if decision == "inactive":
        caller.state = "paused"
    elif decision == "denied_grant":
        caller.denies = ["core:browser"]
    elif decision == "allowlist":
        caller.grant_mode = "allowlist"
    elif decision == "browser_domains":
        caller.browser_allowed_domains = ["allowed.example"]
    elif decision == "browser_mode":
        caller.browser_mode = "shared"

    live = LiveSessions(tmp_path)
    live.ensure = ensure
    live.executor = Executor()
    live.sessions[caller.agent_id] = session
    runtime = SimpleNamespace(approvals=Approvals(), roster=SimpleNamespace(get=get_agent),
                              store=SimpleNamespace(kill_switch=kill_switch))
    verdict = Verdict.BLOCK if decision == "block" else Verdict.QUEUE
    if decision == "executor_deny":
        verdict = Verdict.RUN
    monkeypatch.setattr("jarvis.society.approvals.decide", lambda *a, **kw: verdict)
    task = asyncio.create_task(execute_live(runtime, caller, SimpleNamespace(live=live), {
        "task": "Open Brave and go to https://example.com. Tell me the page title.",
        "url": "https://example.com",
    }, CTX))
    if decision not in {"approve", "deny", "stop", "stop_late_approve", "cancel"}:
        result = await asyncio.wait_for(task, 1)
        assert not result.success
        assert commands == []
        assert not session.run_lock.locked()
        return
    await asyncio.wait_for(queued.wait(), 1)
    if decision == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert approval.state == "denied"
        assert not session.run_lock.locked()
        assert session.active_trace == session.active_chat == ""
        assert "approval" not in session.attention
        assert "page_title" not in commands
        return
    if decision.startswith("stop"):
        await live.cancel(session)
        assert (caller.agent_id, str(CTX.trace_id)) in live.stopped_turns
    if decision in {"approve", "stop_late_approve"}:
        approval.state = "approved"
    elif decision == "deny":
        approval.state = "denied"
    result = await asyncio.wait_for(task, 2)
    assert result.success is (decision == "approve")
    assert commands.count("page_title") == (1 if decision == "approve" else 0)
    assert approval.state != "pending"
    assert not session.run_lock.locked()
    assert session.active_trace == session.active_chat == ""
    assert "approval" not in session.attention
    assert any(event["kind"] == "approval_cleared" for event in events)


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("change,phase", [
    ("require_approval", "initial"),
    ("safe_ceiling", "initial"),
    ("require_approval", "acquisition"),
    ("require_approval", "executor"),
    *[(change, phase) for change in ("browser_mode", "browser_domains")
      for phase in ("acquisition", "executor")],
    *[(change, phase) for change in (
        "kill", "paused", "denied", "revoked", "safe_ceiling", "removed",
    ) for phase in ("acquisition", "approval", "executor")],
])
async def test_title_navigation_revalidates_policy(rt, monkeypatch, change, phase, read_only):
    from contextlib import asynccontextmanager

    from jarvis.society.browser.bridge import execute_live

    await rt.roster.update("scout", {
        "approval_mode": "bypass", "permission_ceiling": "monitor",
        "grant_mode": "allowlist", "grants": ["core:browser"],
        "approval_rules": {"require_approval": [], "always_allow": []},
    })
    commands = []
    approvals = []
    acquired = []
    killed = False
    removed = False
    original_get = rt.roster.get

    async def get_agent(agent_id):
        return None if removed else await original_get(agent_id)

    async def kill_switch():
        return killed

    monkeypatch.setattr(rt.roster, "get", get_agent)
    monkeypatch.setattr(rt.store, "kill_switch", kill_switch)

    async def mutate():
        nonlocal killed, removed
        if change == "kill":
            killed = True
        elif change == "removed":
            removed = True
        else:
            updates = {
                "paused": {"state": "paused"},
                "denied": {"denies": ["core:browser"]},
                "revoked": {"grants": []},
                "browser_mode": {"browser_mode": "attach"},
                "browser_domains": {"browser_allowed_domains": ["example.com"]},
                "safe_ceiling": {"permission_ceiling": "safe"},
                "require_approval": {
                    "approval_rules": {"require_approval": ["core:browser:navigate"]},
                },
            }
            await rt.roster.update("scout", updates[change])

    async def command(op, args, **kwargs):
        commands.append((op, args))
        return {"title": "Example Domain", "url": args["url"]}

    session = SimpleNamespace(command=command, publish=lambda event: None)

    @asynccontextmanager
    async def page_title_run(*args, **kwargs):
        acquired.append(True)
        if phase == "acquisition":
            await mutate()
        yield session

    async def approve(runtime, approval_id, session, **kwargs):
        row = await runtime.approvals.get(approval_id)
        assert row.capability == "core:browser"
        assert row.action == {
            "action": {"navigate": {"url": "https://example.com"}},
            "resume_in_place": True,
        }
        assert commands == []
        approvals.append(approval_id)
        await runtime.approvals.resolve(approval_id, approve=True)
        if phase == "approval":
            await mutate()
        return True

    class Executor:
        async def execute(self, tool, args, **kwargs):
            assert tool.risk_tier == ("safe" if read_only else "monitor")
            assert tool.is_action_tool is not read_only
            if phase == "executor":
                await mutate()
            return await tool.execute(args, CTX)

    monkeypatch.setattr("jarvis.society.browser.bridge.wait_for_browser_approval", approve)
    if phase == "initial":
        await mutate()
    elif phase == "approval":
        await rt.roster.update("scout", {
            "approval_rules": {"require_approval": ["core:browser:navigate"]},
        })
    caller = await rt.roster.get("scout")
    live = SimpleNamespace(
        executor=Executor(), stopped_turns=set(), page_title_run=page_title_run,
    )
    result = await execute_live(rt, caller, SimpleNamespace(live=live), {
        "task": "Open Brave and go to https://example.com. Tell me the page title.",
        "url": "https://example.com",
    }, CTX, read_only=read_only)

    # Require-approval precedes the inherited ceiling, just as in navigate.
    allowed = change == "require_approval" or (
        change == "safe_ceiling" and (read_only or phase == "approval")
    )
    assert result.success is allowed, result.error
    assert commands == ([("page_title", {"url": "https://example.com"})] if allowed else [])
    assert len(approvals) == (1 if change == "require_approval" or phase == "approval" else 0)
    if phase == "initial" and not allowed:
        assert acquired == []


@pytest.mark.parametrize("failure_type", [TimeoutError, RuntimeError])
@pytest.mark.parametrize("cancel_fails", [False, True])
async def test_title_command_failure_cancels_before_release(
    rt, tmp_path, monkeypatch, failure_type, cancel_fails,
):
    from jarvis.core.protocols import ToolResult
    from jarvis.society.approvals import Verdict
    from jarvis.society.browser.bridge import execute_live
    from jarvis.society.browser.live import LiveSessions

    caller = await rt.roster.get("scout")
    original_error = failure_type("Page-title worker failed")
    commands = []
    caught = []
    session = SimpleNamespace(
        agent_id="scout", run_lock=asyncio.Lock(), control_owner="", active_trace="",
        active_chat="", closed=False, subscribers={"viewer"},
    )

    async def command(op, args=None, *, timeout):
        assert session.run_lock.locked()
        assert session.active_trace == str(CTX.trace_id)
        commands.append((op, args, timeout))
        if op == "page_title":
            raise original_error
        assert op == "cancel"
        if cancel_fails:
            raise RuntimeError("Cancellation failed")
        return {}

    session.command = command

    async def ensure(agent):
        return session

    class Executor:
        async def execute(self, tool, args, **kwargs):
            # Match the executor's exception-to-failure conversion: the lifecycle
            # context cannot rely on receiving the worker's exception itself.
            try:
                return await tool.execute(args, CTX)
            except Exception as exc:
                caught.append(exc)
                assert [op for op, _, _ in commands] == ["page_title", "cancel"]
                return ToolResult(False, None, str(exc))

    live = LiveSessions(tmp_path)
    live.ensure = ensure
    live.executor = Executor()
    monkeypatch.setattr("jarvis.society.approvals.decide", lambda *a, **kw: Verdict.RUN)
    result = await execute_live(rt, caller, SimpleNamespace(live=live), {
        "task": "Open Brave and go to https://example.com. Tell me the page title.",
        "url": "https://example.com",
    }, CTX)

    assert not result.success
    assert result.error == str(original_error)
    assert caught == [original_error]
    assert commands == [
        ("page_title", {"url": "https://example.com"}, 30),
        ("cancel", None, 5),
    ]
    assert not session.run_lock.locked()
    assert session.active_trace == session.active_chat == ""
