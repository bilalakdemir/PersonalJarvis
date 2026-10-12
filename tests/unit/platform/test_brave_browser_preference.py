"""AERION browser preference: Brave wins; exclusive mode never uses Edge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jarvis.platform.open_path as op
from jarvis.platform.capabilities import Capabilities


def _caps() -> Capabilities:
    return Capabilities(
        platform="linux",
        has_hotkey=False,
        has_ax_tree=False,
        has_overlay=False,
        has_pty=False,
        has_elevation=False,
        has_cursor=False,
        display_present=True,
        is_wayland=False,
        has_webview=False,
        ax_permission_granted=None,
    )


def test_brave_only_windows_ignores_default_edge(monkeypatch, tmp_path):
    brave = tmp_path / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
    brave.parent.mkdir(parents=True)
    brave.write_bytes(b"test")
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"test")
    monkeypatch.setenv("JARVIS_BROWSER_ONLY", "brave")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with patch.object(op, "_windows_default_browser_exe", return_value=str(edge)):
        assert op._windows_browser_candidates() == [str(brave)]


def test_brave_only_windows_never_falls_back_to_edge(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_BROWSER_ONLY", "brave")
    with (
        patch.object(op, "detect_capabilities", return_value=_caps()),
        patch.object(op, "detect_platform", return_value="win32"),
        patch.object(op, "_windows_browser_candidates", return_value=[str(tmp_path / "brave.exe")]),
        patch.object(op.subprocess, "Popen", side_effect=OSError("unavailable")) as popen,
        patch.object(op.os, "startfile", create=True) as startfile,
    ):
        assert op.open_url("http://localhost:8765/") is False
        popen.assert_called_once()
        startfile.assert_not_called()


def test_brave_only_macos_launches_brave_not_default(monkeypatch):
    monkeypatch.setenv("JARVIS_BROWSER_ONLY", "brave")
    with (
        patch.object(op, "detect_capabilities", return_value=_caps()),
        patch.object(op, "detect_platform", return_value="darwin"),
        patch.object(op.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as runner,
    ):
        assert op.open_url("https://example.com") is True
        runner.assert_called_once()
        assert runner.call_args.args[0] == ["open", "-a", "Brave Browser", "https://example.com"]


def test_brave_only_linux_missing_browser_blocks_default(monkeypatch):
    monkeypatch.setenv("JARVIS_BROWSER_ONLY", "brave")
    with (
        patch.object(op, "detect_capabilities", return_value=_caps()),
        patch.object(op, "detect_platform", return_value="linux"),
        patch.object(op.shutil, "which", return_value=None),
        patch.object(op.subprocess, "run") as run,
        patch.object(op.subprocess, "Popen") as popen,
    ):
        assert op.open_url("https://example.com") is False
        run.assert_not_called()
        popen.assert_not_called()


def test_brave_first_even_without_policy(monkeypatch, tmp_path: Path):
    brave = tmp_path / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
    brave.parent.mkdir(parents=True)
    brave.write_bytes(b"test")
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"test")
    monkeypatch.delenv("JARVIS_BROWSER_ONLY", raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with patch.object(op, "_windows_default_browser_exe", return_value=str(edge)):
        candidates = op._windows_browser_candidates()
    assert candidates[0] == str(brave)
    assert str(edge) in candidates
