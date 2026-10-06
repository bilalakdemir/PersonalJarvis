from __future__ import annotations

import json
from pathlib import Path

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
