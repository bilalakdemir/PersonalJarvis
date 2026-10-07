from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import jarvis.runtime_acceptance as runtime_acceptance
from jarvis.runtime_acceptance import (
    RuntimeAcceptanceResult,
    RuntimeAcceptanceStatus,
    _ExchangeResult,
    derive_acceptance_spec,
    run_runtime_acceptance,
)


class _FakeProcess:
    def __init__(self, pid: int = 1234) -> None:
        self.pid = pid

    def poll(self):
        return None


def _browser_rows() -> list[dict[str, object]]:
    return [
        {
            "activity_id": "tool:browser",
            "kind": "tool",
            "label": "dispatch_to_harness",
            "status": "failed",
            "request_detail": (
                "Open Brave and go to https://example.com. "
                "Tell me the page title."
            ),
        }
    ]


def _install_happy_controller(monkeypatch, *, exchange=None, attempts=None):
    stopped: list[int] = []
    monkeypatch.setattr(runtime_acceptance, "_free_loopback_port", lambda: 49123)
    monkeypatch.setattr(
        runtime_acceptance,
        "_attach_shared_browser_install",
        lambda *args, **kwargs: Path("shared-browser-link"),
    )
    monkeypatch.setattr(
        runtime_acceptance,
        "_detach_shared_browser_install",
        lambda target: None,
    )
    monkeypatch.setattr(
        runtime_acceptance,
        "_spawn_server",
        lambda *args, **kwargs: _FakeProcess(),
    )
    monkeypatch.setattr(
        runtime_acceptance,
        "_wait_for_http",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        runtime_acceptance,
        "_wait_for_health",
        lambda *args, **kwargs: True,
    )

    async def happy_exchange(*args, **kwargs):
        return exchange or _ExchangeResult(
            "The page title is Example Domain.",
            (
                {
                    "tool": "society_browser",
                    "success": True,
                    "error": "",
                },
            ),
            (),
        )

    monkeypatch.setattr(runtime_acceptance, "_exchange", happy_exchange)
    monkeypatch.setattr(
        runtime_acceptance,
        "_read_tool_attempts",
        lambda path: (
            attempts
            if attempts is not None
            else ({"tool": "society_browser", "allowed": True},)
        ),
    )
    monkeypatch.setattr(
        runtime_acceptance,
        "_stop_process_tree",
        lambda process: stopped.append(process.pid),
    )
    return stopped


def test_browser_page_title_profile_is_rebuilt_from_validated_url() -> None:
    spec = derive_acceptance_spec(_browser_rows())

    assert spec is not None
    assert spec.profile == "browser_page_title"
    assert spec.prompt == (
        "Open the isolated browser and go to https://example.com. "
        "Tell me the page title."
    )
    assert spec.required_tools == frozenset({"society_browser"})
    assert "computer_use" in spec.forbidden_tools
    assert "dispatch_to_harness" in spec.forbidden_tools


def test_profile_rejects_credentials_non_http_and_non_title_requests() -> None:
    rows = [
        {"request_detail": "Go to file:///etc/passwd and tell me the page title."},
        {
            "request_detail": (
                "Go to https://user:secret@example.com and tell me the page title."
            )
        },
        {"request_detail": "Open https://example.com and click Delete."},
    ]

    assert derive_acceptance_spec(rows) is None


def test_unsupported_incident_writes_honest_evidence_without_booting(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_acceptance,
        "_spawn_server",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unsupported acceptance must not boot a runtime")
        ),
    )

    result = run_runtime_acceptance(
        worktree=tmp_path / "worktree",
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident",
        rows=[{"request_detail": "Open Notepad"}],
    )

    assert result.status is RuntimeAcceptanceStatus.UNSUPPORTED
    payload = json.loads(
        (tmp_path / "incident" / "runtime_acceptance.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["result"]["status"] == "unsupported"
    assert "spec" not in payload


def test_pass_uses_fresh_thread_and_always_reaps_runtime(
    tmp_path: Path, monkeypatch,
) -> None:
    stopped = _install_happy_controller(monkeypatch)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    first = run_runtime_acceptance(
        worktree=worktree,
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident-a",
        rows=_browser_rows(),
    )
    second = run_runtime_acceptance(
        worktree=worktree,
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident-b",
        rows=_browser_rows(),
    )

    assert first.status is RuntimeAcceptanceStatus.PASS
    assert second.status is RuntimeAcceptanceStatus.PASS
    assert first.thread_id.startswith("runtime-acceptance-")
    assert second.thread_id.startswith("runtime-acceptance-")
    assert first.thread_id != second.thread_id
    assert stopped == [1234, 1234]
    assert not list((tmp_path / "incident-a").glob(".runtime-acceptance-*"))


def test_forbidden_tool_attempt_fails_even_when_browser_answer_succeeds(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_happy_controller(
        monkeypatch,
        attempts=(
            {"tool": "society_browser", "allowed": True},
            {"tool": "computer_use", "allowed": False},
        ),
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_runtime_acceptance(
        worktree=worktree,
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident",
        rows=_browser_rows(),
    )

    assert result.status is RuntimeAcceptanceStatus.FAIL
    assert result.reason == "forbidden_tool_attempt"


def test_failed_browser_action_is_a_runtime_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_happy_controller(
        monkeypatch,
        exchange=_ExchangeResult(
            "",
            (
                {
                    "tool": "society_browser",
                    "success": False,
                    "error": "navigation failed",
                },
            ),
            (),
        ),
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_runtime_acceptance(
        worktree=worktree,
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident",
        rows=_browser_rows(),
    )

    assert result.status is RuntimeAcceptanceStatus.FAIL
    assert result.reason == "browser_action_failed"


def test_provider_capacity_failure_is_transient_not_regression(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_happy_controller(
        monkeypatch,
        exchange=_ExchangeResult(
            "",
            (
                {
                    "tool": "society_browser",
                    "success": False,
                    "error": "Service temporarily overloaded",
                },
            ),
            (),
        ),
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_runtime_acceptance(
        worktree=worktree,
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident",
        rows=_browser_rows(),
    )

    assert result.status is RuntimeAcceptanceStatus.TRANSIENT
    assert result.reason == "browser_action_transient"


def test_required_browser_tool_missing_blocks_acceptance(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_happy_controller(
        monkeypatch,
        exchange=_ExchangeResult("Example Domain", (), ()),
        attempts=(),
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_runtime_acceptance(
        worktree=worktree,
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident",
        rows=_browser_rows(),
    )

    assert result.status is RuntimeAcceptanceStatus.FAIL
    assert result.reason == "required_tool_missing"


def test_turn_exception_still_reaps_process_and_records_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    stopped = _install_happy_controller(monkeypatch)

    async def boom(*args, **kwargs):
        raise RuntimeError("websocket broke")

    monkeypatch.setattr(runtime_acceptance, "_exchange", boom)
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_runtime_acceptance(
        worktree=worktree,
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident",
        rows=_browser_rows(),
    )

    assert result.status is RuntimeAcceptanceStatus.FAIL
    assert result.reason == "runtime_turn_failed"
    assert stopped == [1234]


def test_server_environment_isolated_from_main_data_and_config(
    tmp_path: Path, monkeypatch,
) -> None:
    worktree = tmp_path / "patched"
    worktree.mkdir()
    run_dir = tmp_path / "incident" / ".run"
    run_dir.mkdir(parents=True)
    config = run_dir / "acceptance.toml"
    config.write_text("", encoding="utf-8")
    spec = derive_acceptance_spec(_browser_rows())
    assert spec is not None
    monkeypatch.setenv("PYTHONPATH", "existing-path")

    env = runtime_acceptance._server_env(worktree, run_dir, config, spec)

    assert env["JARVIS_DATA_DIR"] == str(run_dir / "data")
    assert env["JARVIS_CONFIG"] == str(config)
    assert env["JARVIS_INSTANCE"] == "dev"
    assert env["JARVIS__BRAIN__HEALTHCHECK_ON_START"] == "false"
    assert env["JARVIS__ACK_BRAIN__ENABLED"] == "false"
    assert env["JARVIS__MEMORY__WIKI__ENABLED"] == "false"
    assert env["JARVIS__TTS__PROVIDER"] == "none"
    assert env["PYTHONPATH"].split(runtime_acceptance.os.pathsep)[0] == str(
        worktree
    )


def test_tool_tripwire_blocks_before_forbidden_executor_runs(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[str] = []

    class FakeExecutor:
        async def execute(self, tool, args, **kwargs):
            calls.append(tool.name)
            return "executed"

    import jarvis.safety.tool_executor as tool_executor_module

    monkeypatch.setattr(tool_executor_module, "ToolExecutor", FakeExecutor)
    monkeypatch.setenv(
        runtime_acceptance._ACCEPTANCE_ALLOWED_TOOLS_ENV,
        json.dumps(["society_browser"]),
    )
    log_path = tmp_path / "tools.jsonl"
    monkeypatch.setenv(
        runtime_acceptance._ACCEPTANCE_TOOL_LOG_ENV,
        str(log_path),
    )

    runtime_acceptance._install_tool_tripwire()
    executor = FakeExecutor()

    assert (
        asyncio.run(
            executor.execute(
                SimpleNamespace(name="society_browser"),
                {"url": "https://example.com"},
            )
        )
        == "executed"
    )
    try:
        asyncio.run(
            executor.execute(
                SimpleNamespace(name="computer_use"),
                {"task": "open desktop"},
            )
        )
    except RuntimeError as exc:
        assert "RUNTIME_ACCEPTANCE_TRIPWIRE:computer_use" in str(exc)
    else:
        raise AssertionError("forbidden tool must be blocked before execution")

    assert calls == ["society_browser"]
    attempts = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert attempts == [
        {"tool": "society_browser", "allowed": True},
        {"tool": "computer_use", "allowed": False},
    ]


def test_result_to_dict_serializes_enum() -> None:
    result = RuntimeAcceptanceResult(
        RuntimeAcceptanceStatus.PASS,
        "ok",
        "answer",
        (),
        "thread",
    )

    assert result.to_dict()["status"] == "pass"


def test_missing_compatible_browser_runtime_is_unsupported_not_patch_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        runtime_acceptance,
        "_attach_shared_browser_install",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runtime_acceptance,
        "_spawn_server",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("missing browser cache must not trigger an install")
        ),
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_runtime_acceptance(
        worktree=worktree,
        repo=tmp_path / "repo",
        incident_dir=tmp_path / "incident",
        rows=_browser_rows(),
    )

    assert result.status is RuntimeAcceptanceStatus.UNSUPPORTED
    assert result.reason == "compatible_browser_runtime_unavailable"


def test_temp_cleanup_retries_transient_windows_style_lock(
    tmp_path: Path, monkeypatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls: list[Path] = []
    sleeps: list[float] = []

    def flaky_rmtree(path: Path):
        calls.append(Path(path))
        if len(calls) < 3:
            raise OSError("file is briefly locked")
        Path(path).rmdir()

    monkeypatch.setattr(runtime_acceptance.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(runtime_acceptance.time, "sleep", sleeps.append)

    assert runtime_acceptance._remove_run_dir(run_dir) is True

    assert len(calls) == 3
    assert sleeps == [0.1, 0.3]
    assert not run_dir.exists()


def test_untrusted_localhost_url_is_never_replayed() -> None:
    spec = derive_acceptance_spec(
        [
            {
                "request_detail": (
                    "Open http://127.0.0.1:5000/internal_admin and tell me "
                    "the page title."
                )
            }
        ]
    )

    assert spec is not None
    assert "127.0.0.1" not in spec.prompt
    assert "internal_admin" not in spec.prompt
    assert "https://example.com" in spec.prompt


def test_prepare_config_only_copies_safe_brain_selection(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    repo.mkdir()
    run_dir.mkdir()
    (repo / "jarvis.toml").write_text(
        """
[brain]
primary = "codex"
routing_provider = "codex"
local_fallback = "codex"
routing_model = "gpt-test"
reply_language = "auto"

[brain.tool_model]
provider = "codex"
model = "gpt-tool"
unexpected = "drop-me"

[trigger]
enabled = true

[ui]
admin_api_port = 44444
start_minimized = true

[autostart]
enabled = true
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config_path = runtime_acceptance._prepare_config(repo, run_dir, port=49123)
    import tomllib

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))

    assert set(parsed) == {"brain", "ack_brain", "tts", "ui"}
    assert parsed["brain"]["primary"] == "codex"
    assert parsed["brain"]["routing_provider"] == "codex"
    assert parsed["brain"]["local_fallback"] == "codex"
    assert parsed["brain"]["routing_model"] == "gpt-test"
    assert parsed["brain"]["tool_model"] == {
        "provider": "codex",
        "model": "gpt-tool",
    }
    assert parsed["ui"] == {"admin_api_port": 49123}
    assert parsed["ack_brain"] == {"enabled": False}
    assert parsed["tts"] == {"provider": "none"}


def test_exchange_ignores_assistant_message_from_other_thread(
    monkeypatch,
) -> None:
    import sys

    frames = [
        json.dumps({"type": "welcome"}),
        json.dumps(
            {
                "event_name": "MessageSent",
                "payload": {
                    "thread_id": "other-thread",
                    "role": "assistant",
                    "text": "wrong answer",
                },
            }
        ),
        json.dumps(
            {
                "event_name": "ActionExecuted",
                "payload": {
                    "tool_name": "society_browser",
                    "success": True,
                    "error": "",
                },
            }
        ),
        json.dumps(
            {
                "event_name": "MessageSent",
                "payload": {
                    "thread_id": "wanted-thread",
                    "role": "assistant",
                    "text": "right answer",
                },
            }
        ),
    ]

    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def recv(self) -> str:
            return frames.pop(0)

        async def send(self, value: str) -> None:
            self.sent.append(value)

    socket = FakeSocket()

    class FakeConnection:
        async def __aenter__(self):
            return socket

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_websockets = SimpleNamespace(
        connect=lambda *args, **kwargs: FakeConnection()
    )
    monkeypatch.setitem(sys.modules, "websockets", fake_websockets)

    result = asyncio.run(
        runtime_acceptance._exchange(
            "ws://127.0.0.1:49123/ws",
            prompt="test",
            thread_id="wanted-thread",
            timeout_s=2.0,
        )
    )

    assert result.final_text == "right answer"
    assert result.actions == (
        {"tool": "society_browser", "success": True, "error": ""},
    )


def test_cleanup_failure_overrides_nominal_pass_and_fails_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_happy_controller(monkeypatch)
    monkeypatch.setattr(runtime_acceptance, "_remove_run_dir", lambda path: False)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    incident = tmp_path / "incident"

    try:
        run_runtime_acceptance(
            worktree=worktree,
            repo=tmp_path / "repo",
            incident_dir=incident,
            rows=_browser_rows(),
        )
    except RuntimeError as exc:
        assert "temporary state could not be removed" in str(exc)
    else:
        raise AssertionError("cleanup failure must fail closed")

    evidence = json.loads(
        (incident / "runtime_acceptance.json").read_text(encoding="utf-8")
    )
    assert evidence["result"]["status"] == "fail"
    assert evidence["result"]["reason"] == "runtime_cleanup_failed"


def test_page_title_intent_never_replays_untrusted_local_url() -> None:
    spec = derive_acceptance_spec(
        [
            {
                "request_detail": (
                    "Open http://127.0.0.1:9999/private-admin and tell me the page title."
                )
            }
        ]
    )

    assert spec is not None
    assert "127.0.0.1" not in spec.prompt
    assert spec.prompt == (
        "Open the isolated browser and go to https://example.com. "
        "Tell me the page title."
    )


def test_prepare_config_is_minimal_and_does_not_copy_user_config(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "jarvis.toml").write_text(
        "\n".join(
            [
                "[brain]",
                'primary = "nvidia"',
                "",
                "[ui]",
                'dangerous_custom_path = "C:/private/user/path"',
                "",
                "[trigger]",
                'word = "secret-trigger"',
            ]
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    config = runtime_acceptance._prepare_config(repo, run_dir, port=49991)
    text = config.read_text(encoding="utf-8")

    assert 'primary = "nvidia"' in text
    assert "admin_api_port = 49991" in text
    assert "dangerous_custom_path" not in text
    assert "secret-trigger" not in text


def test_cleanup_failure_turns_nominal_pass_into_runner_error(
    tmp_path: Path, monkeypatch,
) -> None:
    _install_happy_controller(monkeypatch)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    monkeypatch.setattr(runtime_acceptance, "_remove_run_dir", lambda path: False)

    try:
        run_runtime_acceptance(
            worktree=worktree,
            repo=tmp_path / "repo",
            incident_dir=tmp_path / "incident",
            rows=_browser_rows(),
        )
    except RuntimeError as exc:
        assert "temporary state could not be removed" in str(exc)
    else:
        raise AssertionError("cleanup failure must fail closed")

    payload = json.loads(
        (tmp_path / "incident" / "runtime_acceptance.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["result"]["status"] == "fail"
    assert payload["result"]["reason"] == "runtime_cleanup_failed"


def test_wait_for_health_requires_expected_aerion_document(
    monkeypatch,
) -> None:
    class FakeResponse:
        status = 200

        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return json.dumps(self._payload).encode()

    process = _FakeProcess()
    payloads = iter(
        [
            {"ok": True, "instance": "default"},
            {"ok": True, "instance": "dev"},
        ]
    )
    monkeypatch.setattr(
        runtime_acceptance.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(next(payloads)),
    )
    clock = iter([0.0, 0.0, 0.1, 0.2])
    monkeypatch.setattr(runtime_acceptance.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runtime_acceptance.time, "sleep", lambda _: None)

    assert runtime_acceptance._wait_for_health(
        "http://127.0.0.1:49991/api/health",
        process,
        timeout_s=1.0,
    )
