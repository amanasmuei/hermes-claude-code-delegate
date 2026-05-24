# claude-code-delegate

A Hermes Agent plugin that delegates tasks to a **headless Claude Code** agent
running inside an **isolated, per-stream Docker sandbox**. Built for
*continuous, multi-task* delegation: tasks that share a `stream_id` reuse one
warm container and continue the same Claude conversation.

## Why it's shaped this way

| Concern | Decision |
|---|---|
| True delegation (Claude's model does the work) | Runs `claude -p` (headless agent), **not** `claude mcp serve` (which would only lend tools to Hermes' model). |
| Continuity across many tasks | **One warm container per stream**, reused via `docker exec`; `--continue` continues the same conversation. |
| Don't pay cold-start every task | `docker stop`/`start` hibernation; state lives on a per-stream **named volume** (repo, deps, `~/.claude`). |
| Isolation | Container is the blast radius (`--cap-drop ALL`, `no-new-privileges`, mem/cpu limits). Isolation boundary = the **stream**, not each task. |

## Install

```bash
docker build -t claude-sandbox:latest \
  ~/.hermes/plugins/claude-code-delegate            # build the sandbox image
export ANTHROPIC_API_KEY=sk-ant-...                 # required
hermes plugins enable claude-code-delegate          # add to the allow-list
# restart Hermes
```

## Permission model (`permission_mode`)

Headless Claude **cannot** pause and ask a human mid-run — so you pick the
posture up front:

| Mode | Hermes-side gate | Inside the box | Use |
|---|---|---|---|
| `boundary` *(default)* | Tool returns `approval_required`; user OKs in chat, you re-call with `confirmed=true` | `--permission-mode acceptEdits` | First run / sensitive work |
| `sandbox` | none | `--dangerously-skip-permissions` | Autonomous / continuous (trust the box) |
| `acceptEdits` | none | `--permission-mode acceptEdits` | Auto-edit; risky ops denied + reported |
| `plan` | none | `--permission-mode plan` | Dry run — Claude plans, changes nothing |

The boundary handshake happens **in the conversation**, so it works from the
CLI *and* from gateway platforms (Telegram, Discord, …). If Claude reports it
was blocked for lack of permission, the result carries
`blocked_on_permissions: true` so it can be relayed to the user.

> A live *per-action* approval bridge (Claude's `--permission-prompt-tool` /
> SDK `canUseTool` calling back to Hermes) is intentionally **not** built in:
> it blocks the agent until a human answers, which fights the async/background
> model. Add it only if your users are always present synchronously.

## Tools & commands

- `delegate_to_claude_code(task, stream_id?, permission_mode?, confirmed?, fresh_session?, timeout_seconds?)`
- `claude_code_streams(action: list|stop|remove, stream_id?)`
- `/cc-streams [list|stop|remove] [stream_id]`
- On session end, running sandboxes are hibernated automatically (volumes kept).

## Assumptions

- Docker is installed and usable by the user running Hermes.
- `ANTHROPIC_API_KEY` is set (baked into the container at stream creation —
  rotate the key ⇒ `remove` and recreate the stream).
- The sandbox needs **network** (Claude must reach the API), so it is not run
  with `--network none`.
- For per-task (not per-stream) throwaway isolation of an untrusted task, use a
  fresh `stream_id` and `remove` it afterward.

## Optional env knobs

`CLAUDE_SANDBOX_IMAGE` (default `claude-sandbox:latest`),
`CC_DELEGATE_MEMORY` (default `4g`), `CC_DELEGATE_CPUS` (default `2`).
