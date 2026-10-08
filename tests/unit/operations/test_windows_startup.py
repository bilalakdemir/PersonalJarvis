from __future__ import annotations

import subprocess
from pathlib import Path

import jarvis.operations.windows_startup as startup


def test_expected_supervisor_command_targets_repo_module(tmp_path: Path) -> None:
    program, args, working_dir = startup._expected(tmp_path)

    assert program
    assert "-m jarvis.operations.supervisor" in args
    assert f'--repo-root "{tmp_path}"' in args
    assert f'--data-dir "{tmp_path / "data-dev"}"' in args
    assert "--auto-merge-low-risk" in args
    assert working_dir == str(tmp_path)


def test_status_reports_matching_windows_shortcut(
    tmp_path: Path,
    monkeypatch,
) -> None:
    shortcut = tmp_path / startup.SHORTCUT_NAME
    shortcut.write_bytes(b"x")
    monkeypatch.setattr(startup.sys, "platform", "win32")
    monkeypatch.setattr(startup, "supervisor_shortcut_path", lambda: shortcut)
    program, args, working_dir = startup._expected(tmp_path)
    monkeypatch.setattr(
        startup,
        "_probe",
        lambda path: {
            "TargetPath": program,
            "Arguments": args,
            "WorkingDirectory": working_dir,
        },
    )

    status = startup.supervisor_startup_status(tmp_path)

    assert status.supported is True
    assert status.installed is True
    assert status.matches is True


def test_status_rejects_shortcut_for_different_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    shortcut = tmp_path / startup.SHORTCUT_NAME
    shortcut.write_bytes(b"x")
    monkeypatch.setattr(startup.sys, "platform", "win32")
    monkeypatch.setattr(startup, "supervisor_shortcut_path", lambda: shortcut)
    monkeypatch.setattr(
        startup,
        "_probe",
        lambda path: {
            "TargetPath": "C:/other/pythonw.exe",
            "Arguments": "-m jarvis.operations.supervisor",
            "WorkingDirectory": "C:/other/repo",
        },
    )

    status = startup.supervisor_startup_status(tmp_path)

    assert status.installed is True
    assert status.matches is False


def test_install_writes_shortcut_without_uac(
    tmp_path: Path,
    monkeypatch,
) -> None:
    shortcut = tmp_path / "Startup" / startup.SHORTCUT_NAME
    monkeypatch.setattr(startup.sys, "platform", "win32")
    monkeypatch.setattr(startup, "supervisor_shortcut_path", lambda: shortcut)
    scripts: list[str] = []

    def fake_run(script: str):
        scripts.append(script)
        return subprocess.CompletedProcess(["powershell.exe"], 0, "", "")

    monkeypatch.setattr(startup, "_run_powershell", fake_run)
    program, args, working_dir = startup._expected(tmp_path)

    def fake_probe(path: Path):
        assert path == shortcut
        return {
            "TargetPath": program,
            "Arguments": args,
            "WorkingDirectory": working_dir,
        }

    monkeypatch.setattr(startup, "_probe", fake_probe)

    status = startup.install_supervisor_startup(tmp_path)

    assert status.matches is True
    assert shortcut.parent.is_dir()
    assert scripts
    assert "CreateShortcut" in scripts[0]
    assert "runas" not in scripts[0].casefold()


def test_migration_removes_legacy_script_only_after_matching_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy = tmp_path / startup.LEGACY_SCRIPT_NAME
    legacy.write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(startup, "legacy_supervisor_script_path", lambda: legacy)
    monkeypatch.setattr(
        startup,
        "install_supervisor_startup",
        lambda root: startup.SupervisorStartupStatus(
            True, True, True, "entry", "ok"
        ),
    )
    monkeypatch.setattr(
        startup,
        "supervisor_startup_status",
        lambda root: startup.SupervisorStartupStatus(
            True, True, True, "entry", "ok"
        ),
    )

    status = startup.migrate_legacy_supervisor(tmp_path)

    assert status.matches is True
    assert not legacy.exists()


def test_nonwindows_status_is_honestly_unsupported(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(startup.sys, "platform", "linux")

    status = startup.supervisor_startup_status(tmp_path)

    assert status.supported is False
    assert status.installed is False
    assert status.matches is False
