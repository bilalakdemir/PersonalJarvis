from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "jarvis/safety/tool_executor.py"

replace_once(
    path,
    '''        rationale: str = "",
        cancel_token: CancelToken | None = None,
    ) -> ToolResult:
''',
    '''        rationale: str = "",
        cancel_token: CancelToken | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        project_root: str | None = None,
        capability_grant: CapabilityGrant | None = None,
        delegated: bool = False,
    ) -> ToolResult:
''',
)

replace_once(
    path,
    '''        # 2. Plausibility check (Phase 4): the result can force confirmation
''',
    '''        # Hard safety above stays first; the tier decision is not acted on
        # until governance and delegated capability checks have both passed.
        policy_denied = await self._enforce_governance_and_capability(
            tid=tid,
            tool=tool,
            args=args,
            project_id=project_id,
            task_id=task_id,
            project_root=project_root,
            capability_grant=capability_grant,
            delegated=delegated,
        )
        if policy_denied is not None:
            return policy_denied

        # 2. Plausibility check (Phase 4): the result can force confirmation
''',
)

replace_once(
    path,
    '''                self._pending_voice[tid] = (tool, dict(args))
''',
    '''                self._pending_voice[tid] = _PendingVoiceAction(
                    tool=tool,
                    args=dict(args),
                    project_id=project_id,
                    task_id=task_id,
                    project_root=project_root,
                    capability_grant=capability_grant,
                    delegated=delegated,
                )
''',
)

replace_once(
    path,
    '''            approved_by=approved_by,
        )
''',
    '''            approved_by=approved_by,
            project_id=project_id,
            task_id=task_id,
            project_root=project_root,
            capability_grant=capability_grant,
            delegated=delegated,
        )
''',
)

replace_once(
    path,
    '''        tool, args = pending
        ctx = ExecutionContext(
            trace_id=trace_id,
            user_utterance=user_utterance,
            config=config_snapshot or {},
            memory_read=memory_read,
            approved_by="user",
        )
''',
    '''        tool, args = pending.tool, pending.args
        try:
            self._evaluator.evaluate(tool, args)
        except ActionBlocked as exc:
            await self._bus.publish(ActionDenied(
                trace_id=trace_id,
                tool_name=tool.name,
                reason=f"blacklist: {exc.pattern}",
            ))
            return ToolResult(success=False, output=None, error=str(exc))

        policy_denied = await self._enforce_governance_and_capability(
            tid=trace_id,
            tool=tool,
            args=args,
            project_id=pending.project_id,
            task_id=pending.task_id,
            project_root=pending.project_root,
            capability_grant=pending.capability_grant,
            delegated=pending.delegated,
        )
        if policy_denied is not None:
            return policy_denied

        ctx = ExecutionContext(
            trace_id=trace_id,
            user_utterance=user_utterance,
            config=config_snapshot or {},
            memory_read=memory_read,
            approved_by="user",
            project_id=pending.project_id,
            task_id=pending.task_id,
            project_root=pending.project_root,
            capability_grant=pending.capability_grant,
            delegated=pending.delegated,
        )
''',
)

replace_once(
    path,
    '''        tool, _args = pending
''',
    '''        tool = pending.tool
''',
)
