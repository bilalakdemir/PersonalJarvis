"""Read-only REST surface for the canonical HUD snapshot (N-17).

Endpoint::

    GET /api/hud/snapshot   The full current HudSnapshot (wire shape)

This is the reconnect/reload/resume path: a window that just (re)opened reads
the whole semantic state once instead of replaying events. Live changes reach
open windows as ``hud.snapshot`` frames on the EXISTING ``/ws`` socket (see
``WebServer._queue_hud_snapshot``) — there is no second socket and no polling.

Strictly read-only. The HUD answers nothing: an approval card's buttons call
the owning domain's existing route (``/api/missions/{id}/tool-approvals/
{trace_id}/approve|deny``), never this module. Every string in the snapshot
was secret-masked and length-capped by the reducer before it got here.

Wired in by the WebServer in ``_build_app()``, behind ``SurfaceSecurity`` like
every other route.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/hud", tags=["hud"])


@router.get("/snapshot")
def get_hud_snapshot(request: Request) -> dict[str, Any]:
    """Return the current semantic HUD state (safe, redacted, bounded).

    A plain ``def`` on purpose: FastAPI serves it from the threadpool, never
    from the event loop that carries voice and every WebSocket. The adapter's
    snapshot is thread-safe for exactly this caller.
    """
    adapter = getattr(request.app.state, "hud", None)
    if adapter is None:
        raise HTTPException(status_code=503, detail="HUD is not available")
    return adapter.snapshot().to_dict()


__all__ = ["router"]
