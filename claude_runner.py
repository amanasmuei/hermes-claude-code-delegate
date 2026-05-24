"""Container lifecycle + headless Claude Code invocation.

Design (matches the architecture we settled on):
  - Isolation boundary is the STREAM, not the individual task.
  - One warm, long-lived container per stream_id, reused across tasks via
    `docker exec`. State (repo, installed deps, Claude session history) lives on
    a per-stream named volume mounted at /work, with HOME=/work so Claude's
    ~/.claude session store persists there too.
  - Continuity: after the first task in a stream we pass `--continue` so each
    subsequent task continues the SAME Claude conversation.
  - Hibernation: `docker stop` between bursts keeps state on the volume at near
    zero cost; the next task `docker start`s it warm again.

Assumptions (documented in README.md):
  - Docker is installed and usable by the user running Hermes.
  - ANTHROPIC_API_KEY is set in the environment (gated via plugin.yaml).
  - A sandbox image with `claude` on PATH exists (see Dockerfile). Default name
    is claude-sandbox:latest, overridable via CLAUDE_SANDBOX_IMAGE.

This module shells out with argv lists (never shell=True) and validates the
stream_id against an allowlist, so task text / stream names cannot inject shell
commands or extra docker flags.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

_NAME_PREFIX = "cc-hermes"
_VALID_STREAM = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STARTED_MARKER = "/work/.claude/.hermes_started"  # presence => continue session


def _image() -> str:
    return os.environ.get("CLAUDE_SANDBOX_IMAGE", "claude-sandbox:latest")


def _container(stream_id: str) -> str:
    return f"{_NAME_PREFIX}-{stream_id}"


def _volume(stream_id: str) -> str:
    return f"{_NAME_PREFIX}-vol-{stream_id}"


def _docker(args, timeout=None, check=False):
    """Run a docker command. Returns CompletedProcess; never raises on nonzero
    unless check=True. Raises FileNotFoundError if docker is missing."""
    proc = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"docker {' '.join(args[:2])} failed ({proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def validate_stream_id(stream_id: str) -> str:
    stream_id = (stream_id or "default").strip()
    if not _VALID_STREAM.match(stream_id):
        raise ValueError(
            "stream_id must be 1-64 chars of letters, digits, '-' or '_'."
        )
    return stream_id


# --- container state ---------------------------------------------------------

def _exists(stream_id: str) -> bool:
    name = _container(stream_id)
    out = _docker(
        ["ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"]
    ).stdout.strip()
    return out == name


def _running(stream_id: str) -> bool:
    name = _container(stream_id)
    out = _docker(
        ["ps", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"]
    ).stdout.strip()
    return out == name


def ensure_container(stream_id: str) -> None:
    """Make sure a warm container for this stream is running (lazy create/start)."""
    if _running(stream_id):
        return
    if _exists(stream_id):
        _docker(["start", _container(stream_id)], check=True)
        return

    # First time for this stream: create the volume and the hardened container.
    _docker(["volume", "create", _volume(stream_id)], check=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    run_args = [
        "run", "-d",
        "--name", _container(stream_id),
        "-v", f"{_volume(stream_id)}:/work",
        "-w", "/work",
        "-e", "HOME=/work",
        # API key baked into the container env at creation. Rotate => recreate stream.
        "-e", f"ANTHROPIC_API_KEY={api_key}",
        # Isolation hardening. Network stays on (Claude must reach the API).
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", os.environ.get("CC_DELEGATE_MEMORY", "4g"),
        "--cpus", os.environ.get("CC_DELEGATE_CPUS", "2"),
        _image(),
        "sleep", "infinity",
    ]
    _docker(run_args, check=True)


# --- permission posture ------------------------------------------------------

def permission_flags(mode: str):
    """Map our permission_mode onto Claude Code's headless permission flags."""
    if mode == "sandbox":
        # Full autonomy: the container IS the safety boundary.
        return ["--dangerously-skip-permissions"]
    if mode == "plan":
        return ["--permission-mode", "plan"]
    # 'acceptEdits' and the post-confirmation 'boundary' path both run with edits
    # auto-accepted; anything riskier is denied by Claude and reported back.
    return ["--permission-mode", "acceptEdits"]


# --- running a task ----------------------------------------------------------

def _has_started(stream_id: str) -> bool:
    name = _container(stream_id)
    rc = _docker(["exec", name, "test", "-f", _STARTED_MARKER]).returncode
    return rc == 0


def _mark_started(stream_id: str) -> None:
    name = _container(stream_id)
    _docker(["exec", name, "sh", "-c",
             f"mkdir -p /work/.claude && touch {_STARTED_MARKER}"])


def _parse_result(stdout: str) -> dict:
    """Parse `claude --output-format json` output defensively.

    Expected shape: {"type":"result","subtype":"success","is_error":false,
                     "result":"...","session_id":"...","num_turns":N, ...}
    """
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {"ok": True, "result": stdout.strip(), "raw": True}

    text = str(data.get("result", "")) if isinstance(data, dict) else str(data)
    is_error = bool(data.get("is_error")) if isinstance(data, dict) else False
    subtype = data.get("subtype") if isinstance(data, dict) else None

    low = text.lower()
    blocked = ("permission" in low and
               any(k in low for k in ("denied", "not allowed", "don't have",
                                      "do not have", "blocked", "requires approval")))
    return {
        "ok": not is_error and subtype in (None, "success"),
        "subtype": subtype,
        "result": text,
        "blocked_on_permissions": blocked,
        "session_id": data.get("session_id") if isinstance(data, dict) else None,
        "num_turns": data.get("num_turns") if isinstance(data, dict) else None,
        "cost_usd": data.get("total_cost_usd") if isinstance(data, dict) else None,
    }


def run_task(stream_id, task, mode, fresh_session, timeout_seconds) -> dict:
    """Ensure the stream container is warm, run one task, return a result dict."""
    ensure_container(stream_id)

    continue_session = (not fresh_session) and _has_started(stream_id)
    name = _container(stream_id)
    cmd = [
        "exec", "-w", "/work", "-e", "HOME=/work", name,
        "claude", "-p", task, "--output-format", "json",
        *permission_flags(mode),
    ]
    if continue_session:
        cmd.append("--continue")

    try:
        proc = _docker(cmd, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "stream_id": stream_id,
            "message": (
                f"Claude Code did not finish within {timeout_seconds}s. The "
                "container is left running (warm) so you can continue this stream."
            ),
        }

    if proc.returncode != 0 and not proc.stdout.strip():
        return {
            "status": "error",
            "stream_id": stream_id,
            "message": (proc.stderr.strip() or "claude exited nonzero with no output"),
        }

    parsed = _parse_result(proc.stdout)
    _mark_started(stream_id)
    return {
        "status": "completed" if parsed.get("ok") else "completed_with_errors",
        "stream_id": stream_id,
        "continued": continue_session,
        **parsed,
    }


# --- management --------------------------------------------------------------

def list_streams() -> list:
    out = _docker(
        ["ps", "-a", "--filter", f"name=^/{_NAME_PREFIX}-",
         "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"]
    ).stdout.strip()
    streams = []
    for line in (l for l in out.splitlines() if l.strip()):
        parts = line.split("\t")
        name = parts[0]
        if name.startswith(f"{_NAME_PREFIX}-vol-"):
            continue
        streams.append({
            "stream_id": name[len(_NAME_PREFIX) + 1:],
            "state": parts[1] if len(parts) > 1 else "",
            "status": parts[2] if len(parts) > 2 else "",
        })
    return streams


def stop_stream(stream_id: str) -> dict:
    if not _exists(stream_id):
        return {"status": "not_found", "stream_id": stream_id}
    _docker(["stop", _container(stream_id)], check=True)
    return {"status": "stopped", "stream_id": stream_id,
            "note": "State preserved on volume; next task will resume warm."}


def remove_stream(stream_id: str) -> dict:
    if _exists(stream_id):
        _docker(["rm", "-f", _container(stream_id)])
    _docker(["volume", "rm", "-f", _volume(stream_id)])
    return {"status": "removed", "stream_id": stream_id,
            "note": "Container and volume destroyed (irreversible)."}


def hibernate_all() -> int:
    """Stop every running stream container (volumes kept). Returns count stopped.
    Wired to on_session_end so sandboxes don't linger after a conversation ends."""
    stopped = 0
    for s in list_streams():
        if str(s.get("state", "")).lower() == "running":
            try:
                _docker(["stop", _container(s["stream_id"])])
                stopped += 1
            except Exception:
                pass
    return stopped
