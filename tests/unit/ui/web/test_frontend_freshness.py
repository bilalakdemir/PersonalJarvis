from __future__ import annotations

import json
from pathlib import Path

from jarvis.ui.web import frontend_freshness


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path
    frontend = repo / "jarvis/ui/web/frontend"
    dist = repo / "jarvis/ui/web/dist"
    marker = repo / "data-dev/frontend-build.json"
    (frontend / "src").mkdir(parents=True)
    dist.mkdir(parents=True)
    (frontend / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (frontend / "package-lock.json").write_text("lock", encoding="utf-8")
    (frontend / "src/App.tsx").write_text("export const app = 1;\n", encoding="utf-8")
    (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
    return repo, frontend, marker


def _write_matching_marker(repo: Path, frontend: Path, marker: Path) -> None:
    index = repo / "jarvis/ui/web/dist/index.html"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "schema": 1,
                "source_sha256": frontend_freshness.source_fingerprint(frontend),
                "index_sha256": frontend_freshness.file_sha256(index),
            }
        ),
        encoding="utf-8",
    )


def test_build_is_fresh_detects_source_and_dist_drift(tmp_path: Path) -> None:
    repo, frontend, marker = _layout(tmp_path)
    _write_matching_marker(repo, frontend, marker)

    assert frontend_freshness.build_is_fresh(repo, marker) is True

    (frontend / "src/App.tsx").write_text("export const app = 2;\n", encoding="utf-8")
    assert frontend_freshness.build_is_fresh(repo, marker) is False

    _write_matching_marker(repo, frontend, marker)
    (repo / "jarvis/ui/web/dist/index.html").write_text(
        "<!doctype html><html><body>old</body></html>",
        encoding="utf-8",
    )
    assert frontend_freshness.build_is_fresh(repo, marker) is False


def test_ensure_frontend_build_rebuilds_stale_bundle_and_records_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, frontend, marker = _layout(tmp_path)
    (frontend / "node_modules").mkdir()
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(frontend_freshness, "_npm_executable", lambda: "npm")

    def fake_run(command, *, cwd, check):
        assert cwd == frontend
        assert check is True
        calls.append(tuple(command))
        (repo / "jarvis/ui/web/dist/index.html").write_text(
            "<!doctype html><html><body>fresh</body></html>",
            encoding="utf-8",
        )

    monkeypatch.setattr(frontend_freshness.subprocess, "run", fake_run)

    rebuilt = frontend_freshness.ensure_frontend_build(repo, marker)

    assert rebuilt is True
    assert calls == [("npm", "run", "build")]
    assert marker.is_file()
    assert frontend_freshness.build_is_fresh(repo, marker) is True

    calls.clear()
    assert frontend_freshness.ensure_frontend_build(repo, marker) is False
    assert calls == []
