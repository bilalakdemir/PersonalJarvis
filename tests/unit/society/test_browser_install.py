"""Managed installation failures must preserve the previously verified runtime."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jarvis.society.browser import bootstrap, install


@pytest.fixture
def installer(monkeypatch, tmp_path):
    root = install.install_root(tmp_path)
    launches = []
    failure = {"stage": ""}

    def run(cmd, **kwargs):
        if "venv" in cmd:
            runtime = Path(cmd[-1])
            executable = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test runtime")
            launches.append(runtime)
        if failure["stage"] and failure["stage"] in cmd:
            raise RuntimeError("Interrupted test download")
        if "--probe" in cmd:
            binary = root / "browsers" / "chrome"
            binary.parent.mkdir(exist_ok=True)
            binary.write_bytes(b"test browser")
            return json.dumps(
                {
                    "kind": "probe",
                    "ok": True,
                    "executable": str(binary),
                    "version": "test",
                    "packages": [{"name": "test", "license": "MIT"}],
                }
            )
        return ""

    monkeypatch.setattr(install, "_run", run)
    monkeypatch.setattr(install, "managed_python_request", lambda *_: "3.12")
    monkeypatch.setattr(install.sys, "frozen", False, raising=False)
    monkeypatch.setattr(bootstrap, "ensure_uv", lambda *_: "uv")
    yield tmp_path, root, launches, failure
    install._reset_for_tests()


@pytest.mark.parametrize("stage", ["ensurepip", "pip", "playwright", "--probe"])
def test_failed_repair_keeps_the_verified_environment(installer, stage):
    data, root, launches, failure = installer
    install.ensure_installed(data)
    original = (root / "installed.json").read_bytes()
    previous_python = install.venv_python(data)
    failure["stage"] = stage
    with pytest.raises(RuntimeError, match="Interrupted"):
        install.ensure_installed(data, repair=True)
    assert (root / "installed.json").read_bytes() == original
    assert install.venv_python(data) == previous_python
    assert install.is_installed(data)
    assert install.snapshot(data)["retry_at"] > 0
    failure["stage"] = ""
    install.ensure_installed(data, repair=True)
    assert install.venv_python(data) != previous_python
    assert previous_python.is_file()
    assert install.is_installed(data)


def test_simultaneous_installers_promote_only_one_runtime(installer):
    data, root, launches, _ = installer
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(install.ensure_installed, [data, data]))
    assert all(row["installed"] for row in outcomes)
    assert len(launches) == 1


def test_missing_binary_and_corrupt_marker_trigger_repair(installer):
    data, root, launches, _ = installer
    install.ensure_installed(data)
    install.browser_executable(data).unlink()
    assert not install.is_installed(data)
    install.ensure_installed(data)
    assert install.is_installed(data)
    (root / "installed.json").write_text("{partial", encoding="utf-8")
    assert not install.is_installed(data)
    install.ensure_installed(data)
    assert install.is_installed(data)
    assert len(launches) == 3


def test_lock_identity_survives_platform_line_endings(installer, monkeypatch, tmp_path):
    data, root, _, _ = installer
    install.ensure_installed(data)
    original = install.requirements_path().read_bytes().replace(b"\r\n", b"\n")
    copy = tmp_path / "requirements.lock"
    copy.write_bytes(original.replace(b"\n", b"\r\n"))
    monkeypatch.setattr(install, "requirements_path", lambda: copy)
    assert install.is_installed(data)
    copy.write_bytes(original + b"# changed dependency manifest\n")
    assert not install.is_installed(data)


def test_browser_candidate_order_prefers_brave_before_chrome() -> None:
    brave_windows, chrome_windows = install._system_browser_paths(  # noqa: SLF001
        "win32",
        {
            "ProgramFiles": r"C:\Program Files",
            "ProgramFiles(x86)": r"C:\Program Files (x86)",
            "LOCALAPPDATA": r"C:\Users\test\AppData\Local",
        },
    )
    assert "BraveSoftware" in str(brave_windows[0])
    assert "BraveSoftware" in str(brave_windows[1])
    assert "Google" in str(chrome_windows[0])

    brave_mac, chrome_mac = install._system_browser_paths(  # noqa: SLF001
        "darwin", {}
    )
    assert "Brave Browser.app" in str(brave_mac[0])
    assert "Google Chrome.app" in str(chrome_mac[0])


def test_system_browser_path_lookup_prefers_brave(monkeypatch) -> None:
    found = {
        "brave-browser": "/opt/brave/brave",
        "google-chrome": "/opt/google/chrome",
    }
    monkeypatch.setattr(install.shutil, "which", lambda name: found.get(name))
    monkeypatch.setattr(install, "_system_browser_paths", lambda: ((), ()))

    assert install.system_browser_executable() == Path("/opt/brave/brave")


def test_brave_install_path_beats_chrome_on_path(monkeypatch, tmp_path: Path) -> None:
    brave = tmp_path / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
    brave.parent.mkdir(parents=True)
    brave.write_bytes(b"brave")
    chrome = tmp_path / "google-chrome"

    monkeypatch.setattr(
        install.shutil,
        "which",
        lambda name: str(chrome) if name == "google-chrome" else None,
    )
    monkeypatch.setattr(install, "_system_browser_paths", lambda: ((brave,), ()))

    assert install.system_browser_executable() == brave


def test_preferred_browser_falls_back_to_managed_chromium(monkeypatch, tmp_path: Path) -> None:
    managed = tmp_path / "managed-chromium"
    monkeypatch.setattr(install, "system_browser_executable", lambda: None)
    monkeypatch.setattr(install, "browser_executable", lambda *_: managed)

    assert install.preferred_browser_executable(tmp_path) == managed


def test_python311_host_uses_managed_python312_runtime(monkeypatch, tmp_path: Path) -> None:
    root = install.install_root(tmp_path)
    commands: list[list[str]] = []

    def run(cmd, **kwargs):
        commands.append([str(part) for part in cmd])
        if "venv" in cmd:
            runtime = Path(cmd[-1])
            executable = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test runtime")
        if "--probe" in cmd:
            binary = root / "browsers" / "chrome"
            binary.parent.mkdir(exist_ok=True)
            binary.write_bytes(b"test browser")
            return json.dumps(
                {
                    "kind": "probe",
                    "ok": True,
                    "executable": str(binary),
                    "version": "test",
                    "packages": [],
                }
            )
        return ""

    monkeypatch.setattr(install, "_run", run)
    monkeypatch.setattr(install, "managed_python_request", lambda *_: "3.12")
    monkeypatch.setattr(install.sys, "version_info", (3, 11, 9))
    monkeypatch.setattr(install.sys, "frozen", False, raising=False)
    monkeypatch.setattr(bootstrap, "ensure_uv", lambda *_: "uv")

    install.ensure_installed(tmp_path)

    first_venv = next(command for command in commands if "venv" in command)
    assert first_venv[:4] == ["uv", "venv", "--python", "3.12"]
    assert str(root / "runtimes") in first_venv[-1]
