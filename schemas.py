"""Tool schemas — what the Hermes model sees and reasons about.

Kept separate from the handlers so the LLM-facing contract is easy to audit.
"""

# Main delegation tool.
DELEGATE_SCHEMA = {
    "name": "delegate_to_claude_code",
    "description": (
        "Delegate a self-contained coding/agentic task to a headless Claude Code "
        "agent running in an ISOLATED Docker sandbox. Claude's own model plans and "
        "executes the task and returns a result summary.\n\n"
        "Continuity: tasks sharing the same `stream_id` reuse one warm container and "
        "continue the SAME Claude conversation (workspace + context persist across "
        "calls). Use a new stream_id to start unrelated work in a fresh, isolated box.\n\n"
        "PERMISSIONS — choose `permission_mode`:\n"
        "  • boundary   (default) Ask the USER for approval before running. The tool "
        "first returns status=\"approval_required\" with a preview; show that preview "
        "to the user, and only call again with confirmed=true once they agree.\n"
        "  • sandbox    Full autonomy inside the box (no prompts). Safe because the "
        "container is the blast radius. Best for continuous/background work.\n"
        "  • acceptEdits Auto-accept file edits; other risky actions are denied and "
        "reported (Claude works around them or tells you it was blocked).\n"
        "  • plan       Dry run: Claude produces a plan and makes NO changes.\n\n"
        "The result includes `blocked_on_permissions` if Claude reported it could not "
        "do something for lack of permission — relay that to the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The task for Claude Code, as a clear standalone instruction. "
                    "Include any context Claude needs; it does not see this conversation."
                ),
            },
            "stream_id": {
                "type": "string",
                "description": (
                    "Identifier for a line of related work. Same id = same warm "
                    "container and continued conversation. Default 'default'. "
                    "Allowed chars: letters, digits, '-', '_'."
                ),
                "default": "default",
            },
            "permission_mode": {
                "type": "string",
                "enum": ["boundary", "sandbox", "acceptEdits", "plan"],
                "description": "Human-in-the-loop posture (see tool description).",
                "default": "boundary",
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Set true ONLY after the user approved a prior "
                    "status=\"approval_required\" response. Ignored unless "
                    "permission_mode is 'boundary'."
                ),
                "default": False,
            },
            "fresh_session": {
                "type": "boolean",
                "description": (
                    "Force a brand-new Claude conversation in this stream instead of "
                    "continuing the previous one. Workspace files still persist."
                ),
                "default": False,
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Max seconds to wait for Claude to finish (default 1800).",
                "default": 1800,
            },
        },
        "required": ["task"],
    },
}

# Stream management tool (list / hibernate / destroy).
STREAMS_SCHEMA = {
    "name": "claude_code_streams",
    "description": (
        "Manage the Docker sandboxes used by delegate_to_claude_code. "
        "list = show streams and their state; stop = hibernate a stream (keeps its "
        "volume/state, cheap); remove = destroy a stream's container AND volume "
        "(irreversible)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "stop", "remove"],
                "description": "What to do.",
            },
            "stream_id": {
                "type": "string",
                "description": "Target stream for stop/remove (required for those).",
            },
        },
        "required": ["action"],
    },
}
