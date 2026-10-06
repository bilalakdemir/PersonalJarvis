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
