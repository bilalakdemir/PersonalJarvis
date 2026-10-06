"""AERION DEV self-engineering sidecar.

Watches canonical HUD failures, gives a fresh isolated worktree to the user's
Codex ChatGPT-login CLI, validates the repair, independently verifies it, and
opens a PR against develop. It never uses OPENAI_API_KEY and never edits the
main checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

LOG = logging.getLogger("aerion.self_engineering")
SMOKE = (
    "tests/unit/brain/test_routing.py",
    "tests/unit/brain/test_output_filter.py",
    "tests/unit/sessions/test_hangup_reason_parity.py",
    "tests/unit/core/test_turn_language.py",
)
HIGH = (".github/workflows/", "install/", "packaging/", "jarvis/safety/")
MEDIUM = ("jarvis/core/", "jarvis/brain/", "jarvis/memory/", "jarvis/ui/")
BLOCKED = {"AGENTS.md", "CLAUDE.md", "jarvis.toml", "PROJECT.md", "STATE.md",
           "TASKS.md", "DECISIONS.md", "BACKLOG.md", "jarvis/core/config_writer.py"}
SECRET = re.compile(r"(?i)\b(?:nvapi-|sk-|gh[pousr]_)[A-Za-z0-9_-]{8,}|\bBearer\s+\S+")


def redact(text: str) -> str:
    return SECRET.sub("[REDACTED]", text)


def failed(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = snapshot.get("recent_outputs")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)
            and str(r.get("status") or "").lower() == "failed"
            and str(r.get("activity_id") or "").strip()]


def risk(paths: Iterable[str]) -> str:
    paths = [str(p).replace("\\", "/").lstrip("./") for p in paths]
    if any(p in BLOCKED or any(p.startswith(x) for x in HIGH) or "credential" in p.lower()
           for p in paths):
        return "HIGH"
    product = [p for p in paths if not p.startswith("tests/")]
    if len(paths) > 8 or len(product) > 4 or any(any(p.startswith(x) for x in MEDIUM) for p in paths):
        return "MEDIUM"
    return "LOW"


def verdict(text: str, floor: str) -> tuple[str, str]:
    vm = re.search(r"(?i)VERDICT\s*:\s*(PASS|FAIL|NEEDS_HUMAN)", text)
    rm = re.search(r"(?i)RISK\s*:\s*(LOW|MEDIUM|HIGH)", text)
    v = vm.group(1).upper() if vm else "NEEDS_HUMAN"
    r = rm.group(1).upper() if rm else floor
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return v, floor if order[r] < order[floor] else r


def _run(cmd: list[str], cwd: Path, *, stdin: str | None = None, timeout: float = 300,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        cmd, cwd=cwd, input=stdin, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=timeout, env=env,
        creationflags=NO_WINDOW_CREATIONFLAGS if os.name == "nt" else 0,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], repo)


def _port(repo: Path) -> int:
    base = 47821
    raw = os.environ.get("JARVIS__UI__ADMIN_API_PORT", "")
    if raw.isdigit():
        base = int(raw)
    else:
        try:
            data = tomllib.loads((repo / "jarvis.toml").read_text(encoding="utf-8-sig"))
            value = data.get("ui", {}).get("admin_api_port")
            if isinstance(value, int) and not isinstance(value, bool):
                base = value
        except (OSError, ValueError):
            pass
    return base + 100


def _snapshot(url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - loopback only
            value = json.loads(response.read().decode())
            return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {"bootstrapped": False, "seen": []}


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="state-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _codex(worktree: Path, prompt: str, sandbox: str) -> subprocess.CompletedProcess[str]:
    from jarvis.codex_auth import CodexAuthService
    from jarvis.plugins.brain.codex import _ensure_node_reachable

    service = CodexAuthService()
    status = service.status()
    if not (status.connected and status.mode == "chatgpt"):
        raise RuntimeError("Codex ChatGPT login is not connected")
    binary = service._resolve_binary()
    if not binary:
        raise RuntimeError("Codex CLI is unavailable")
    env = {k: v for k, v in os.environ.items() if k not in {"OPENAI_API_KEY", "CODEX_HOME"}}
    _ensure_node_reachable(env, binary)
    return _run([binary, "exec", "--json", "--ephemeral", "--skip-git-repo-check",
                 "--sandbox", sandbox, "-c", "approval_policy=never"],
                worktree, stdin=prompt, timeout=1200, env=env)


def _changed(worktree: Path) -> list[str]:
    out = []
    for args in (("diff", "--name-only"), ("ls-files", "--others", "--exclude-standard")):
        out += _git(worktree, *args).stdout.splitlines()
    return sorted({x.strip().replace("\\", "/") for x in out if x.strip()})


def _tests(worktree: Path, changed: list[str]) -> tuple[bool, tuple[str, ...], str]:
    explicit = tuple(p for p in changed if p.startswith("tests/") and p.endswith(".py"))
    selected = explicit or SMOKE
    run = _run([sys.executable, "-m", "pytest", *selected, "-q", "-p", "no:cacheprovider"],
               worktree, timeout=1800)
    gates = _run([sys.executable, "scripts/ci/run_gates.py", "--base", "origin/develop", "--pr"],
                 worktree, timeout=1200)
    output = (run.stdout + run.stderr + "\n--- gates ---\n" + gates.stdout + gates.stderr)[-20000:]
    return run.returncode == 0 and gates.returncode == 0, selected, output


def _engineer_prompt(row: dict[str, Any], logs: str) -> str:
    return f"""Read AGENTS.md first. You are the AERION repair worker in an isolated worktree.
Failure: {redact(str(row.get('label') or 'failure'))}
Detail: {redact(str(row.get('detail') or ''))}
Trace: {row.get('trace_id') or ''}
Runtime evidence:\n{logs[-16000:]}

Find the evidenced root cause. If it is external/transient and already handled correctly, change nothing.
Otherwise make the smallest bounded fix plus a regression test and run focused tests.
Never run git add/commit/branch/checkout/push/merge/rebase/reset/clean/stash.
Never edit governance files, jarvis.toml, credentials, secrets or user data. Never use paid API keys.
Finish with OUTCOME: FIXED | NO_CODE_CHANGE | NEEDS_HUMAN.
"""


def _verify_prompt(row: dict[str, Any], floor: str, tests: tuple[str, ...]) -> str:
    return f"""Read AGENTS.md. You are an independent READ-ONLY verifier. Inspect git diff/status.
Original failure: {redact(str(row.get('detail') or ''))}
Risk floor: {floor}. Tests run: {', '.join(tests)}.
Judge root-cause fit, regression coverage, scope, and safety. Do not edit.
Finish exactly with:\nVERDICT: PASS | FAIL | NEEDS_HUMAN\nRISK: LOW | MEDIUM | HIGH\nREASON: <brief>\n"""


def _incident(repo: Path, data: Path, row: dict[str, Any], auto_merge: bool) -> dict[str, Any]:
    activity = str(row["activity_id"])
    stamp = int(time.time())
    fp = hashlib.sha256((activity + str(row.get("detail"))).encode()).hexdigest()[:8]
    ident = f"{stamp}-{fp}"
    branch = f"agent/aerion-{ident}"
    worktree = repo.parent / ".aerion-worktrees" / ident
    store = data / "engineering" / "incidents" / ident
    store.mkdir(parents=True, exist_ok=True)
    _git(repo, "fetch", "--quiet", "origin", "develop")
    add = _git(repo, "worktree", "add", "-b", branch, str(worktree), "origin/develop")
    if add.returncode:
        raise RuntimeError(add.stderr[-1000:])
    log_path = data / "jarvis_desktop.log"
    logs = redact(log_path.read_text(encoding="utf-8", errors="replace")[-30000:]) if log_path.exists() else ""
    worker = _codex(worktree, _engineer_prompt(row, logs), "workspace-write")
    (store / "worker.txt").write_text(redact(worker.stdout + worker.stderr), encoding="utf-8")
    if worker.returncode:
        return {"status": "worker_failed", "branch": branch}
    changed = _changed(worktree)
    if not changed:
        return {"status": "no_code_change", "branch": branch}
    floor = risk(changed)
    ok, tests, test_output = _tests(worktree, changed)
    (store / "validation.txt").write_text(test_output, encoding="utf-8")
    if not ok:
        return {"status": "validation_failed", "branch": branch, "changed": changed}
    check = _codex(worktree, _verify_prompt(row, floor, tests), "read-only")
    raw = redact(check.stdout + check.stderr)
    (store / "verifier.txt").write_text(raw, encoding="utf-8")
    decision, final_risk = verdict(raw, floor)
    if check.returncode or decision != "PASS":
        return {"status": "needs_human", "branch": branch, "risk": final_risk, "changed": changed}
    for path in changed:
        if _git(worktree, "add", "--", path).returncode:
            raise RuntimeError(f"could not stage {path}")
    commit = _git(worktree, "commit", "-m", "fix: automated AERION incident repair", "--", *changed)
    if commit.returncode:
        raise RuntimeError(commit.stderr[-1000:])
    push = _git(worktree, "push", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}")
    if push.returncode:
        raise RuntimeError(push.stderr[-1000:])
    gh = shutil.which("gh")
    if not gh:
        return {"status": "branch_pushed", "branch": branch, "risk": final_risk}
    body = store / "pr.md"
    body.write_text(f"Automated AERION repair for `{activity}`.\n\nRisk: **{final_risk}**.\n", encoding="utf-8")
    cmd = [gh, "pr", "create", "--base", "develop", "--head", branch,
           "--title", f"fix: AERION self-heal {row.get('label') or 'failure'}", "--body-file", str(body)]
    if final_risk == "HIGH":
        cmd.append("--draft")
    pr = _run(cmd, worktree)
    if pr.returncode:
        return {"status": "branch_pushed", "branch": branch, "risk": final_risk}
    url = next((x for x in pr.stdout.splitlines() if x.startswith("http")), pr.stdout.strip())
    merged = False
    if auto_merge and final_risk == "LOW":
        merged = _run([gh, "pr", "merge", "--auto", "--squash", branch], worktree).returncode == 0
    return {"status": "pr_opened", "branch": branch, "risk": final_risk, "pr_url": url,
            "auto_merge": merged, "changed": changed}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=str(Path.cwd()))
    p.add_argument("--data-dir", default="data-dev")
    p.add_argument("--poll", type=float, default=5)
    p.add_argument("--once", action="store_true")
    p.add_argument("--auto-merge-low-risk", action="store_true")
    args = p.parse_args(argv)
    repo, data = Path(args.repo_root).resolve(), Path(args.data_dir).resolve()
    eng = data / "engineering"; eng.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=eng / "supervisor.log", level=logging.INFO, encoding="utf-8")
    state_path = eng / "state.json"; state = _state(state_path)
    url = f"http://127.0.0.1:{_port(repo)}/api/hud/snapshot"
    while True:
        snap = _snapshot(url)
        if snap is None:
            if args.once: return 3
            time.sleep(args.poll); continue
        rows = failed(snap); ids = [str(x["activity_id"]) for x in rows]
        if not state.get("bootstrapped"):
            state.update(bootstrapped=True, seen=ids); _save(state_path, state)
        else:
            seen = set(state.get("seen", []))
            for row in rows:
                aid = str(row["activity_id"])
                if aid in seen: continue
                seen.add(aid); state["seen"] = sorted(seen)[-500:]; _save(state_path, state)
                try:
                    state["last_incident"] = _incident(repo, data, row, args.auto_merge_low_risk)
                except Exception as exc:  # noqa: BLE001 - survive one failed repair
                    LOG.exception("self-engineering incident failed")
                    state["last_incident"] = {"status": "supervisor_failed", "reason": redact(str(exc))[:1200]}
                _save(state_path, state)
        if args.once: return 0
        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
