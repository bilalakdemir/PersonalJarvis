"""Windows login startup integration for the AERION supervisor."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

SHORTCUT_NAME = "AERION Supervisor.lnk"
LEGACY_SCRIPT_NAME = "aerion_supervisor.pyw"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SupervisorStartupStatus:
    supported: bool
    installed: bool
    matches: bool
    entry_path: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
    return (
        Path.home()
        / "AppData"
        / "Roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def supervisor_shortcut_path() -> Path:
    return _startup_dir() / SHORTCUT_NAME


def legacy_supervisor_script_path() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return local / "PersonalJarvis" / LEGACY_SCRIPT_NAME


def _pythonw(repo_root: Path) -> Path:
    candidate = repo_root / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.is_file():
        return candidate
    sibling = Path(sys.executable).with_name("pythonw.exe")
    return sibling if sibling.is_file() else Path(sys.executable)


def _expected(repo_root: Path) -> tuple[str, str, str]:
    program = str(_pythonw(repo_root))
    args = (
        "-m jarvis.operations.supervisor "
        f'--repo-root "{repo_root}" '
        f'--data-dir "{repo_root / "data-dev"}" '
        "--auto-merge-low-risk"
    )
    return program, args, str(repo_root)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def _probe(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    script = (
        "$ws=New-Object -ComObject WScript.Shell;"
        f"$s=$ws.CreateShortcut({_ps_quote(str(path))});"
        "$o=[ordered]@{TargetPath=$s.TargetPath;Arguments=$s.Arguments;"
        "WorkingDirectory=$s.WorkingDirectory};"
        "$o|ConvertTo-Json -Compress"
    )
    result = _run_powershell(script)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout.strip())
    except ValueError:
        log.debug("Supervisor shortcut metadata was not valid JSON", exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _norm(value: str) -> str:
    return str(value or "").strip().strip('"').casefold()


def supervisor_startup_status(repo_root: Path) -> SupervisorStartupStatus:
    repo = repo_root.resolve()
    path = supervisor_shortcut_path()
    if sys.platform != "win32":
        return SupervisorStartupStatus(
            False,
            False,
            False,
            str(path),
            "AERION supervisor startup is currently managed only on Windows.",
        )
    info = _probe(path)
    if info is None:
        return SupervisorStartupStatus(
            True,
            False,
            False,
            str(path),
            "AERION supervisor startup entry is not installed.",
        )
    program, args, working_dir = _expected(repo)
    matches = (
        _norm(info.get("TargetPath", "")) == _norm(program)
        and str(info.get("Arguments", "")).strip() == args
        and _norm(info.get("WorkingDirectory", "")) == _norm(working_dir)
    )
    return SupervisorStartupStatus(
        True,
        True,
        matches,
        str(path),
        (
            "AERION supervisor starts automatically at Windows login."
            if matches
            else "AERION supervisor startup entry points at a different install."
        ),
    )


def install_supervisor_startup(repo_root: Path) -> SupervisorStartupStatus:
    repo = repo_root.resolve()
    if sys.platform != "win32":
        return supervisor_startup_status(repo)
    path = supervisor_shortcut_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    program, args, working_dir = _expected(repo)
    script = (
        "$ws=New-Object -ComObject WScript.Shell;"
        f"$s=$ws.CreateShortcut({_ps_quote(str(path))});"
        f"$s.TargetPath={_ps_quote(program)};"
        f"$s.Arguments={_ps_quote(args)};"
        f"$s.WorkingDirectory={_ps_quote(working_dir)};"
        "$s.Description='AERION lifecycle supervisor';"
        "$s.Save()"
    )
    result = _run_powershell(script)
    if result.returncode != 0:
        return SupervisorStartupStatus(
            True,
            False,
            False,
            str(path),
            f"Could not install supervisor startup entry: {result.stderr.strip()[:300]}",
        )
    return supervisor_startup_status(repo)


def uninstall_supervisor_startup(repo_root: Path) -> SupervisorStartupStatus:
    path = supervisor_shortcut_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning(
            "AERION supervisor startup shortcut could not be removed",
            exc_info=True,
        )
    return supervisor_startup_status(repo_root.resolve())


def migrate_legacy_supervisor(repo_root: Path) -> SupervisorStartupStatus:
    status = install_supervisor_startup(repo_root)
    if not status.matches:
        return status
    legacy = legacy_supervisor_script_path()
    try:
        legacy.unlink(missing_ok=True)
    except OSError:
        log.warning(
            "Legacy AERION supervisor script could not be removed",
            exc_info=True,
        )
    return supervisor_startup_status(repo_root.resolve())


__all__ = [
    "SupervisorStartupStatus",
    "install_supervisor_startup",
    "legacy_supervisor_script_path",
    "migrate_legacy_supervisor",
    "supervisor_shortcut_path",
    "supervisor_startup_status",
    "uninstall_supervisor_startup",
]
