#!/usr/bin/env bash
# UserPromptSubmit hook wrapper — invoked by Claude Code per turn.
# Reads prompt JSON from stdin, emits hookSpecificOutput.additionalContext on stdout.

set -e
# Same ROOT-derivation as skillogy-bootstrap.sh — pwd fallback would resolve
# to the user's working directory which has no dist/.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SKILLOGY_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
LOG_DIR="${SKILLOGY_LOG_DIR:-/tmp/skillogy}"
mkdir -p "$LOG_DIR"

# Load .env so we don't depend on the parent shell
if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ROOT/.env"
    set +a
fi

# Default to Claude Agent SDK (OAuth from Claude Code) — no LLM API key needed
export SKILLOGY_LLM="${SKILLOGY_LLM:-sdk}"

HOOK_ENTRY="$ROOT/dist/adapters/hook.js"
if [ ! -f "$HOOK_ENTRY" ]; then
    # First-run guard: if dist/ missing, emit passthrough rather than crashing the prompt
    echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":""}}'
    echo "[skillogy-hook] dist/ not built yet — passthrough" >> "$LOG_DIR/hook.log"
    exit 0
fi

exec node "$HOOK_ENTRY"
