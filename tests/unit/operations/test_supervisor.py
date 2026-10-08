from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import jarvis.operations.supervisor as supervisor


class _Proc:
    def __init__(self, pid: int, command: str = "") -> None:
        self.pid = pid
        self._command = command
        self.terminated = False
        self.killed = False
        self._children: list[_Proc] = []

    def cmdline(self) -> list[str]:
        return self._command.split()

    def children(self, recursive: bool = False) -> list["_Proc"]:
        assert recursive is True
        return list(self._children)

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_admin_api_port_reads_only_ui_port(tmp_path: Path) -> None:
    (tmp_path / "jarvis.toml").write_text(
        '[ui]\nadmin_api_port = 49111\n[brain]\nprimary = "nvidia"\n',
        encoding="utf-8",
    )

    assert supervisor._admin_api_port(tmp_path) == 49111


def test_admin_api_port_falls_back_on_invalid_config(tmp_path: Path) -> None:
    (tmp_path / "jarvis.toml").write_text(
        "[ui]\nadmin_api_port = 99999\n",
        encoding="utf-8",
    )

    assert supervisor._admin_api_port(tmp_path) == supervisor.DEFAULT_ADMIN_API_PORT


def test_operational_status_reports_runtime_supervisor_and_engineering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from jarvis.operations import windows_startup

    monkeypatch.setattr(
        supervisor,
        "_health_payload",
        lambda root: {"ok": True, "instance": "default", "stage": "Ready"},
    )
    monkeypatch.setattr(supervisor, "desktop_processes", lambda: [_Proc(10)])
    monkeypatch.setattr(supervisor, "supervisor_processes", lambda: [_Proc(20)])
    monkeypatch.setattr(
        supervisor,
        "self_engineering_processes",
        lambda: [_Proc(30), _Proc(31)],
    )
    monkeypatch.setattr(
        supervisor,
        "supervisor_log_path",
        lambda: tmp_path / "supervisor.log",
    )
    monkeypatch.setattr(
        windows_startup,
        "supervisor_startup_status",
        lambda root: windows_startup.SupervisorStartupStatus(
            True,
            True,
            True,
            "startup.lnk",
            "ok",
        ),
    )

    report = supervisor.operational_status(tmp_path)

    assert report["reachable"] is True
    assert report["runtime"]["healthy"] is True
    assert report["runtime"]["pids"] == [10]
    assert report["supervisor"]["pids"] == [20]
    assert report["self_engineering"]["pids"] == [30, 31]
    assert report["startup"]["matches"] is True


def test_start_self_engineering_preserves_expected_arguments(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seen: list[list[str]] = []

    def fake_spawn(root: Path, argv: list[str]):
        assert root == tmp_path
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(supervisor, "_spawn", fake_spawn)
    monkeypatch.setattr(supervisor, "_pythonw", lambda root: "pythonw.exe")
    monkeypatch.setattr(supervisor, "_append_log", lambda message: None)

    supervisor.start_self_engineering(
        tmp_path,
        tmp_path / "data-dev",
        auto_merge_low_risk=True,
        reason="test",
    )

    assert seen == [
        [
            "pythonw.exe",
            "-m",
            "jarvis.self_engineering",
            "--repo-root",
            str(tmp_path),
            "--data-dir",
            str(tmp_path / "data-dev"),
            "--auto-merge-low-risk",
        ]
    ]


def test_terminate_desktop_tree_deduplicates_descendants(monkeypatch) -> None:
    root = _Proc(10)
    child = _Proc(11)
    root._children = [child]
    monkeypatch.setattr(supervisor, "desktop_processes", lambda: [root, child])
    monkeypatch.setattr(
        supervisor.psutil,
        "wait_procs",
        lambda targets, timeout: (targets, []),
    )
    monkeypatch.setattr(supervisor, "_append_log", lambda message: None)

    count = supervisor.terminate_desktop_tree()

    assert count == 2
    assert root.terminated is True
    assert child.terminated is True


def test_supervisor_waits_for_relauncher_instead_of_double_starting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(supervisor._WindowsSingleton, "acquire", lambda self: True)
    monkeypatch.setattr(supervisor._WindowsSingleton, "release", lambda self: None)
    monkeypatch.setattr(supervisor, "_pythonw", lambda root: __file__)
    monkeypatch.setattr(supervisor, "_append_log", lambda message: None)
    monkeypatch.setattr(supervisor, "runtime_healthy", lambda root: False)
    monkeypatch.setattr(supervisor, "desktop_processes", lambda: [])
    monkeypatch.setattr(supervisor, "relauncher_processes", lambda: [_Proc(99)])
    monkeypatch.setattr(
        supervisor,
        "self_engineering_processes",
        lambda: [_Proc(77)],
    )
    monkeypatch.setattr(
        supervisor,
        "start_desktop",
        lambda root, reason: calls.append(reason),
    )
    monkeypatch.setattr(
        supervisor.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = supervisor.supervise(
        root=tmp_path,
        data_dir=tmp_path / "data-dev",
        auto_merge_low_risk=True,
        check_seconds=0.01,
        hang_failures=2,
    )

    assert result == 0
    assert calls == []


def test_open_diagnostics_is_fail_closed_off_windows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(supervisor.sys, "platform", "linux")
    monkeypatch.setattr(supervisor, "state_dir", lambda: tmp_path)

    assert supervisor.open_diagnostics() is False
    assert (tmp_path / supervisor.LOG_FILE_NAME).is_file()


def test_supervisor_starts_missing_self_engineering_on_first_loop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(supervisor._WindowsSingleton, "acquire", lambda self: True)
    monkeypatch.setattr(supervisor._WindowsSingleton, "release", lambda self: None)
    monkeypatch.setattr(supervisor, "_pythonw", lambda root: __file__)
    monkeypatch.setattr(supervisor, "_append_log", lambda message: None)
    monkeypatch.setattr(
        supervisor,
        "supervisor_stop_path",
        lambda: tmp_path / "no-stop-marker",
    )
    monkeypatch.setattr(supervisor, "runtime_healthy", lambda root: True)
    monkeypatch.setattr(supervisor, "self_engineering_processes", lambda: [])
    monkeypatch.setattr(
        supervisor,
        "start_self_engineering",
        lambda root, data_dir, auto_merge_low_risk, reason: calls.append(reason),
    )
    monkeypatch.setattr(
        supervisor.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = supervisor.supervise(
        root=tmp_path,
        data_dir=tmp_path / "data-dev",
        auto_merge_low_risk=True,
        check_seconds=0.01,
        hang_failures=2,
    )

    assert result == 0
    assert calls == ["missing"]


def test_terminate_supervisor_tree_stops_all_detected_supervisor_processes(
    monkeypatch,
) -> None:
    root = _Proc(40)
    child = _Proc(41)
    root._children = [child]
    monkeypatch.setattr(supervisor, "supervisor_processes", lambda: [root])
    monkeypatch.setattr(
        supervisor.psutil,
        "wait_procs",
        lambda targets, timeout: (targets, []),
    )
    monkeypatch.setattr(supervisor, "_append_log", lambda message: None)

    count = supervisor.terminate_supervisor_tree()

    assert count == 2
    assert root.terminated is True
    assert child.terminated is True
