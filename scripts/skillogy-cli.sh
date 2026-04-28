#!/usr/bin/env bash
# Slash-command friendly wrapper around `node dist/cli/index.js`.
# Sets NODE_PATH so the CLI can resolve runtime deps from
# ${CLAUDE_PLUGIN_DATA}/node_modules (where bootstrap installs them).
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SKILLOGY_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
DATA="${SKILLOGY_DATA:-${CLAUDE_PLUGIN_DATA:-$ROOT/.skillogy-data}}"

if [ -f "$ROOT/.env" ]; then
    set -a; source "$ROOT/.env"; set +a
fi

export NODE_PATH="$DATA/node_modules${NODE_PATH:+:$NODE_PATH}"
exec node "$ROOT/dist/cli/index.js" "$@"
