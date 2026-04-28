#!/usr/bin/env bash
# UserPromptSubmit hook wrapper — invoked by Claude Code per turn.
# Reads prompt JSON from stdin, emits hookSpecificOutput.additionalContext on stdout.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SKILLOGY_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
DATA="${SKILLOGY_DATA:-${CLAUDE_PLUGIN_DATA:-$ROOT/.skillogy-data}}"
LOG_DIR="${SKILLOGY_LOG_DIR:-/tmp/skillogy}"
mkdir -p "$LOG_DIR"

# Load .env so we don't depend on the parent shell.
if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ROOT/.env"
    set +a
fi

# Default to Claude Agent SDK (OAuth from Claude Code) — no LLM API key needed.
export SKILLOGY_LLM="${SKILLOGY_LLM:-sdk}"

# Resolve runtime deps from the persistent DATA dir (npm-installed by bootstrap).
export NODE_PATH="$DATA/node_modules${NODE_PATH:+:$NODE_PATH}"

HOOK_ENTRY="$ROOT/dist/adapters/hook.js"
if [ ! -f "$HOOK_ENTRY" ]; then
    # First-run guard: if dist/ missing, emit passthrough rather than crashing the prompt.
    echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":""}}'
    echo "[skillogy-hook] dist/ not built yet — passthrough" >> "$LOG_DIR/hook.log"
    exit 0
fi

if [ ! -d "$DATA/node_modules" ]; then
    # Dependencies haven't installed yet (bootstrap may still be running).
    # Passthrough silently instead of throwing module-not-found at the user.
    echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":""}}'
    echo "[skillogy-hook] $DATA/node_modules missing — passthrough (bootstrap still installing?)" >> "$LOG_DIR/hook.log"
    exit 0
fi

exec node "$HOOK_ENTRY"
