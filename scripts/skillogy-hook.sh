#!/usr/bin/env bash
# UserPromptSubmit hook wrapper — invoked by Claude Code per turn.
# Reads prompt JSON from stdin, emits additionalContext JSON to stdout.
# Adjust SKILLOGY_ROOT if the project lives elsewhere.

set -e
ROOT="${SKILLOGY_ROOT:-/home/catow/GIT/Hackathon}"
echo "[HOOK FIRED] $(date '+%H:%M:%S') cwd=$PWD" >> /tmp/skill-router-hook.log

# Load LLM credentials from .env so we don't depend on the parent shell
if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ROOT/.env"
    set +a
fi

# Use claude-agent-sdk (OAuth from Claude Code) — no LLM API key needed
unset ANTHROPIC_API_KEY GOOGLE_API_KEY
export SKILLOGY_LLM="${SKILLOGY_LLM:-sdk}"

exec "$ROOT/.venv/bin/python" -m skillogy.adapters.hook
