"""Router-visible bridge to Jarvis' isolated lead browser.

The front-page/AERION command dock uses the classic BrainManager bus path, not
the agent-chat SurfaceKit that already adds society_browser. This tiny lazy
wrapper gives that same isolated browser hand to the router without building
the society runtime during app boot or duplicating browser execution.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ToolResult

RuntimeResolver = Callable[[], Any | None]


class LeadSocietyBrowserTool:
    """Drive the lead agent's isolated browser from the main Jarvis router."""

    name: str = "society_browser"
    risk_tier: str = "monitor"
    is_action_tool: bool = True
    description: str = (
        "Use Jarvis' isolated browser for work ON A WEBSITE or WEB APP: navigate "
        "to URLs, search, read pages, click links, fill forms, or operate a web "
        "service. This browser has its own Jarvis profile and is separate from "
        "the user's normal desktop browser. Prefer this over computer_use for "
        "website work. Use computer_use for native desktop apps, windows, mouse "
        "or keyboard work outside a website. Give one clear task and an optional "
        "starting URL."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Workspace files the website task may upload.",
            },
            "task": {
                "type": "string",
                "description": "What to accomplish in the website and what to return.",
            },
            "url": {
                "type": "string",
                "description": "Optional HTTP(S) URL to start at.",
            },
            "max_steps": {
                "type": "integer",
                "description": "Optional browser step cap.",
            },
        },
        "required": ["task"],
    }

    def __init__(self, *, runtime_resolver: RuntimeResolver) -> None:
        self._resolve = runtime_resolver

    def describe_args(self, args: dict[str, Any]) -> dict[str, str] | None:
        task = str(args.get("task") or "").strip()
        if not task:
            return None
        return {
            "task": task[:300],
            "url": str(args.get("url") or "")[:200],
        }

    @staticmethod
    def _model_pick() -> tuple[str, str] | None:
        """Use the active brain family, but never pin a known screenshot-blind model."""
        try:
            from jarvis.brain.model_catalog import model_capabilities, pick_fast_vision_model
            from jarvis.core import runtime_refs

            manager = runtime_refs.get_brain_manager()
            provider = str(getattr(manager, "active_provider", "") or "").strip()
            if not provider:
                return None
            fast_model = getattr(manager, "_fast_model", None)
            model = str(fast_model(provider) or "").strip() if callable(fast_model) else ""
            if model and model_capabilities(provider, model).get("vision") is False:
                model = pick_fast_vision_model(provider) or model
            return (provider, model) if model else None
        except Exception:
            # BrowserTool can resolve the lead's configured model itself.
            return None

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        task = str(args.get("task") or "").strip()
        if not task:
            return ToolResult(success=False, output=None, error="task is required")

        try:
            runtime = self._resolve()
            if runtime is None:
                return ToolResult(success=False, output=None, error="browser runtime unavailable")
            prepare = getattr(runtime, "prepare_context", None)
            if callable(prepare):
                if not await prepare():
                    return ToolResult(success=False, output=None, error="browser runtime unavailable")
            else:
                await runtime.ensure_started()

            from jarvis.society.browser.tool import BrowserTool

            browser = BrowserTool(
                runtime,
                runtime.lead_id,
                runtime.browser,
                model_pick=self._model_pick(),
            )
            return await browser.execute(args, ctx)
        except Exception as exc:  # noqa: BLE001 - return an inspectable tool failure
            return ToolResult(success=False, output=None, error=str(exc))


__all__ = ["LeadSocietyBrowserTool"]
