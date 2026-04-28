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
export NODE_PATH="$DATA/node_modules${NODE_PATH:+:$NODE_PATH}"

emit_passthrough() {
    echo '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":""}}'
}

emit_context() {
    # $1 = additionalContext text (escaped for JSON)
    local ctx="$1"
    node -e "process.stdout.write(JSON.stringify({hookSpecificOutput:{hookEventName:'UserPromptSubmit',additionalContext:process.argv[1]}}))" -- "$ctx"
}

HOOK_ENTRY="$ROOT/dist/adapters/hook.js"
if [ ! -f "$HOOK_ENTRY" ]; then
    emit_passthrough
    echo "[skillogy-hook] dist/ not built yet — passthrough" >> "$LOG_DIR/hook.log"
    exit 0
fi

# Surface "installing dependencies" as a one-time message until the bootstrap
# install finishes (sentinel = $DATA/.installing).
if [ -f "$DATA/.installing" ] || [ ! -d "$DATA/node_modules" ]; then
    if [ ! -f "$DATA/.installing-notified" ]; then
        touch "$DATA/.installing-notified" 2>/dev/null || true
        emit_context $'[skillogy] Installing dependencies in the background.\nRouting will activate once npm install finishes (~30-60s on first session).\nTail /tmp/skillogy/install.log for progress.\n'
    else
        emit_passthrough
    fi
    exit 0
fi
# Once install is done, clear the notification flag so the next degradation
# (Neo4j down, indexing pending) can re-notify cleanly.
rm -f "$DATA/.installing-notified" 2>/dev/null || true

exec node "$HOOK_ENTRY"
