# Sandbox image for claude-code-delegate.
# Build once:  docker build -t claude-sandbox:latest .
#
# Override the image name via CLAUDE_SANDBOX_IMAGE if you bake in project
# toolchains (compilers, language runtimes, your repo's deps, etc.).
FROM node:20-bookworm-slim

# Tools Claude Code commonly relies on. Add your project's toolchain here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates ripgrep curl \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI.
RUN npm install -g @anthropic-ai/claude-code

# HOME=/work so Claude's ~/.claude session store lands on the persistent volume
# the plugin mounts at /work (this is what gives cross-task conversation continuity).
ENV HOME=/work
WORKDIR /work

# The plugin keeps the container alive and drives it via `docker exec`.
CMD ["sleep", "infinity"]
