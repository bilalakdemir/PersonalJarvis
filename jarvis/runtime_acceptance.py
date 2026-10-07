"""Isolated one-turn runtime acceptance for self-engineered repairs.

The controller boots the patched worktree with fresh data/config/thread state,
executes exactly one narrowly-derived turn, records tool routing, and always
reaps the spawned runtime tree. Unsupported incidents degrade honestly instead
of inventing an end-to-end test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit
from uuid import uuid4

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.redact import redact_secrets

LOG = logging.getLogger(__name__)

_PAGE_TITLE_RE = re.compile(
    r"\b(?:page\s+title|title\s+(?:of|for)\s+(?:the\s+)?page)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_TRANSIENT_RUNTIME_MARKERS = (
    "usage limit",
    "rate limit",
    "temporarily unavailable",
    "temporary unavailable",
    "service temporarily overloaded",
    "resourceexhausted",
    "resource exhausted",
    "worker local total request limit reached",
    "capacity",
    "overloaded",
    "too many requests",
)
_ACCEPTANCE_ALLOWED_TOOLS_ENV = "JARVIS_RUNTIME_ACCEPTANCE_ALLOWED_TOOLS"
_ACCEPTANCE_TOOL_LOG_ENV = "JARVIS_RUNTIME_ACCEPTANCE_TOOL_LOG"


class RuntimeAcceptanceStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    TRANSIENT = "transient"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RuntimeAcceptanceSpec:
    profile: str
    prompt: str
    allowed_tools: frozenset[str]
    required_tools: frozenset[str]
    forbidden_tools: frozenset[str]
    timeout_s: float = 120.0


@dataclass(frozen=True, slots=True)
class RuntimeAcceptanceResult:
    status: RuntimeAcceptanceStatus
    reason: str
    final_text: str
    actions: tuple[dict[str, Any], ...]
    thread_id: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass(frozen=True, slots=True)
class _ExchangeResult:
    final_text: str
    actions: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]


def _safe_http_url(raw: str) -> str | None:
    candidate = raw.rstrip(".,!?;:)]}")
    try:
        parsed = urlsplit(candidate)
    except ValueError:  # Malformed untrusted URLs are unsupported evidence.
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.hostname or parsed.username or parsed.password:
        return None
    return candidate


def derive_acceptance_spec(
    rows: Iterable[dict[str, Any]],
) -> RuntimeAcceptanceSpec | None:
    """Derive only a strict, side-effect-bounded acceptance profile.

    Runtime evidence is untrusted. We never replay it verbatim: once a known
    browser/page-title intent is recognized, the acceptance turn is rebuilt
    against the fixed public example.com fixture.
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        request_detail = row.get("request_detail")
        if not isinstance(request_detail, str):
            continue
        text = request_detail.strip()
        if not text or len(text) > 1000 or "\x00" in text:
            continue
        if not _PAGE_TITLE_RE.search(text):
            continue
        match = _URL_RE.search(text)
        if match is None:
            continue
        url = _safe_http_url(match.group(0))
        if url is None:
            continue
        # The incident URL is only an intent discriminator. Replaying arbitrary
        # untrusted URLs from failure evidence could turn a verifier into an SSRF
        # primitive, so the v1 routing proof always uses the public inert fixture.
        return RuntimeAcceptanceSpec(
            profile="browser_page_title",
            prompt=(
                "Open the isolated browser and go to https://example.com. "
                "Tell me the page title."
            ),
            allowed_tools=frozenset({"society_browser", "society_browser_action"}),
            required_tools=frozenset({"society_browser"}),
            forbidden_tools=frozenset({"computer_use", "dispatch_to_harness"}),
        )
    return None


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _python_console(executable: str | None = None) -> str:
    exe = Path(executable or sys.executable)
    if os.name == "nt" and exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.is_file():
            return str(candidate)
    return str(exe)


def _prepare_config(repo: Path, run_dir: Path, *, port: int) -> Path:
    """Write a minimal acceptance config; never copy the user's full config."""
    source = repo / "jarvis.toml"
    brain_source: dict[str, Any] = {}
    if source.is_file():
        try:
            loaded = tomllib.loads(source.read_text(encoding="utf-8-sig"))
            candidate = loaded.get("brain")
            if isinstance(candidate, dict):
                brain_source = candidate
        except (OSError, ValueError) as exc:
            LOG.warning("runtime acceptance could not read provider selection: %s", exc)

    def _selected_string(key: str, default: str = "") -> str:
        value = brain_source.get(key)
        return str(value).strip() if isinstance(value, str) else default

    primary = (
        _selected_string("primary")
        or os.environ.get("JARVIS__BRAIN__PRIMARY", "").strip()
        or "claude-api"
    )
    routing_provider = _selected_string("routing_provider", primary) or primary
    local_fallback = _selected_string("local_fallback", primary) or primary

    # JSON string quoting is valid TOML basic-string syntax for these bounded
    # provider/model identifiers and avoids hand-rolled escaping.
    lines = [
        "[brain]",
        f"primary = {json.dumps(primary)}",
        f"routing_provider = {json.dumps(routing_provider)}",
        f"local_fallback = {json.dumps(local_fallback)}",
        "healthcheck_on_start = false",
    ]
    for key in ("routing_model", "local_fallback_model", "reply_language"):
        value = _selected_string(key)
        if value:
            lines.append(f"{key} = {json.dumps(value)}")

    tool_source = brain_source.get("tool_model")
    if not isinstance(tool_source, dict):
        tool_source = brain_source.get("computer_use")
    if isinstance(tool_source, dict):
        safe_tool = {
            key: value
            for key, value in tool_source.items()
            if key in {"provider", "model", "fallback_provider", "fallback_model"}
            and isinstance(value, str)
            and value.strip()
        }
        if safe_tool:
            lines.extend(["", "[brain.tool_model]"])
            for key, value in safe_tool.items():
                lines.append(f"{key} = {json.dumps(value.strip())}")

    lines.extend(
        [
            "",
            "[ack_brain]",
            "enabled = false",
            "",
            "[tts]",
            'provider = "none"',
            "",
            "[ui]",
            f"admin_api_port = {int(port)}",
            "",
        ]
    )
    target = run_dir / "acceptance.toml"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _browser_install_compatible(
    worktree: Path,
    data_dir: Path,
    *,
    python_executable: str | None,
) -> bool:
    """Ask the patched browser installer to attest an existing runtime cache."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(worktree)
    probe = subprocess.run(  # noqa: S603 - fixed local Python probe
        [
            _python_console(python_executable),
            "-c",
            (
                "from pathlib import Path; "
                "from jarvis.society.browser.install import is_installed; "
                "import sys; "
                "print('1' if is_installed(Path(sys.argv[1])) else '0')"
            ),
            str(data_dir),
        ],
        cwd=worktree,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
        creationflags=NO_WINDOW_CREATIONFLAGS if os.name == "nt" else 0,
    )
    return probe.returncode == 0 and probe.stdout.strip().endswith("1")


def _candidate_browser_data_dirs(repo: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    env_data = os.environ.get("JARVIS_DATA_DIR")
    if env_data:
        candidates.append(Path(env_data).resolve())
    candidates.extend(((repo / "data-dev").resolve(), (repo / "data").resolve()))
    return tuple(dict.fromkeys(candidates))


def _attach_shared_browser_install(
    worktree: Path,
    repo: Path,
    run_dir: Path,
    *,
    python_executable: str | None,
) -> Path | None:
    """Link only a patched-code-compatible immutable browser install cache."""
    target = run_dir / "data" / "society" / "browser"
    for data_dir in _candidate_browser_data_dirs(repo):
        source = data_dir / "society" / "browser"
        if not (source / "installed.json").is_file():
            continue
        try:
            compatible = _browser_install_compatible(
                worktree,
                data_dir,
                python_executable=python_executable,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOG.warning("runtime acceptance browser-cache probe failed: %s", exc)
            continue
        if not compatible:
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            linked = subprocess.run(  # noqa: S603 - fixed cmd built-in for a local junction
                ["cmd", "/d", "/c", "mklink", "/J", str(target), str(source)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            if linked.returncode:
                LOG.warning(
                    "runtime acceptance could not attach browser cache: %s",
                    linked.stderr[-500:],
                )
                continue
        else:
            try:
                target.symlink_to(source, target_is_directory=True)
            except OSError as exc:
                LOG.warning("runtime acceptance could not attach browser cache: %s", exc)
                continue
        return target
    return None


def _detach_shared_browser_install(target: Path | None) -> None:
    if target is None:
        return
    try:
        if os.name == "nt":
            os.rmdir(target)
        else:
            target.unlink()
    except FileNotFoundError:  # Already detached by child/process cleanup.
        return
    except OSError as exc:
        LOG.warning("runtime acceptance could not detach browser cache link: %s", exc)


def _remove_run_dir(path: Path) -> bool:
    """Retry bounded cleanup; False makes a nominal PASS fail closed."""
    last_error: OSError | None = None
    for delay in (0.0, 0.1, 0.3, 0.8, 1.5):
        if delay:
            time.sleep(delay)
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:  # A concurrent child teardown already removed it.
            return True
        except OSError as exc:
            last_error = exc
            LOG.debug("runtime acceptance temp cleanup retry for %s: %s", path, exc)
    if last_error is not None:
        LOG.warning(
            "runtime acceptance temp cleanup incomplete for %s: %s",
            path,
            last_error,
        )
    return not path.exists()


def _server_env(
    worktree: Path,
    run_dir: Path,
    config_path: Path,
    spec: RuntimeAcceptanceSpec,
) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(worktree)
        if not existing_pythonpath
        else str(worktree) + os.pathsep + existing_pythonpath
    )
    env["JARVIS_DATA_DIR"] = str(run_dir / "data")
    env["JARVIS_CONFIG"] = str(config_path)
    env["JARVIS_INSTANCE"] = "dev"
    env["JARVIS_BIND_HOST"] = "127.0.0.1"
    # ENV overrides beat the copied TOML, so explicitly silence unrelated
    # startup/voice subsystems for this one-turn headless acceptance runtime.
    env["JARVIS__BRAIN__HEALTHCHECK_ON_START"] = "false"
    env["JARVIS__ACK_BRAIN__ENABLED"] = "false"
    env["JARVIS__MEMORY__WIKI__ENABLED"] = "false"
    env["JARVIS__TTS__PROVIDER"] = "none"
    env[_ACCEPTANCE_ALLOWED_TOOLS_ENV] = json.dumps(sorted(spec.allowed_tools))
    env[_ACCEPTANCE_TOOL_LOG_ENV] = str(run_dir / "tool-attempts.jsonl")
    return env


def _spawn_server(
    worktree: Path,
    run_dir: Path,
    config_path: Path,
    spec: RuntimeAcceptanceSpec,
    *,
    port: int,
    python_executable: str | None,
    log_path: Path,
) -> subprocess.Popen[str]:
    env = _server_env(worktree, run_dir, config_path, spec)
    creationflags = NO_WINDOW_CREATIONFLAGS if os.name == "nt" else 0
    if os.name == "nt":
        creationflags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed local Python module
            [
                _python_console(python_executable),
                "-m",
                "jarvis.runtime_acceptance",
                "--serve",
                "--port",
                str(port),
            ],
            cwd=worktree,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    finally:
        log_handle.close()
    return process


def _wait_for_http(
    url: str,
    process: subprocess.Popen[str],
    *,
    timeout_s: float,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:  # noqa: S310 - loopback only
                if response.status == 200:
                    response.read(1)
                    return True
        except (OSError, urllib.error.URLError):  # Expected while the isolated runtime boots.
            pass
        time.sleep(0.2)
    return False



def _wait_for_health(
    url: str,
    process: subprocess.Popen[str],
    *,
    timeout_s: float,
) -> bool:
    """Require the expected AERION health document, not merely HTTP 200."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:  # noqa: S310 - loopback only
                if response.status != 200:
                    continue
                payload = json.loads(response.read().decode("utf-8"))
                if (
                    isinstance(payload, dict)
                    and payload.get("ok") is True
                    and payload.get("instance") == "dev"
                ):
                    return True
        except (OSError, ValueError, urllib.error.URLError):  # Expected during health warm-up.
            pass
        time.sleep(0.2)
    return False


async def _exchange(
    ws_url: str,
    *,
    prompt: str,
    thread_id: str,
    timeout_s: float,
) -> _ExchangeResult:
    import websockets

    actions: list[dict[str, Any]] = []
    errors: list[str] = []
    final_text = ""
    async with websockets.connect(
        ws_url,
        origin=ws_url.replace("ws://", "http://").rsplit("/ws", 1)[0],
        max_size=2**24,
        open_timeout=10,
    ) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5)
        await ws.send(
            json.dumps(
                {
                    "type": "message",
                    "content": prompt,
                    "metadata": {"thread_id": thread_id},
                }
            )
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(5.0, remaining))
            except asyncio.TimeoutError:  # No frame yet; the bounded turn deadline still applies.
                continue
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                continue
            name = str(obj.get("event_name") or "")
            payload = obj.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if name == "ActionExecuted":
                actions.append(
                    {
                        "tool": str(payload.get("tool_name") or ""),
                        "success": payload.get("success") is True,
                        "error": str(payload.get("error") or "")[:1000],
                    }
                )
            elif name == "ErrorOccurred":
                errors.append(str(payload.get("message") or payload.get("error") or "")[:2000])
            elif (
                name == "MessageSent"
                and payload.get("role") == "assistant"
                and payload.get("thread_id") == thread_id
            ):
                final_text = str(payload.get("text") or "")[:8000]
                if final_text:
                    return _ExchangeResult(final_text, tuple(actions), tuple(errors))
    return _ExchangeResult(final_text, tuple(actions), tuple(errors))


def _read_tool_attempts(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        return ()
    attempts: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:  # A partial/malformed private tool-log line is not evidence.
            continue
        if isinstance(value, dict):
            attempts.append(
                {
                    "tool": str(value.get("tool") or ""),
                    "allowed": value.get("allowed") is True,
                }
            )
    return tuple(attempts)


def _read_redacted_log(path: Path) -> str:
    if not path.is_file():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace")
    redacted = redact_secrets(raw)[-40000:]
    if redacted != raw:
        path.write_text(redacted, encoding="utf-8")
    return redacted


def _looks_transient(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _TRANSIENT_RUNTIME_MARKERS)


def _stop_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(  # noqa: S603 - fixed system utility and numeric pid
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # Grace period expired; force-kill the remaining root.
            process.kill()
        return

    try:
        os.killpg(process.pid, 15)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):  # Process exited or ignored graceful group termination.
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:  # The process group already exited; cleanup is complete.
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # Final reap timed out; force-kill the root handle.
                process.kill()


def _write_evidence(
    incident_dir: Path,
    spec: RuntimeAcceptanceSpec | None,
    result: RuntimeAcceptanceResult,
    *,
    tool_attempts: tuple[dict[str, Any], ...] = (),
) -> None:
    payload: dict[str, Any] = {
        "result": result.to_dict(),
        "tool_attempts": list(tool_attempts),
    }
    if spec is not None:
        payload["spec"] = {
            "profile": spec.profile,
            "prompt": spec.prompt,
            "allowed_tools": sorted(spec.allowed_tools),
            "required_tools": sorted(spec.required_tools),
            "forbidden_tools": sorted(spec.forbidden_tools),
            "timeout_s": spec.timeout_s,
        }
    (incident_dir / "runtime_acceptance.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def run_runtime_acceptance(
    *,
    worktree: Path,
    repo: Path,  # noqa: ARG001 - kept for call-site/audit context
    incident_dir: Path,
    rows: Iterable[dict[str, Any]],
    python_executable: str | None = None,
) -> RuntimeAcceptanceResult:
    """Run one isolated real-runtime turn against the patched worktree."""
    incident_dir.mkdir(parents=True, exist_ok=True)
    spec = derive_acceptance_spec(rows)
    if spec is None:
        result = RuntimeAcceptanceResult(
            RuntimeAcceptanceStatus.UNSUPPORTED,
            "no_supported_runtime_profile",
            "",
            (),
            "",
        )
        _write_evidence(incident_dir, None, result)
        return result

    thread_id = f"runtime-acceptance-{uuid4()}"
    run_dir = incident_dir / f".runtime-acceptance-{uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=False)
    log_path = incident_dir / "runtime_acceptance.log"
    tool_log = run_dir / "tool-attempts.jsonl"
    process: subprocess.Popen[str] | None = None
    shared_browser_link: Path | None = None
    exchange = _ExchangeResult("", (), ())
    result: RuntimeAcceptanceResult
    attempts: tuple[dict[str, Any], ...] = ()
    try:
        if spec.profile == "browser_page_title":
            shared_browser_link = _attach_shared_browser_install(
                worktree,
                repo,
                run_dir,
                python_executable=python_executable,
            )
            if shared_browser_link is None:
                result = RuntimeAcceptanceResult(
                    RuntimeAcceptanceStatus.UNSUPPORTED,
                    "compatible_browser_runtime_unavailable",
                    "",
                    (),
                    thread_id,
                )
                return result
        port = _free_loopback_port()
        config_path = _prepare_config(repo, run_dir, port=port)
        process = _spawn_server(
            worktree,
            run_dir,
            config_path,
            spec,
            port=port,
            python_executable=python_executable,
            log_path=log_path,
        )
        health_url = f"http://127.0.0.1:{port}/api/health"
        if not _wait_for_health(health_url, process, timeout_s=30.0):
            log_text = _read_redacted_log(log_path)
            status = (
                RuntimeAcceptanceStatus.TRANSIENT
                if _looks_transient(log_text)
                else RuntimeAcceptanceStatus.FAIL
            )
            result = RuntimeAcceptanceResult(
                status,
                "runtime_boot_transient" if status is RuntimeAcceptanceStatus.TRANSIENT else "runtime_boot_failed",
                "",
                (),
                thread_id,
            )
            return result

        # FastBootstrap intentionally answers /api/health before the full app is
        # ready. /api/config exists only on the full app, so this second local
        # readiness probe prevents a reconnect loop against a warming server.
        if not _wait_for_http(
            f"http://127.0.0.1:{port}/api/config",
            process,
            timeout_s=30.0,
        ):
            log_text = _read_redacted_log(log_path)
            status = (
                RuntimeAcceptanceStatus.TRANSIENT
                if _looks_transient(log_text)
                else RuntimeAcceptanceStatus.FAIL
            )
            result = RuntimeAcceptanceResult(
                status,
                "runtime_ready_transient" if status is RuntimeAcceptanceStatus.TRANSIENT else "runtime_not_ready",
                "",
                (),
                thread_id,
            )
            return result

        try:
            exchange = asyncio.run(
                _exchange(
                    f"ws://127.0.0.1:{port}/ws",
                    prompt=spec.prompt,
                    thread_id=thread_id,
                    timeout_s=spec.timeout_s,
                )
            )
        except Exception as exc:  # noqa: BLE001 - acceptance records runner faults instead of crashing the supervisor
            log_text = _read_redacted_log(log_path)
            combined = f"{type(exc).__name__}: {exc}\n{log_text}"
            status = (
                RuntimeAcceptanceStatus.TRANSIENT
                if _looks_transient(combined)
                else RuntimeAcceptanceStatus.FAIL
            )
            result = RuntimeAcceptanceResult(
                status,
                "runtime_turn_transient" if status is RuntimeAcceptanceStatus.TRANSIENT else "runtime_turn_failed",
                "",
                (),
                thread_id,
            )
            return result

        attempts = _read_tool_attempts(tool_log)
        forbidden_attempts = [a for a in attempts if not a["allowed"]]
        if forbidden_attempts:
            result = RuntimeAcceptanceResult(
                RuntimeAcceptanceStatus.FAIL,
                "forbidden_tool_attempt",
                exchange.final_text,
                exchange.actions,
                thread_id,
            )
            return result

        failed_allowed_actions = [
            action
            for action in exchange.actions
            if action["tool"] in spec.allowed_tools and action["success"] is not True
        ]
        if failed_allowed_actions:
            combined = "\n".join(
                str(action.get("error") or "") for action in failed_allowed_actions
            )
            status = (
                RuntimeAcceptanceStatus.TRANSIENT
                if _looks_transient(combined)
                else RuntimeAcceptanceStatus.FAIL
            )
            result = RuntimeAcceptanceResult(
                status,
                "browser_action_transient" if status is RuntimeAcceptanceStatus.TRANSIENT else "browser_action_failed",
                exchange.final_text,
                exchange.actions,
                thread_id,
            )
            return result

        successful_tools = {
            action["tool"]
            for action in exchange.actions
            if action.get("success") is True
        }
        missing = spec.required_tools - successful_tools
        combined_runtime_text = "\n".join(
            [exchange.final_text, *exchange.errors, _read_redacted_log(log_path)]
        )
        if missing:
            status = (
                RuntimeAcceptanceStatus.TRANSIENT
                if _looks_transient(combined_runtime_text)
                else RuntimeAcceptanceStatus.FAIL
            )
            result = RuntimeAcceptanceResult(
                status,
                "required_tool_transient" if status is RuntimeAcceptanceStatus.TRANSIENT else "required_tool_missing",
                exchange.final_text,
                exchange.actions,
                thread_id,
            )
            return result

        if not exchange.final_text.strip():
            status = (
                RuntimeAcceptanceStatus.TRANSIENT
                if _looks_transient(combined_runtime_text)
                else RuntimeAcceptanceStatus.FAIL
            )
            result = RuntimeAcceptanceResult(
                status,
                "empty_response_transient" if status is RuntimeAcceptanceStatus.TRANSIENT else "empty_final_response",
                "",
                exchange.actions,
                thread_id,
            )
            return result

        result = RuntimeAcceptanceResult(
            RuntimeAcceptanceStatus.PASS,
            "runtime_contract_satisfied",
            exchange.final_text,
            exchange.actions,
            thread_id,
        )
        return result
    finally:
        if process is not None:
            _stop_process_tree(process)
        attempts = _read_tool_attempts(tool_log)
        _read_redacted_log(log_path)
        if "result" in locals():
            _write_evidence(incident_dir, spec, result, tool_attempts=attempts)
        _detach_shared_browser_install(shared_browser_link)
        if not _remove_run_dir(run_dir):
            cleanup_failure = RuntimeAcceptanceResult(
                RuntimeAcceptanceStatus.FAIL,
                "runtime_cleanup_failed",
                exchange.final_text,
                exchange.actions,
                thread_id,
            )
            _write_evidence(
                incident_dir,
                spec,
                cleanup_failure,
                tool_attempts=attempts,
            )
            raise RuntimeError(
                f"runtime acceptance temporary state could not be removed: {run_dir}"
            )


def _install_tool_tripwire() -> None:
    from jarvis.safety.tool_executor import ToolExecutor

    raw_allowed = os.environ.get(_ACCEPTANCE_ALLOWED_TOOLS_ENV, "[]")
    try:
        parsed = json.loads(raw_allowed)
    except ValueError:  # Malformed guard configuration fails closed with no allowed tools.
        parsed = []
    allowed = {str(value) for value in parsed if isinstance(value, str)}
    log_path = Path(os.environ[_ACCEPTANCE_TOOL_LOG_ENV])
    old_execute = ToolExecutor.execute

    async def execute(self: Any, tool: Any, args: Any, **kwargs: Any) -> Any:
        name = str(getattr(tool, "name", "") or "")
        permitted = name in allowed
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"tool": name, "allowed": permitted}, ensure_ascii=True)
                + "\n"
            )
        if not permitted:
            raise RuntimeError(f"RUNTIME_ACCEPTANCE_TRIPWIRE:{name}")
        return await old_execute(self, tool, args, **kwargs)

    ToolExecutor.execute = execute


def _server_main(port: int) -> int:
    # The parent already wrote a minimal disposable config. The child never
    # mutates config files, including the user's real jarvis.toml.
    _install_tool_tripwire()

    # The acceptance server exists only to exercise one chat turn. Suppress
    # unrelated ambient/background duties so the proof cannot touch the user's
    # board, wiki vault, chat channels, or connected marketplace credentials.
    from jarvis.ui.web import server as web_server_module

    async def _acceptance_noop_async(*args: Any, **kwargs: Any) -> None:
        return None

    def _acceptance_noop_sync(*args: Any, **kwargs: Any) -> None:
        return None

    web_server_module.WebServer._start_marketplace_refresh_scheduler = _acceptance_noop_sync
    web_server_module.WebServer._start_local_models_health_monitor = _acceptance_noop_sync
    web_server_module.WebServer._setup_board = _acceptance_noop_sync
    web_server_module.WebServer._init_wiki_integration = _acceptance_noop_async
    web_server_module.WebServer._init_memory_retention = _acceptance_noop_async
    web_server_module.WebServer._init_wiki_boot_index = _acceptance_noop_sync
    web_server_module.WebServer._init_wiki_watcher = _acceptance_noop_sync
    web_server_module.WebServer._init_channel_stack = _acceptance_noop_async
    from jarvis.ui.web.launcher import main

    return int(
        main(
            [
                "--headless",
                "--instance",
                "dev",
                "--port",
                str(port),
                "--no-lock",
            ]
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    if args.serve:
        if not args.port:
            parser.error("--serve requires --port")
        return _server_main(args.port)
    parser.error("runtime_acceptance is an internal self-engineering helper")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

