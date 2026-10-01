from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "jarvis/missions/workers/worker_tool_broker.py"

replace_once(
    path,
    '''import threading
import time
from contextlib import suppress
''',
    '''import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
''',
)

replace_once(
    path,
    '''from jarvis.safety.approval_surface import INTERACTIVE
''',
    '''from jarvis.safety.approval_surface import INTERACTIVE
from jarvis.safety.capabilities import CapabilityGrant
''',
)

replace_once(
    path,
    '''    mission_id: str | None = None
    worker_id: str | None = None
    _revoked: bool = False
''',
    '''    mission_id: str | None = None
    worker_id: str | None = None
    capability_grant: CapabilityGrant | None = None
    _revoked: bool = False
''',
)

replace_once(
    path,
    '''                    mission_id=self.mission_id,
                    worker_id=self.worker_id,
                    config_snapshot={
''',
    '''                    mission_id=self.mission_id,
                    worker_id=self.worker_id,
                    capability_grant=self.capability_grant,
                    delegated=True,
                    config_snapshot={
''',
)

replace_once(
    path,
    '''        scope = _BrokerScope(
            task_text=task_text,
            gateway=gateway,
            loop=loop,
            expires_at=time.monotonic() + max(1.0, float(ttl_s)),
            mcp_server_ids=tuple(dict.fromkeys(mcp_server_ids)),
            app_commands=requested_app_commands,
            native_tool_names=tuple(dict.fromkeys(native_tool_names)),
            mission_id=mission_id,
            worker_id=worker_id,
        )
        if not scope.specs:
            return None
''',
    '''        ttl_seconds = max(1.0, float(ttl_s))
        scope = _BrokerScope(
            task_text=task_text,
            gateway=gateway,
            loop=loop,
            expires_at=time.monotonic() + ttl_seconds,
            mcp_server_ids=tuple(dict.fromkeys(mcp_server_ids)),
            app_commands=requested_app_commands,
            native_tool_names=tuple(dict.fromkeys(native_tool_names)),
            mission_id=mission_id,
            worker_id=worker_id,
        )
        granted_tool_names = frozenset(str(spec["name"]) for spec in scope.specs)
        if not granted_tool_names:
            return None
        scope.capability_grant = CapabilityGrant(
            grant_id=str(uuid4()),
            tools=granted_tool_names,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )
''',
)
