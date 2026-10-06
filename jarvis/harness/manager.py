"""HarnessManager: entry-point discovery, health check, dispatch, parallel.

Analogous to BrainManager: lazy-loads plugins via `importlib.metadata.entry_points`,
falls back through a chain when primary is missing, `dispatch(name, task)` as a
streaming iterator.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from importlib.metadata import entry_points
from uuid import uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import HarnessCompleted, HarnessDispatched, HarnessProgress
from jarvis.core.protocols import Harness, HarnessResult, HarnessTask

log = logging.getLogger(__name__)

PLUGIN_GROUP = "jarvis.harness"


class HarnessManager:
    """Lifecycle manager and dispatcher for all registered harnesses."""

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus
        self._classes: dict[str, type] = {}
        self._failed: dict[str, str] = {}
        self._instances: dict[str, Harness] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _load_classes(self) -> None:
        if self._loaded:
            return
        for ep in entry_points(group=PLUGIN_GROUP):
            try:
                cls = ep.load()
                self._classes[ep.name] = cls
            except Exception as exc:  # noqa: BLE001
                self._failed[ep.name] = f"{type(exc).__name__}: {exc}"
        self._loaded = True

    def available(self) -> list[str]:
        self._load_classes()
        return sorted(self._classes.keys())

    def failed(self) -> dict[str, str]:
        self._load_classes()
        return dict(self._failed)

    def get(self, name: str) -> Harness:
        self._load_classes()
        if name not in self._instances:
            if name not in self._classes:
                # The raw active/failed inventory belongs in the log ONLY — it
                # must not ride along in the exception message, which can reach
                # the voice path (a harness name + the internal list was read
                # aloud, forensic 2026-06-28). Keep the message short + neutral.
                log.warning(
                    "Harness %r not registered. Active: %s. Failed: %s.",
                    name, self.available(), list(self._failed),
                )
                raise KeyError(f"harness {name!r} not registered")
            self._instances[name] = self._classes[name]()
        return self._instances[name]

    async def health(self, name: str) -> bool:
        try:
            harness = self.get(name)
            return await harness.health()
        except Exception:  # noqa: BLE001
            return False

    async def healthy_harnesses(self) -> list[str]:
        results = await asyncio.gather(
            *(self.health(n) for n in self.available()),
            return_exceptions=True,
        )
        return [n for n, ok in zip(self.available(), results) if ok is True]

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(
        self, name: str, task: HarnessTask
    ) -> AsyncIterator[HarnessResult]:
        """Start a harness and yield progress plus one correlated terminal event.

        Every lifecycle event of ONE dispatch carries the same trace id. The HUD
        keys running harness work by trace_id + harness; letting each event
        create its own default UUID meant a completion could never close the row
        opened by HarnessDispatched.

        HarnessCompleted is emitted from the outer finally even when a caller
        abandons the stream or the harness raises. This is a projection contract,
        not a claim that an interrupted harness succeeded: interrupted streams
        use exit code 130 and crashes use exit code 1.
        """
        harness = self.get(name)
        event_trace = uuid4()
        if self._bus is not None:
            await self._bus.publish(
                HarnessDispatched(trace_id=event_trace, harness=name, task=task)
            )

        terminal: HarnessResult | None = None
        stream = harness.invoke(task)
        try:
            try:
                async for result in stream:
                    if result.is_final:
                        terminal = result
                    elif self._bus is not None:
                        await self._bus.publish(
                            HarnessProgress(
                                trace_id=event_trace,
                                harness=name,
                                result=result,
                            )
                        )
                    yield result
            except asyncio.CancelledError:
                terminal = HarnessResult(
                    stderr="harness dispatch cancelled",
                    exit_code=130,
                    is_final=True,
                )
                raise
            except GeneratorExit:
                terminal = HarnessResult(
                    stderr="harness dispatch closed before completion",
                    exit_code=130,
                    is_final=True,
                )
                raise
            except Exception as exc:
                terminal = HarnessResult(
                    stderr=f"{type(exc).__name__}: {exc}",
                    exit_code=1,
                    is_final=True,
                )
                raise
        finally:
            try:
                # A consumer that abandons this dispatch mid-stream (break,
                # outer cancellation, explicit aclose) must still unwind the
                # harness own finally deterministically.
                await stream.aclose()
            except asyncio.CancelledError:
                terminal = terminal or HarnessResult(
                    stderr="harness cleanup cancelled",
                    exit_code=130,
                    is_final=True,
                )
                raise
            except GeneratorExit:
                terminal = terminal or HarnessResult(
                    stderr="harness cleanup closed before completion",
                    exit_code=130,
                    is_final=True,
                )
                raise
            except Exception as exc:
                terminal = terminal or HarnessResult(
                    stderr=f"{type(exc).__name__}: {exc}",
                    exit_code=1,
                    is_final=True,
                )
                raise
            finally:
                if terminal is None:
                    # A harness is required to yield a final result. Ending
                    # without one is a failed lifecycle, never a forever-running
                    # HUD row and never an invented success.
                    terminal = HarnessResult(
                        stderr="harness ended without a final result",
                        exit_code=1,
                        is_final=True,
                    )
                if self._bus is not None:
                    await self._bus.publish(
                        HarnessCompleted(
                            trace_id=event_trace,
                            harness=name,
                            result=terminal,
                        )
                    )

    async def dispatch_parallel(
        self,
        names: list[str],
        task: HarnessTask,
        *,
        aggregation: str = "merge",
    ) -> AsyncIterator[tuple[str, HarnessResult]]:
        """Dispatch in parallel to N harnesses, yielding (harness_name, result) tuples.

        aggregation:
            - "merge": all events interleaved (default)
            - "first_success": stops as soon as the first is_final result with exit_code=0 arrives
        """
        queue: asyncio.Queue[tuple[str, HarnessResult] | None] = asyncio.Queue()
        tasks: list[asyncio.Task[None]] = []
        active = len(names)

        async def _run(n: str) -> None:
            try:
                async for r in self.dispatch(n, task):
                    await queue.put((n, r))
            except Exception as exc:  # noqa: BLE001
                await queue.put((n, HarnessResult(
                    stderr=f"Harness-Crash: {exc}\n",
                    exit_code=1,
                    is_final=True,
                )))
            finally:
                await queue.put(None)

        for n in names:
            tasks.append(asyncio.create_task(_run(n)))

        try:
            while active > 0:
                item = await queue.get()
                if item is None:
                    active -= 1
                    continue
                yield item
                if aggregation == "first_success" and item[1].is_final and item[1].exit_code == 0:
                    break
        finally:
            for t in tasks:
                t.cancel()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel_all(self) -> None:
        for name, inst in list(self._instances.items()):
            try:
                await inst.cancel()
            except Exception as exc:  # noqa: BLE001
                log.debug("Cancel %s: %s", name, exc)