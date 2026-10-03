"""N-17 transport: the HUD rides the EXISTING FastAPI app and ``/ws`` socket.

- ``GET /api/hud/snapshot`` returns the full canonical snapshot (reconnect /
  reload / resume read it once instead of replaying events).
- A visible HUD change is pushed as a ``hud.snapshot`` frame on ``/ws``.
- The ``welcome`` frame stays the only unsolicited frame on connect.
- The route is read-only: there is no write verb under ``/api/hud``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import ActionApprovalRequired, TaskStarted
from jarvis.ui.hud import reset_hud_adapters
from jarvis.ui.web.server import WebServer


@pytest.fixture
def web_server():
    reset_hud_adapters()
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    server = WebServer(cfg, bus=EventBus())
    yield server
    reset_hud_adapters()


def test_snapshot_route_reflects_bus_state(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        first = client.get("/api/hud/snapshot")
        assert first.status_code == 200
        body = first.json()
        assert body["primary_state"] == "IDLE"
        assert body["schema_version"] == 1
        assert body["epoch"]

        assert client.portal is not None
        client.portal.call(
            web_server.bus.publish,
            ActionApprovalRequired(tool_name="deploy", mission_id="m-1", args_preview="prod"),
        )
        body = client.get("/api/hud/snapshot").json()
        assert body["primary_state"] == "WAITING_FOR_APPROVAL"
        card = body["approval_requests"][0]
        assert card["mission_id"] == "m-1"
        assert card["decision_channel"] == "mission_tool_api"
        assert body["revision"] > first.json()["revision"]


def test_snapshot_route_is_read_only(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)("/api/hud/snapshot").status_code == 405


def test_snapshot_route_503_without_adapter(web_server: WebServer) -> None:
    web_server.app.state.hud = None
    with TestClient(web_server.app) as client:
        assert client.get("/api/hud/snapshot").status_code == 503


def test_welcome_stays_first_and_changes_are_pushed(web_server: WebServer) -> None:
    with TestClient(web_server.app) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "welcome"
            assert client.portal is not None
            client.portal.call(web_server.bus.publish, TaskStarted(task_id="t-1"))
            frames = []
            for _ in range(6):
                frame = ws.receive_json()
                frames.append(frame["type"])
                if frame["type"] == "hud.snapshot":
                    snapshot = frame["snapshot"]
                    assert snapshot["primary_state"] == "WORKING"
                    assert snapshot["active_operations"][0]["task_id"] == "t-1"
                    break
            else:  # pragma: no cover - diagnostic
                pytest.fail(f"no hud.snapshot frame, got {frames}")
            # The event envelope itself still arrives first, unchanged.
            assert frames[0] == "event"
