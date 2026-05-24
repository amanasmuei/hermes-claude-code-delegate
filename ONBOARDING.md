# Onboarding — testing `claude-code-delegate`

Thanks for helping test this Hermes Agent plugin. It delegates tasks to a
**headless Claude Code** agent running inside an **isolated, per-stream Docker
sandbox**. This guide gets you from zero to a verified run in a few minutes.

> New here? Read the [README](./README.md) first for the *why* behind the
> design. This file is the hands-on test path.

## 1. Prerequisites

| Need | Check |
|---|---|
| Hermes Agent installed | `hermes --version` |
| Docker running | `docker info` (should not error) |
| Anthropic API key | `echo $ANTHROPIC_API_KEY` (must be set) |

## 2. Install

```bash
git clone https://github.com/amanasmuei/hermes-claude-code-delegate \
  ~/.hermes/plugins/claude-code-delegate

# Build the sandbox image (the plugin runs Claude inside this)
docker build -t claude-sandbox:latest ~/.hermes/plugins/claude-code-delegate

export ANTHROPIC_API_KEY=sk-ant-...        # required
hermes plugins enable claude-code-delegate # add to allow-list
# restart Hermes
```

Confirm the tools registered:

```
hermes plugins list        # claude-code-delegate should show "enabled"
```

In a chat, the model now has `delegate_to_claude_code` and `claude_code_streams`,
and you have the `/cc-streams` slash command.

## 3. Test path (run in order — each step raises the risk a little)

### Step A — `plan` mode (zero risk, no changes)
Ask Hermes:
> "Delegate to Claude Code in **plan** mode: list the files in the workspace and propose a hello-world script."

✅ Pass = you get a plan back, nothing is written. This exercises the whole
container → `claude -p` → JSON-parse path safely.

### Step B — `boundary` mode (the approval handshake)
> "Delegate to Claude Code: create `hello.py` that prints the date."

✅ Pass = Hermes first asks **you** to approve (it received
`status: "approval_required"`), and only runs after you say yes. Works the same
from the CLI and from Telegram/Discord.

### Step C — continuity (the multi-task point of the plugin)
After Step B, in the **same** stream:
> "Now add a `--name` argument to that script."

✅ Pass = Claude remembers `hello.py` from the previous task (same conversation
continued), and the file persists in the sandbox.

### Step D — stream management
```
/cc-streams list           # see the running sandbox
/cc-streams stop default   # hibernate it (state kept)
```
Then run another task → it should resume **warm**.

## 4. What I most need eyes on

Two things could **not** be verified without a live Hermes + Claude Code, so
please watch them closely and report results either way:

1. **Plugin callback signatures** — `/cc-streams` and the on-session-end
   auto-hibernate hook. If either errors, copy the traceback.
2. **`claude -p --output-format json` shape** — parsing is defensive, but field
   names (`is_error`, `subtype`, `result`) may differ across Claude Code
   versions. If results look truncated/wrong, grab the raw JSON.

Get the raw output for debugging:
```bash
docker exec cc-hermes-default claude -p "say hi" --output-format json
```

## 5. Reporting back

Open an issue using a template:
- **🐞 Bug report** — something broke or errored.
- **🧪 Test report** — it worked (tell us your environment + which steps passed).

→ https://github.com/amanasmuei/hermes-claude-code-delegate/issues/new/choose

Please include: Hermes version, Claude Code version (`claude --version`), Docker
version, your OS, and the `permission_mode` you used.

## 6. Cleanup

```
/cc-streams remove default          # destroys container + volume (irreversible)
docker rmi claude-sandbox:latest    # remove the image
hermes plugins disable claude-code-delegate
```
