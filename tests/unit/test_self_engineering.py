from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jarvis.self_engineering as self_engineering
from jarvis.self_engineering import _port, _save, _state, failed, redact, risk, verdict


def test_failed_only_returns_terminal_failures() -> None:
    snapshot = {
        "recent_outputs": [
            {"activity_id": "a", "status": "completed"},
            {"activity_id": "b", "status": "failed"},
            {"activity_id": "", "status": "failed"},
        ]
    }
    assert [row["activity_id"] for row in failed(snapshot)] == ["b"]


def test_redact_masks_subscription_provider_secrets() -> None:
    assert redact("token nvapi-1234567890SECRET") == "token [REDACTED]"
    bearer = "x" * 20
    assert bearer not in redact("Authorization: Bearer " + bearer)


def test_risk_classifier_fails_closed() -> None:
    assert risk(["jarvis/harness/foo.py", "tests/unit/test_foo.py"]) == "LOW"
    assert risk(["jarvis/core/events.py", "tests/unit/test_events.py"]) == "MEDIUM"
    assert risk(["jarvis/safety/tool_executor.py"]) == "HIGH"
    assert risk(["DECISIONS.md"]) == "HIGH"


def test_verifier_cannot_downgrade_risk_floor() -> None:
    assert verdict("VERDICT: PASS\nRISK: LOW", "MEDIUM") == ("PASS", "MEDIUM")
    assert verdict("missing fields", "LOW") == ("NEEDS_HUMAN", "LOW")


def test_dev_port_reads_config_without_writing_it(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "jarvis.toml"
    config.write_text("[ui]\nadmin_api_port = 49000\n", encoding="utf-8")
    before = config.read_bytes()
    monkeypatch.delenv("JARVIS__UI__ADMIN_API_PORT", raising=False)
    assert _port(tmp_path) == 49100
    assert config.read_bytes() == before


def test_state_round_trip_is_atomic_shape(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    value = {"bootstrapped": True, "seen": ["a", "b"]}
    _save(path, value)
    assert _state(path) == value
    assert json.loads(path.read_text(encoding="utf-8")) == value


def test_missing_pytest_defers_local_tests_to_ci(
    tmp_path: Path, monkeypatch,
) -> None:
    def fake_run(cmd, cwd, *, stdin=None, timeout=300, env=None):
        if cmd[1:3] == ["-m", "pytest"]:
            return subprocess.CompletedProcess(
                cmd, 1, "", f"{cmd[0]}: No module named pytest\n"
            )
        if "scripts/ci/run_gates.py" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "gates pass\n", "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(self_engineering, "_run", fake_run)

    ok, selected, output, local_validation = self_engineering._tests(
        tmp_path, ["jarvis/plugins/tool/run_shell.py"]
    )

    assert ok is True
    assert selected
    assert local_validation == "unavailable"
    assert "LOCAL_TESTS_UNAVAILABLE" in output


def test_push_branch_uses_primary_repo(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_git(repo: Path, *args: str):
        calls.append((repo, args))
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    monkeypatch.setattr(self_engineering, "_git", fake_git)

    result = self_engineering._push_branch(tmp_path, "agent/aerion-test")

    assert result.returncode == 0
    assert calls == [
        (
            tmp_path,
            (
                "push",
                "origin",
                "refs/heads/agent/aerion-test:refs/heads/agent/aerion-test",
            ),
        )
    ]



def test_related_failures_groups_dispatch_and_harness_without_merging_unrelated() -> None:
    base = 1_000_000_000_000
    dispatch = {
        "activity_id": "tool:dispatch",
        "kind": "tool",
        "label": "dispatch_to_harness",
        "status": "failed",
        "trace_id": "trace-dispatch",
        "detail": "exit 1",
        "updated_at_ns": base,
    }
    screenshot = {
        "activity_id": "harness:screenshot",
        "kind": "harness",
        "label": "screenshot",
        "status": "failed",
        "trace_id": "trace-shot",
        "detail": "worker saturation",
        "updated_at_ns": base + 2_000_000_000,
    }
    unrelated = {
        "activity_id": "tool:unrelated",
        "kind": "tool",
        "label": "run_shell",
        "status": "failed",
        "trace_id": "trace-other",
        "detail": "different failure",
        "updated_at_ns": base + 3_000_000_000,
    }

    grouped = self_engineering.related_failures(
        screenshot, [unrelated, screenshot, dispatch]
    )

    assert [row["activity_id"] for row in grouped] == [
        "tool:dispatch",
        "harness:screenshot",
    ]


def test_engineer_prompt_treats_provider_failure_as_possible_downstream_symptom() -> None:
    prompt = self_engineering._engineer_prompt(
        '[{"label":"dispatch_to_harness"},{"label":"screenshot","detail":"16/16"}]',
        "provider saturated",
    )

    assert "downstream symptom" in prompt
    assert "upstream routing" in prompt
    assert "why that harness was selected" in prompt


def test_no_change_rejection_gets_one_bounded_recheck(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []
    changed_results = iter([
        [],
        ["jarvis/harness/routing.py", "tests/unit/test_routing_fix.py"],
    ])

    def completed(text: str, code: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["codex"], code, text, "")

    def fake_codex(worktree: Path, prompt: str, sandbox: str):
        calls.append((sandbox, prompt))
        if len(calls) == 1:
            return completed("OUTCOME: NO_CODE_CHANGE")
        if len(calls) == 2:
            return completed(
                "VERDICT: FAIL\nRISK: LOW\nREASON: downstream symptom not root cause"
            )
        if len(calls) == 3:
            return completed("OUTCOME: FIXED")
        return completed("VERDICT: PASS\nRISK: LOW\nREASON: bounded fix matches root cause")

    monkeypatch.setattr(self_engineering, "_codex", fake_codex)
    monkeypatch.setattr(
        self_engineering,
        "_changed",
        lambda worktree: next(changed_results),
    )
    monkeypatch.setattr(
        self_engineering,
        "_tests",
        lambda worktree, changed: (True, tuple(changed), "ok", "passed"),
    )
    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(["git", *args], 0, "", ""),
    )
    monkeypatch.setattr(
        self_engineering, "_push_branch",
        lambda repo, branch: subprocess.CompletedProcess(["git", "push"], 0, "", ""),
    )
    monkeypatch.setattr(self_engineering.shutil, "which", lambda name: None)
    monkeypatch.setattr(self_engineering.time, "time", lambda: 1234)

    data = tmp_path / "data"
    result = self_engineering._incident(
        tmp_path / "repo",
        data,
        [
            {
                "activity_id": "tool:dispatch",
                "kind": "tool",
                "label": "dispatch_to_harness",
                "status": "failed",
                "updated_at_ns": 100,
            },
            {
                "activity_id": "harness:screenshot",
                "kind": "harness",
                "label": "screenshot",
                "status": "failed",
                "detail": "worker saturation",
                "updated_at_ns": 101,
            },
        ],
        False,
    )

    assert result["status"] == "branch_pushed"
    assert len(calls) == 4
    assert calls[0][0] == "workspace-write"
    assert calls[1][0] == "read-only"
    assert calls[2][0] == "workspace-write"
    assert calls[3][0] == "read-only"
    assert "prior pass made no code changes" in calls[2][1]



def test_active_operation_context_survives_terminal_failure() -> None:
    cache: dict[str, dict] = {}
    self_engineering._capture_active_context(
        {
            "active_operations": [
                {
                    "activity_id": "tool:dispatch",
                    "kind": "tool",
                    "label": "dispatch_to_harness",
                    "detail": "Open Brave and go to https://example.com",
                    "trace_id": "trace-dispatch",
                }
            ]
        },
        cache,
    )

    enriched = self_engineering._enrich_failures(
        [
            {
                "activity_id": "tool:dispatch",
                "kind": "tool",
                "label": "dispatch_to_harness",
                "status": "failed",
                "detail": "exit 1",
                "trace_id": "trace-dispatch",
            }
        ],
        cache,
    )

    assert enriched[0]["request_detail"] == "Open Brave and go to https://example.com"
    context = self_engineering._incident_context(enriched)
    assert "request_detail" in context
    assert "Open Brave and go to https://example.com" in context



def test_failed_harness_groups_with_running_dispatch_context() -> None:
    base = 5_000_000_000
    screenshot = {
        "activity_id": "harness:shot",
        "kind": "harness",
        "label": "screenshot",
        "status": "failed",
        "updated_at_ns": base + 1_000_000_000,
    }
    dispatch = {
        "activity_id": "tool:dispatch",
        "kind": "tool",
        "label": "dispatch_to_harness",
        "status": "running",
        "detail": "Open Brave and go to https://example.com",
        "updated_at_ns": base,
    }

    grouped = self_engineering.related_failures(screenshot, [screenshot, dispatch])

    assert [row["activity_id"] for row in grouped] == [
        "tool:dispatch",
        "harness:shot",
    ]



def test_terminal_request_context_reaches_incident_without_poll_cache() -> None:
    row = {
        "activity_id": "tool:trace:dispatch_to_harness",
        "kind": "tool",
        "label": "dispatch_to_harness",
        "status": "failed",
        "trace_id": "trace",
        "request_detail": (
            "{'harness': 'screenshot', 'prompt': "
            "'Open Brave and go to https://example.com'}"
        ),
        "rationale": "desktop path was selected",
        "detail": "exit 3",
    }

    enriched = self_engineering._enrich_failures([row], {})
    context = self_engineering._incident_context(enriched)

    assert "Open Brave" in context
    assert "https://example.com" in context
    assert "desktop path was selected" in context
    assert "exit 3" in context



def test_verifier_rejected_patch_gets_one_bounded_rework(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []
    changed = [
        "jarvis/plugins/tool/open_app.py",
        "tests/unit/plugins/tool/test_open_app.py",
    ]
    fingerprints = iter(["before", "after"])

    def completed(text: str, code: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["codex"], code, text, "")

    def fake_codex(worktree: Path, prompt: str, sandbox: str):
        calls.append((sandbox, prompt))
        if len(calls) == 1:
            return completed("OUTCOME: FIXED")
        if len(calls) == 2:
            return completed(
                "VERDICT: FAIL\nRISK: LOW\n"
                "REASON: alias fix breaks Start Menu-only installs"
            )
        if len(calls) == 3:
            return completed("OUTCOME: FIXED")
        return completed(
            "VERDICT: PASS\nRISK: LOW\nREASON: regression fixed and covered"
        )

    monkeypatch.setattr(self_engineering, "_codex", fake_codex)
    monkeypatch.setattr(self_engineering, "_changed", lambda worktree: list(changed))
    monkeypatch.setattr(
        self_engineering,
        "_change_fingerprint",
        lambda worktree, paths: next(fingerprints),
    )
    monkeypatch.setattr(
        self_engineering,
        "_tests",
        lambda worktree, paths: (True, tuple(paths), "ok", "passed"),
    )
    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(["git", *args], 0, "", ""),
    )
    monkeypatch.setattr(
        self_engineering,
        "_push_branch",
        lambda repo, branch: subprocess.CompletedProcess(["git", "push"], 0, "", ""),
    )
    monkeypatch.setattr(self_engineering.shutil, "which", lambda name: None)
    monkeypatch.setattr(self_engineering.time, "time", lambda: 4321)

    result = self_engineering._incident(
        tmp_path / "repo",
        tmp_path / "data",
        [
            {
                "activity_id": "tool:open",
                "kind": "tool",
                "label": "open_app",
                "status": "failed",
                "request_detail": "{'app_name': 'Brave Browser'}",
                "detail": "not found",
                "updated_at_ns": 100,
            }
        ],
        False,
    )

    assert result["status"] == "branch_pushed"
    assert [sandbox for sandbox, _ in calls] == [
        "workspace-write",
        "read-only",
        "workspace-write",
        "read-only",
    ]
    assert "rejected the current patch" in calls[2][1]
    assert "Start Menu-only installs" in calls[2][1]


def test_change_fingerprint_detects_same_path_revision(tmp_path: Path) -> None:
    path = tmp_path / "jarvis" / "plugins" / "tool" / "open_app.py"
    path.parent.mkdir(parents=True)
    path.write_text("before", encoding="utf-8")
    before = self_engineering._change_fingerprint(
        tmp_path, ["jarvis/plugins/tool/open_app.py"]
    )

    path.write_text("after", encoding="utf-8")
    after = self_engineering._change_fingerprint(
        tmp_path, ["jarvis/plugins/tool/open_app.py"]
    )

    assert before != after


def test_transient_agent_failure_classification_is_narrow() -> None:
    assert self_engineering._transient_agent_failure(
        "You've hit your usage limit. Try again later."
    )
    assert self_engineering._transient_agent_failure(
        "ResourceExhausted: Worker local total request limit reached (16/16)"
    )
    assert not self_engineering._transient_agent_failure(
        "Codex ChatGPT login is not connected"
    )
    assert not self_engineering._transient_agent_failure(
        "401 Unauthorized: authentication failed"
    )


def test_retry_later_is_not_seen_and_uses_bounded_backoff() -> None:
    state = {"bootstrapped": True, "seen": []}
    result = {
        "status": "retry_later",
        "reason": "transient_worker_failure",
        "activities": ["tool:a", "tool:b"],
    }
    rows = [
        {
            "activity_id": "tool:a",
            "kind": "tool",
            "label": "open_app",
            "status": "failed",
            "detail": "quota",
        },
        {
            "activity_id": "tool:b",
            "kind": "tool",
            "label": "open_app",
            "status": "failed",
            "detail": "quota",
        },
    ]

    terminal = self_engineering._record_incident_result(
        state,
        result,
        ["tool:a", "tool:b"],
        now=1_000.0,
        retry_rows=rows,
    )

    assert terminal is False
    assert state["seen"] == []
    entry = state["retries"]["tool:a"]
    assert entry["attempt"] == 1
    assert entry["next_retry_at"] == 1_060.0
    assert entry["owner"] == "tool:a"
    assert entry["rows"] == rows
    assert not self_engineering._retry_ready(state, "tool:a", 1_059.0)
    assert self_engineering._retry_ready(state, "tool:a", 1_060.0)
    assert self_engineering._due_retry_groups(state, 1_059.0) == []
    assert self_engineering._due_retry_groups(state, 1_060.0) == [
        (rows, ["tool:a", "tool:b"])
    ]

    self_engineering._record_incident_result(
        state,
        result,
        ["tool:a", "tool:b"],
        now=1_060.0,
        retry_rows=rows,
    )
    entry = state["retries"]["tool:a"]
    assert entry["attempt"] == 2
    assert entry["next_retry_at"] == 1_360.0

    self_engineering._record_incident_result(
        state,
        result,
        ["tool:a", "tool:b"],
        now=1_360.0,
        retry_rows=rows,
    )
    assert state["retries"]["tool:a"]["attempt"] == 3
    assert state["retries"]["tool:a"]["next_retry_at"] == 3_160.0

    self_engineering._record_incident_result(
        state,
        result,
        ["tool:a", "tool:b"],
        now=3_160.0,
        retry_rows=rows,
    )
    assert state["retries"]["tool:a"]["attempt"] == 4
    assert state["retries"]["tool:a"]["next_retry_at"] == 4_960.0

    terminal = self_engineering._record_incident_result(
        state,
        {"status": "worker_failed", "activities": ["tool:a", "tool:b"]},
        ["tool:a", "tool:b"],
        now=4_960.0,
    )
    assert terminal is True
    assert state["seen"] == ["tool:a", "tool:b"]
    assert "retries" not in state

def test_transient_worker_failure_without_patch_retries_and_discards_worktree(
    tmp_path: Path, monkeypatch,
) -> None:
    discarded: list[tuple[Path, Path, str]] = []

    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(["git", *args], 0, "", ""),
    )
    monkeypatch.setattr(
        self_engineering,
        "_codex",
        lambda worktree, prompt, sandbox: subprocess.CompletedProcess(
            ["codex"], 1, "You've hit your usage limit. Try again later.", ""
        ),
    )
    monkeypatch.setattr(self_engineering, "_changed", lambda worktree: [])
    monkeypatch.setattr(
        self_engineering,
        "_discard_unmodified_worktree",
        lambda repo, worktree, branch: discarded.append((repo, worktree, branch)) or True,
    )
    monkeypatch.setattr(self_engineering.time, "time", lambda: 4_444)

    result = self_engineering._incident(
        tmp_path / "repo",
        tmp_path / "data",
        [
            {
                "activity_id": "tool:quota",
                "kind": "tool",
                "label": "open_app",
                "status": "failed",
                "detail": "quota",
                "updated_at_ns": 100,
            }
        ],
        False,
    )

    assert result["status"] == "retry_later"
    assert result["reason"] == "transient_worker_failure"
    assert result["activities"] == ["tool:quota"]
    assert len(discarded) == 1


def test_two_verifier_rejections_can_rework_then_pass(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []
    changed = [
        "jarvis/plugins/tool/open_app.py",
        "tests/unit/plugins/tool/test_open_app.py",
    ]
    fingerprints = iter(["before-1", "after-1", "before-2", "after-2"])

    def completed(text: str, code: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["agent"], code, text, "")

    def fake_codex(worktree: Path, prompt: str, sandbox: str):
        calls.append((sandbox, prompt))
        responses = (
            "OUTCOME: FIXED",
            "VERDICT: FAIL\nRISK: LOW\nREASON: first gap",
            "OUTCOME: FIXED",
            "VERDICT: FAIL\nRISK: LOW\nREASON: second gap",
            "OUTCOME: FIXED",
            "VERDICT: PASS\nRISK: LOW\nREASON: complete",
        )
        return completed(responses[len(calls) - 1])

    monkeypatch.setattr(self_engineering, "_codex", fake_codex)
    monkeypatch.setattr(self_engineering, "_changed", lambda worktree: list(changed))
    monkeypatch.setattr(
        self_engineering,
        "_change_fingerprint",
        lambda worktree, paths: next(fingerprints),
    )
    monkeypatch.setattr(
        self_engineering,
        "_tests",
        lambda worktree, paths: (True, tuple(paths), "ok", "passed"),
    )
    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(["git", *args], 0, "", ""),
    )
    monkeypatch.setattr(
        self_engineering,
        "_push_branch",
        lambda repo, branch: subprocess.CompletedProcess(["git", "push"], 0, "", ""),
    )
    monkeypatch.setattr(self_engineering.shutil, "which", lambda name: None)
    monkeypatch.setattr(self_engineering.time, "time", lambda: 5_555)

    result = self_engineering._incident(
        tmp_path / "repo",
        tmp_path / "data",
        [
            {
                "activity_id": "tool:open",
                "kind": "tool",
                "label": "open_app",
                "status": "failed",
                "detail": "not found",
                "updated_at_ns": 100,
            }
        ],
        False,
    )

    assert result["status"] == "branch_pushed"
    assert [sandbox for sandbox, _ in calls] == [
        "workspace-write",
        "read-only",
        "workspace-write",
        "read-only",
        "workspace-write",
        "read-only",
    ]
    incident_dir = next((tmp_path / "data" / "engineering" / "incidents").iterdir())
    assert (incident_dir / "worker_rework_1.txt").exists()
    assert (incident_dir / "verifier_rework_1.txt").exists()
    assert (incident_dir / "worker_rework_2.txt").exists()
    assert (incident_dir / "verifier_rework_2.txt").exists()


def test_rework_loop_stops_at_configured_maximum(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []
    changed = [
        "jarvis/plugins/tool/open_app.py",
        "tests/unit/plugins/tool/test_open_app.py",
    ]
    fingerprints = iter(
        [
            "before-1", "after-1",
            "before-2", "after-2",
            "before-3", "after-3",
        ]
    )

    def fake_codex(worktree: Path, prompt: str, sandbox: str):
        calls.append((sandbox, prompt))
        if len(calls) % 2:
            text = "OUTCOME: FIXED"
        else:
            text = "VERDICT: FAIL\nRISK: LOW\nREASON: still incomplete"
        return subprocess.CompletedProcess(["agent"], 0, text, "")

    monkeypatch.setattr(self_engineering, "_codex", fake_codex)
    monkeypatch.setattr(self_engineering, "_changed", lambda worktree: list(changed))
    monkeypatch.setattr(
        self_engineering,
        "_change_fingerprint",
        lambda worktree, paths: next(fingerprints),
    )
    monkeypatch.setattr(
        self_engineering,
        "_tests",
        lambda worktree, paths: (True, tuple(paths), "ok", "passed"),
    )
    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(["git", *args], 0, "", ""),
    )
    monkeypatch.setattr(self_engineering.time, "time", lambda: 6_666)

    result = self_engineering._incident(
        tmp_path / "repo",
        tmp_path / "data",
        [
            {
                "activity_id": "tool:open",
                "kind": "tool",
                "label": "open_app",
                "status": "failed",
                "detail": "not found",
                "updated_at_ns": 100,
            }
        ],
        False,
    )

    assert result["status"] == "needs_human"
    assert result["reason"] == "max_rework_rounds_exhausted"
    assert len(calls) == 2 + 2 * self_engineering.MAX_REWORK_ROUNDS


def test_transient_verifier_failure_with_patch_is_preserved_for_human(
    tmp_path: Path, monkeypatch,
) -> None:
    calls = 0
    changed = [
        "jarvis/plugins/tool/open_app.py",
        "tests/unit/plugins/tool/test_open_app.py",
    ]

    def fake_codex(worktree: Path, prompt: str, sandbox: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(["agent"], 0, "OUTCOME: FIXED", "")
        return subprocess.CompletedProcess(
            ["agent"], 1, "You've hit your usage limit. Try again later.", ""
        )

    monkeypatch.setattr(self_engineering, "_codex", fake_codex)
    monkeypatch.setattr(self_engineering, "_changed", lambda worktree: list(changed))
    monkeypatch.setattr(
        self_engineering,
        "_tests",
        lambda worktree, paths: (True, tuple(paths), "ok", "passed"),
    )
    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(["git", *args], 0, "", ""),
    )
    monkeypatch.setattr(
        self_engineering,
        "_discard_unmodified_worktree",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("a patched worktree must never be discarded")
        ),
    )
    monkeypatch.setattr(self_engineering.time, "time", lambda: 7_777)

    result = self_engineering._incident(
        tmp_path / "repo",
        tmp_path / "data",
        [
            {
                "activity_id": "tool:patched",
                "kind": "tool",
                "label": "open_app",
                "status": "failed",
                "detail": "not found",
                "updated_at_ns": 100,
            }
        ],
        False,
    )

    assert result["status"] == "needs_human"
    assert result["reason"] == "verification_pending_transient"
    assert result["changed"] == changed


def test_auth_worker_failure_is_terminal_and_not_retried(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(["git", *args], 0, "", ""),
    )
    monkeypatch.setattr(
        self_engineering,
        "_codex",
        lambda worktree, prompt, sandbox: subprocess.CompletedProcess(
            ["codex"], 1, "Codex ChatGPT login is not connected", ""
        ),
    )
    monkeypatch.setattr(self_engineering, "_changed", lambda worktree: [])
    monkeypatch.setattr(
        self_engineering,
        "_discard_unmodified_worktree",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("auth failure must not enter transient cleanup")
        ),
    )
    monkeypatch.setattr(self_engineering.time, "time", lambda: 8_888)

    result = self_engineering._incident(
        tmp_path / "repo",
        tmp_path / "data",
        [
            {
                "activity_id": "tool:auth",
                "kind": "tool",
                "label": "open_app",
                "status": "failed",
                "detail": "auth",
                "updated_at_ns": 100,
            }
        ],
        False,
    )

    assert result["status"] == "worker_failed"
    assert result["activities"] == ["tool:auth"]


def test_login_failed_try_again_is_not_transient() -> None:
    assert not self_engineering._transient_agent_failure(
        "Login failed; try again after signing in."
    )


def test_changed_includes_staged_paths_and_fails_closed_on_git_error(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(repo: Path, *args: str):
        calls.append(args)
        if args == ("diff", "--name-only"):
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        if args == ("diff", "--cached", "--name-only"):
            return subprocess.CompletedProcess(
                ["git", *args], 0, "jarvis/staged.py\n", ""
            )
        if args == ("ls-files", "--others", "--exclude-standard"):
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(self_engineering, "_git", fake_git)
    assert self_engineering._changed(tmp_path) == ["jarvis/staged.py"]
    assert ("diff", "--cached", "--name-only") in calls

    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(
            ["git", *args], 1, "", "repository unavailable"
        ),
    )
    try:
        self_engineering._changed(tmp_path)
    except RuntimeError as exc:
        assert "repository unavailable" in str(exc)
    else:
        raise AssertionError("_changed must fail closed when git cannot enumerate changes")


def test_transient_cleanup_refuses_staged_or_unknown_worktree_state(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def staged_git(repo: Path, *args: str):
        calls.append((repo, args))
        if args[0] == "status":
            return subprocess.CompletedProcess(
                ["git", *args], 0, "M  jarvis/staged.py\n", ""
            )
        raise AssertionError("cleanup must not remove a dirty worktree")

    monkeypatch.setattr(self_engineering, "_git", staged_git)
    assert not self_engineering._discard_unmodified_worktree(
        tmp_path / "repo", tmp_path / "worktree", "agent/test"
    )
    assert len(calls) == 1

    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(
            ["git", *args], 1, "", "status failed"
        ),
    )
    assert not self_engineering._discard_unmodified_worktree(
        tmp_path / "repo", tmp_path / "worktree", "agent/test"
    )


def test_codex_timeout_before_patch_enters_retry_later(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        self_engineering,
        "_git",
        lambda repo, *args: subprocess.CompletedProcess(["git", *args], 0, "", ""),
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=1200)

    monkeypatch.setattr(self_engineering, "_codex", timeout)
    monkeypatch.setattr(self_engineering, "_changed", lambda worktree: [])
    monkeypatch.setattr(
        self_engineering,
        "_discard_unmodified_worktree",
        lambda repo, worktree, branch: True,
    )
    monkeypatch.setattr(self_engineering.time, "time", lambda: 9_999)

    result = self_engineering._incident(
        tmp_path / "repo",
        tmp_path / "data",
        [
            {
                "activity_id": "tool:timeout",
                "kind": "tool",
                "label": "open_app",
                "status": "failed",
                "detail": "provider stalled",
                "updated_at_ns": 100,
            }
        ],
        False,
    )

    assert result["status"] == "retry_later"
    assert result["reason"] == "transient_worker_failure"


def test_retry_snapshot_persists_across_state_reload_without_secrets(
    tmp_path: Path,
) -> None:
    state = {"bootstrapped": True, "seen": []}
    rows = [
        {
            "activity_id": "tool:persist",
            "kind": "tool",
            "label": "open_app",
            "status": "failed",
            "request_detail": "token nvapi-1234567890SECRET",
            "detail": "quota",
        }
    ]
    self_engineering._record_incident_result(
        state,
        {
            "status": "retry_later",
            "reason": "transient_worker_failure",
            "activities": ["tool:persist"],
        },
        ["tool:persist"],
        now=1_000.0,
        retry_rows=rows,
    )

    path = tmp_path / "state.json"
    self_engineering._save(path, state)
    raw = path.read_text(encoding="utf-8")
    restored = self_engineering._state(path)

    assert "nvapi-1234567890SECRET" not in raw
    assert "[REDACTED]" in raw
    assert self_engineering._due_retry_groups(restored, 1_059.0) == []
    due = self_engineering._due_retry_groups(restored, 1_060.0)
    assert len(due) == 1
    group, activity_ids = due[0]
    assert group[0]["activity_id"] == "tool:persist"
    assert activity_ids == ["tool:persist"]


def test_retry_snapshot_truncation_keeps_complete_activity_id_cleanup() -> None:
    rows = [
        {
            "activity_id": f"tool:{index:02d}",
            "kind": "tool",
            "label": "open_app",
            "status": "failed",
            "detail": "quota",
            "updated_at_ns": index,
        }
        for index in range(33)
    ]
    ids = [row["activity_id"] for row in rows]
    state = {"bootstrapped": True, "seen": []}
    result = {
        "status": "retry_later",
        "reason": "transient_worker_failure",
        "activities": ids,
    }

    self_engineering._record_incident_result(
        state,
        result,
        ids,
        now=1_000.0,
        retry_rows=rows,
    )

    due = self_engineering._due_retry_groups(state, 1_060.0)
    assert len(due) == 1
    snapshot, stored_ids = due[0]
    assert len(snapshot) == 32
    assert stored_ids == ids
    owner = state["retries"][ids[0]]["owner"]
    assert owner in {row["activity_id"] for row in snapshot}

    terminal = self_engineering._record_incident_result(
        state,
        {"status": "needs_human", "activities": stored_ids},
        stored_ids,
        now=1_060.0,
    )
    assert terminal is True
    assert state["seen"] == ids
    assert "retries" not in state


def test_group_gate_blocks_new_correlated_rows_during_backoff_or_after_terminal() -> None:
    state = {
        "retries": {
            "tool:a": {
                "attempt": 1,
                "next_retry_at": 1_060.0,
                "owner": "tool:a",
            }
        }
    }

    assert self_engineering._group_blocked(
        state, set(), {"tool:a", "tool:b"}, 1_059.0
    )
    assert not self_engineering._group_blocked(
        state, set(), {"tool:a", "tool:b"}, 1_060.0
    )
    assert self_engineering._group_blocked(
        {}, {"tool:a"}, {"tool:a", "tool:b"}, 2_000.0
    )
    assert not self_engineering._group_blocked(
        {}, {"tool:a"}, {"tool:b"}, 2_000.0
    )


def test_login_config_and_expired_token_errors_are_never_transient() -> None:
    cases = (
        "Login timed out",
        "Error loading config.toml: invalid value for request_timeout",
        "Token has expired. Please log in and try again.",
    )
    assert all(
        not self_engineering._transient_agent_failure(message)
        for message in cases
    )
