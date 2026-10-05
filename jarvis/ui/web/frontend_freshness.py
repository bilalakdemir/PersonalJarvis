"""Keep the built desktop frontend aligned with its source inputs.

The desktop dev launcher serves the production-style static bundle from
jarvis/ui/web/dist. A Git pull can update React source without rebuilding that
bundle, leaving a current Python backend paired with an older HUD.

This module fingerprints build inputs, verifies the current dist bundle, and
rebuilds only when source and dist have drifted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from .spa_build import build_is_complete

_MARKER_SCHEMA: Final[int] = 1
_FRONTEND_REL: Final[Path] = Path("jarvis/ui/web/frontend")
_DIST_REL: Final[Path] = Path("jarvis/ui/web/dist")
_BUILD_INPUT_FILES: Final[tuple[str, ...]] = (
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "postcss.config.js",
    "tailwind.config.js",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
)
_BUILD_INPUT_DIRS: Final[tuple[str, ...]] = ("src", "public")


def _iter_build_inputs(frontend_dir: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for name in _BUILD_INPUT_FILES:
        candidate = frontend_dir / name
        if candidate.is_file():
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield candidate
    for name in _BUILD_INPUT_DIRS:
        root = frontend_dir / name
        if not root.is_dir():
            continue
        for candidate in sorted(path for path in root.rglob("*") if path.is_file()):
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield candidate


def source_fingerprint(frontend_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(_iter_build_inputs(frontend_dir), key=lambda item: item.as_posix()):
        relative = path.relative_to(frontend_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_marker(marker_path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != _MARKER_SCHEMA:
        return None
    return payload


def build_is_fresh(repo_root: Path, marker_path: Path) -> bool:
    frontend_dir = repo_root / _FRONTEND_REL
    dist_dir = repo_root / _DIST_REL
    index_file = dist_dir / "index.html"
    if not build_is_complete(index_file, dist_dir):
        return False
    marker = _read_marker(marker_path)
    if marker is None:
        return False
    try:
        current_source = source_fingerprint(frontend_dir)
        current_index = file_sha256(index_file)
    except OSError:
        return False
    return (
        marker.get("source_sha256") == current_source
        and marker.get("index_sha256") == current_index
    )


def _npm_executable() -> str | None:
    return shutil.which("npm") or shutil.which("npm.cmd")


def _write_marker(repo_root: Path, marker_path: Path) -> None:
    frontend_dir = repo_root / _FRONTEND_REL
    index_file = repo_root / _DIST_REL / "index.html"
    payload = {
        "schema": _MARKER_SCHEMA,
        "source_sha256": source_fingerprint(frontend_dir),
        "index_sha256": file_sha256(index_file),
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def ensure_frontend_build(repo_root: Path, marker_path: Path) -> bool:
    repo_root = repo_root.resolve()
    marker_path = marker_path if marker_path.is_absolute() else repo_root / marker_path
    frontend_dir = repo_root / _FRONTEND_REL
    dist_dir = repo_root / _DIST_REL
    index_file = dist_dir / "index.html"

    if build_is_fresh(repo_root, marker_path):
        print("frontend-build: fresh; reusing existing dist")
        return False

    npm = _npm_executable()
    if npm is None:
        raise RuntimeError("frontend build is stale but npm is not available on PATH")

    if not (frontend_dir / "node_modules").is_dir():
        print("frontend-build: dependencies missing; running npm ci")
        subprocess.run([npm, "ci"], cwd=frontend_dir, check=True)

    print("frontend-build: source/dist drift detected; rebuilding HUD")
    subprocess.run([npm, "run", "build"], cwd=frontend_dir, check=True)

    if not build_is_complete(index_file, dist_dir):
        raise RuntimeError("frontend build completed but dist is incomplete")

    _write_marker(repo_root, marker_path)
    print("frontend-build: rebuilt and fingerprinted")
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--marker", type=Path, default=Path("data-dev/frontend-build.json"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    ensure_frontend_build(args.repo_root, args.marker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
