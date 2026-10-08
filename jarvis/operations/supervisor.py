"""AERION desktop/runtime supervisor.

This module owns only process lifecycle. It deliberately does not choose model
providers, edit jarvis.toml, or bypass the desktop restart safety boundary.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psutil

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

DEFAULT_ADMIN_API_PORT = 47821
CHECK_SECONDS = 5.0
HANG_FAILURES = 6
STATE_DIR_NAME = "PersonalJarvis"
LOG_FILE_NAME = "aerion-supervisor.log"
LOCK_FILE_NAME = "aerion-supervisor.lock"
STOP_FILE_NAME = "aerion-supervisor.stop"

if sys.platform == "win32":
    import msvcrt
else:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def state_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return base / STATE_DIR_NAME
    return Path.home() / ".local" / "state" / "personal-jarvis"


def supervisor_log_path() -> Path:
    return state_dir() / LOG_FILE_NAME


def supervisor_stop_path() -> Path:
    return state_dir() / STOP_FILE_NAME


def open_diagnostics() -> bool:
    """Open the durable supervisor log without spawning a console window."""
    _append_log("diagnostics opened")
    path = supervisor_log_path()
    if sys.platform != "win32":
        return False
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        log.debug("Could not open supervisor diagnostics log", exc_info=True)
        return False
    return True


def _append_log(message: str) -> None:
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with supervisor_log_path().open("a", encoding="utf-8") as stream:
        stream.write(f"{stamp} {message}\n")


def _admin_api_port(root: Path) -> int:
    path = root / "jarvis.toml"
    if not path.is_file():
        return DEFAULT_ADMIN_API_PORT
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        ui = data.get("ui")
        if isinstance(ui, dict):
            value = ui.get("admin_api_port")
            if isinstance(value, int) and 1 <= value <= 65535:
                return value
    except (OSError, ValueError):
        log.debug("Could not read admin_api_port from %s", path, exc_info=True)
    return DEFAULT_ADMIN_API_PORT


def health_url(root: Path | None = None) -> str:
    port = _admin_api_port(root or project_root())
    return f"http://127.0.0.1:{port}/api/health"


def _health_payload(root: Path, *, timeout_s: float = 2.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed loopback URL
            health_url(root),
            timeout=timeout_s,
        ) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):  # Health miss is the recovery signal.
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("ok") is not True or payload.get("instance") != "default":
        return None
    return payload


def runtime_healthy(root: Path | None = None) -> bool:
    return _health_payload(root or project_root()) is not None


def _cmdline(proc: psutil.Process) -> str:
    try:
        return " ".join(proc.cmdline()).casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):  # Process races are expected while scanning.
        return ""


def _matching_processes(marker: str) -> list[psutil.Process]:
    needle = marker.casefold()
    found: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid"]):
        if proc.pid == os.getpid():
            continue
        if needle in _cmdline(proc):
            found.append(proc)
    return found


def desktop_processes() -> list[psutil.Process]:
    found = _matching_processes("jarvis.ui.web.launcher")
    return [proc for proc in found if "--instance" not in _cmdline(proc)]


def self_engineering_processes() -> list[psutil.Process]:
    return _matching_processes("-m jarvis.self_engineering")


def relauncher_processes() -> list[psutil.Process]:
    return _matching_processes("-m jarvis.ui.relauncher")


def supervisor_processes() -> list[psutil.Process]:
    return _matching_processes("-m jarvis.operations.supervisor")


def _pythonw(root: Path) -> str:
    if sys.platform == "win32":
        candidate = root / ".venv" / "Scripts" / "pythonw.exe"
        if candidate.is_file():
            return str(candidate)
        sibling = Path(sys.executable).with_name("pythonw.exe")
        if sibling.is_file():
            return str(sibling)
    return sys.executable


def _spawn(root: Path, argv: list[str]) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        argv,
        cwd=str(root),
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def start_desktop(root: Path, *, reason: str) -> None:
    _append_log(f"starting desktop reason={reason}")
    _spawn(root, [_pythonw(root), "-m", "jarvis.ui.web.launcher"])


def start_self_engineering(
    root: Path,
    data_dir: Path,
    *,
    auto_merge_low_risk: bool,
    reason: str,
) -> None:
    argv = [
        _pythonw(root),
        "-m",
        "jarvis.self_engineering",
        "--repo-root",
        str(root),
        "--data-dir",
        str(data_dir),
    ]
    if auto_merge_low_risk:
        argv.append("--auto-merge-low-risk")
    _append_log(f"starting self_engineering reason={reason}")
    _spawn(root, argv)


def terminate_desktop_tree() -> int:
    roots = desktop_processes()
    if not roots:
        return 0
    descendants: dict[int, psutil.Process] = {}
    for proc in roots:
        try:
            for child in proc.children(recursive=True):
                descendants[child.pid] = child
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # A disappearing child is already cleaned up.
            continue
    targets_by_pid = {proc.pid: proc for proc in roots}
    targets_by_pid.update(descendants)
    targets = list(targets_by_pid.values())
    for proc in targets:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # Exit during terminate is the desired outcome.
            continue
    _, alive = psutil.wait_procs(targets, timeout=4.0)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # Exit before kill is already success.
            continue
    _append_log(f"terminated stale desktop processes={len(targets)}")
    return len(targets)


def _pid_list(items: list[psutil.Process]) -> list[int]:
    return sorted(proc.pid for proc in items)


def operational_status(root: Path | None = None) -> dict[str, Any]:
    repo = (root or project_root()).resolve()
    payload = _health_payload(repo)
    try:
        from jarvis.operations.windows_startup import supervisor_startup_status

        startup = supervisor_startup_status(repo).to_dict()
    except Exception:  # noqa: BLE001 - status must degrade, never crash
        log.debug("Supervisor startup status failed", exc_info=True)
        startup = {
            "supported": sys.platform == "win32",
            "installed": False,
            "matches": False,
            "detail": "startup status unavailable",
        }
    desktop = desktop_processes()
    engineering = self_engineering_processes()
    supervisors = supervisor_processes()
    return {
        "reachable": payload is not None,
        "runtime": {
            "healthy": payload is not None,
            "pids": _pid_list(desktop),
            "health": payload or {},
        },
        "supervisor": {
            "running": bool(supervisors),
            "pids": _pid_list(supervisors),
            "log_path": str(supervisor_log_path()),
        },
        "self_engineering": {
            "running": bool(engineering),
            "pids": _pid_list(engineering),
        },
        "startup": startup,
    }


class _WindowsSingleton:
    def __init__(self) -> None:
        self._handle: Any = None

    def acquire(self) -> bool:
        if sys.platform != "win32" or msvcrt is None:
            return True
        root = state_dir()
        root.mkdir(parents=True, exist_ok=True)
        handle = (root / LOCK_FILE_NAME).open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:  # Lock contention means another supervisor already owns lifecycle.
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None or sys.platform != "win32" or msvcrt is None:
            return
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:  # Best-effort unlock during process teardown cannot be recovered here.
            pass
        handle.close()


def supervise(
    *,
    root: Path,
    data_dir: Path,
    auto_merge_low_risk: bool,
    check_seconds: float = CHECK_SECONDS,
    hang_failures: int = HANG_FAILURES,
) -> int:
    singleton = _WindowsSingleton()
    if not singleton.acquire():
        return 0
    executable = Path(_pythonw(root))
    if not executable.is_file():
        _append_log(f"fatal python executable missing: {executable}")
        singleton.release()
        return 2

    _append_log(f"supervisor started pid={os.getpid()}")
    health_failures = 0
    # Probe the sidecar on the first loop, then every fourth loop.
    engineering_tick = 3
    try:
        while True:
            if supervisor_stop_path().exists():
                health_failures = 0
                time.sleep(check_seconds)
                continue

            engineering_tick += 1
            if engineering_tick >= 4:
                engineering_tick = 0
                if not self_engineering_processes():
                    start_self_engineering(
                        root,
                        data_dir,
                        auto_merge_low_risk=auto_merge_low_risk,
                        reason="missing",
                    )

            if runtime_healthy(root):
                health_failures = 0
            else:
                apps = desktop_processes()
                if not apps:
                    health_failures = 0
                    if not relauncher_processes():
                        start_desktop(root, reason="not_running")
                        time.sleep(min(8.0, max(check_seconds, 0.1) * 2))
                else:
                    health_failures += 1
                    if health_failures >= hang_failures:
                        _append_log("desktop health failed continuously; recovering")
                        terminate_desktop_tree()
                        start_desktop(root, reason="health_recovery")
                        health_failures = 0
                        time.sleep(min(8.0, max(check_seconds, 0.1) * 2))

            time.sleep(check_seconds)
    except KeyboardInterrupt:
        _append_log("supervisor stopped by user")
        return 0
    except Exception as exc:  # noqa: BLE001 - durable log + nonzero exit
        _append_log(f"supervisor fatal {type(exc).__name__}: {exc}")
        log.exception("AERION supervisor failed")
        return 1
    finally:
        singleton.release()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AERION desktop lifecycle supervisor")
    parser.add_argument("--repo-root", type=Path, default=project_root())
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--auto-merge-low-risk", action="store_true")
    parser.add_argument("--status", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.repo_root.resolve()
    if args.status:
        print(json.dumps(operational_status(root), indent=2, sort_keys=True))
        return 0
    data_dir = (args.data_dir or (root / "data-dev")).resolve()
    return supervise(
        root=root,
        data_dir=data_dir,
        auto_merge_low_risk=bool(args.auto_merge_low_risk),
    )


if __name__ == "__main__":
    raise SystemExit(main())
