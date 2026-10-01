from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "jarvis/core/protocols.py",
    '''@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Context passed to a tool at execution time."""

    trace_id: UUID
    user_utterance: str
    config: dict[str, Any]
    memory_read: Any  # MemoryStore read-only handle
    approved_by: str | None = None  # "auto" | "user" | None (falls tier=safe)
''',
    '''@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Context passed to a tool at execution time."""

    trace_id: UUID
    user_utterance: str
    config: dict[str, Any]
    memory_read: Any  # MemoryStore read-only handle
    approved_by: str | None = None  # "auto" | "user" | None (falls tier=safe)
    project_id: str | None = None
    task_id: str | None = None
    project_root: str | None = None
    capability_grant: Any | None = None
    delegated: bool = False
''',
)

replace_once(
    "jarvis/core/protocols.py",
    '''@dataclass(frozen=True, slots=True)
class SupervisorToolRequest:
    """Execution metadata supplied by Realtime or a mission worker."""

    trace_id: UUID
    origin: str
    user_utterance: str
    rationale: str = ""
    mission_id: str | None = None
    worker_id: str | None = None
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    cancel_token: CancelToken | None = None
''',
    '''@dataclass(frozen=True, slots=True)
class SupervisorToolRequest:
    """Execution metadata supplied by Realtime or a mission worker."""

    trace_id: UUID
    origin: str
    user_utterance: str
    rationale: str = ""
    mission_id: str | None = None
    worker_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    project_root: str | None = None
    capability_grant: Any | None = None
    delegated: bool = False
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    cancel_token: CancelToken | None = None
''',
)

replace_once(
    "jarvis/brain/tool_gateway.py",
    '''        return await executor.execute(
            tool,
            dict(arguments),
            user_utterance=request.user_utterance,
            config_snapshot=config_snapshot,
            trace_id=request.trace_id,
            rationale=request.rationale,
            cancel_token=request.cancel_token,
        )
''',
    '''        return await executor.execute(
            tool,
            dict(arguments),
            user_utterance=request.user_utterance,
            config_snapshot=config_snapshot,
            trace_id=request.trace_id,
            rationale=request.rationale,
            cancel_token=request.cancel_token,
            project_id=request.project_id,
            task_id=request.task_id,
            project_root=request.project_root,
            capability_grant=request.capability_grant,
            delegated=request.delegated,
        )
''',
)
