"""claude-code-delegate — delegate tasks to a headless Claude Code agent in an
isolated, per-stream Docker sandbox.

register(ctx) wires:
  - tool  delegate_to_claude_code  (with the permission_mode gate)
  - tool  claude_code_streams      (list / stop / remove)
  - cmd   /cc-streams              (human-facing convenience)
  - hook  on_session_end           (hibernate idle sandboxes)
"""

import importlib.util
import json
import os

# Load sibling modules by path so this works regardless of how Hermes imports
# the plugin package (avoids relative-import fragility).
_HERE = os.path.dirname(__file__)


def _load(mod_name):
    spec = importlib.util.spec_from_file_location(
        f"cc_delegate_{mod_name}", os.path.join(_HERE, f"{mod_name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load("claude_runner")
schemas = _load("schemas")


def register(ctx):
    # --- tool: delegate_to_claude_code -------------------------------------
    def handle_delegate(params, **kwargs):
        del kwargs
        try:
            stream_id = runner.validate_stream_id(params.get("stream_id", "default"))
        except ValueError as e:
            return json.dumps({"status": "error", "message": str(e)})

        task = (params.get("task") or "").strip()
        if not task:
            return json.dumps({"status": "error", "message": "task is required."})

        mode = params.get("permission_mode", "boundary")
        confirmed = bool(params.get("confirmed", False))
        fresh = bool(params.get("fresh_session", False))
        timeout = int(params.get("timeout_seconds", 1800) or 1800)

        # The human-in-the-loop boundary gate: on the first call we DON'T run.
        # We return a preview the model must show the user, then re-call with
        # confirmed=true. Works in both CLI and gateway (e.g. Telegram) modes,
        # because the approval happens in the conversation itself.
        if mode == "boundary" and not confirmed:
            return json.dumps({
                "status": "approval_required",
                "stream_id": stream_id,
                "summary": (
                    f"Claude Code wants to run this task in sandbox stream "
                    f"'{stream_id}' with edits auto-accepted:"
                ),
                "task_preview": task[:500],
                "capabilities": "read/write files, run commands, network — inside "
                                "an isolated container (host is not touched).",
                "next_step": ("Ask the user to approve. If they agree, call "
                              "delegate_to_claude_code again with confirmed=true "
                              "(same task and stream_id)."),
            })

        try:
            result = runner.run_task(stream_id, task, mode, fresh, timeout)
        except FileNotFoundError:
            return json.dumps({"status": "error",
                               "message": "docker not found on PATH where Hermes runs."})
        except Exception as e:  # surface real failure rather than a silent half-result
            return json.dumps({"status": "error", "stream_id": stream_id,
                               "message": f"{type(e).__name__}: {e}"})

        return json.dumps(result)

    ctx.register_tool(
        name="delegate_to_claude_code",
        toolset="claude_code_delegate",
        schema=schemas.DELEGATE_SCHEMA,
        handler=handle_delegate,
        description="Delegate a task to a headless Claude Code agent in an isolated sandbox.",
    )

    # --- tool: claude_code_streams -----------------------------------------
    def handle_streams(params, **kwargs):
        del kwargs
        action = params.get("action")
        sid_raw = params.get("stream_id")
        try:
            if action == "list":
                return json.dumps({"status": "ok", "streams": runner.list_streams()})
            sid = runner.validate_stream_id(sid_raw)
            if action == "stop":
                return json.dumps(runner.stop_stream(sid))
            if action == "remove":
                return json.dumps(runner.remove_stream(sid))
            return json.dumps({"status": "error",
                               "message": f"unknown action: {action!r}"})
        except FileNotFoundError:
            return json.dumps({"status": "error", "message": "docker not found on PATH."})
        except ValueError as e:
            return json.dumps({"status": "error", "message": str(e)})
        except Exception as e:
            return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})

    ctx.register_tool(
        name="claude_code_streams",
        toolset="claude_code_delegate",
        schema=schemas.STREAMS_SCHEMA,
        handler=handle_streams,
        description="List, hibernate, or destroy Claude Code delegation sandboxes.",
    )

    # --- slash command: /cc-streams (human convenience) --------------------
    def cmd_streams(args, **kwargs):
        del kwargs
        parts = (args or "").split()
        action = parts[0] if parts else "list"
        sid = parts[1] if len(parts) > 1 else None
        return handle_streams({"action": action, "stream_id": sid})

    ctx.register_command(
        "cc-streams",
        cmd_streams,
        "List/stop/remove Claude Code sandboxes. Usage: /cc-streams [list|stop|remove] [stream_id]",
    )

    # --- hook: hibernate sandboxes when a conversation ends ----------------
    def on_end(*_args, **_kwargs):
        try:
            runner.hibernate_all()
        except Exception:
            pass  # never let cleanup break session teardown

    ctx.register_hook("on_session_end", on_end)
