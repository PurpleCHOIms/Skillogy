#!/usr/bin/env bash
# Skillogy SessionStart bootstrap.
# Idempotent: ensures the plugin's npm dependencies are installed in
# ${CLAUDE_PLUGIN_DATA}/node_modules, brings up Neo4j, and kicks off
# incremental indexing in the background.
#
# This follows the official Claude Code plugin pattern — the bundled dist/
# under ${CLAUDE_PLUGIN_ROOT} is small (esbuild only inlines our own source;
# every runtime dependency is external). Deps are persisted in
# ${CLAUDE_PLUGIN_DATA} so they survive plugin updates and so native binary
# packages (e.g. @anthropic-ai/claude-agent-sdk's platform-specific .node
# files, which esbuild cannot bundle) resolve correctly via NODE_PATH.
#
# Honors:
#   SKILLOGY_ROOT             → repo root (default: ${CLAUDE_PLUGIN_ROOT}, then script's parent dir)
#   SKILLOGY_DATA             → persistent dep dir (default: ${CLAUDE_PLUGIN_DATA}, then $ROOT/.skillogy-data)
#   SKILLOGY_SKIP_BOOTSTRAP=1 → no-op
#   SKILLOGY_SKIP_NEO4J=1     → skip Neo4j docker bringup
#   SKILLOGY_SKIP_INDEX=1     → skip background indexing
#   SKILLOGY_SKIP_INSTALL=1   → skip npm install in DATA
#   SKILLOGY_DEV=1            → also rebuild dist/ from source (requires source checkout)

set -e

if [ "${SKILLOGY_SKIP_BOOTSTRAP:-}" = "1" ]; then
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SKILLOGY_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
DATA="${SKILLOGY_DATA:-${CLAUDE_PLUGIN_DATA:-$ROOT/.skillogy-data}}"
LOG_DIR="${SKILLOGY_LOG_DIR:-/tmp/skillogy}"
mkdir -p "$LOG_DIR" "$DATA"

log() { echo "[skillogy-bootstrap] $*" >&2; }
err() { echo "[skillogy-bootstrap] ERROR: $*" >&2; }

# 0. Prereq check
if ! command -v node >/dev/null; then
    err "node is required but not installed (need >= 20)."
    exit 1
fi
if ! command -v npm >/dev/null; then
    err "npm is required but not installed."
    exit 1
fi
if ! command -v docker >/dev/null; then
    log "WARNING: docker not found — Neo4j auto-start will be skipped."
    log "  Install Docker, or start Neo4j yourself on bolt://localhost:7687"
    node "$SCRIPT_DIR/state-set.mjs" docker-missing 1 2>/dev/null || true
else
    node "$SCRIPT_DIR/state-set.mjs" docker-missing 0 2>/dev/null || true
fi

# Load .env (dev workflow only — published plugin doesn't ship one)
if [ -f "$ROOT/.env" ]; then
    set -a; source "$ROOT/.env"; set +a
fi

# 1. Optional dev rebuild from source.
if [ "${SKILLOGY_DEV:-}" = "1" ] && [ -f "$ROOT/scripts/build.mjs" ]; then
    if [ ! -d "$ROOT/node_modules" ]; then
        log "[dev] Installing devDependencies for source build → $LOG_DIR/install.log"
        (cd "$ROOT" && npm install) >>"$LOG_DIR/install.log" 2>&1 || {
            err "[dev] npm install in $ROOT failed — see $LOG_DIR/install.log"
            exit 1
        }
    fi
    log "[dev] Rebuilding bundles → $LOG_DIR/build.log"
    (cd "$ROOT" && npm run build) >>"$LOG_DIR/build.log" 2>&1 || {
        err "[dev] npm run build failed — see $LOG_DIR/build.log"
        exit 1
    }
fi

# 2. Sync the plugin's runtime npm dependencies into ${CLAUDE_PLUGIN_DATA}.
#    Only re-run npm install when package.json differs from the cached copy
#    (the official Claude Code plugin pattern).
if [ "${SKILLOGY_SKIP_INSTALL:-}" != "1" ]; then
    if ! diff -q "$ROOT/package.json" "$DATA/package.json" >/dev/null 2>&1; then
        log "Installing plugin dependencies → $DATA/node_modules (see $LOG_DIR/install.log)"
        cp "$ROOT/package.json" "$DATA/"
        # npm install honors optionalDependencies (needed for claude-agent-sdk
        # platform-specific .node binaries) and skips devDependencies.
        if (cd "$DATA" && npm install --omit=dev --no-audit --no-fund) >>"$LOG_DIR/install.log" 2>&1; then
            log "Dependencies installed."
        else
            err "npm install in $DATA failed — see $LOG_DIR/install.log"
            rm -f "$DATA/package.json"
            exit 1
        fi
    fi
fi

# Make node_modules visible to default Node ESM resolution. Node walks UP
# from the importing file, so ${ROOT}/node_modules must resolve to the deps
# in DATA. Symlink, don't copy — copying breaks update semantics. NODE_PATH
# alone wouldn't suffice because Node ignores it for ESM imports.
if [ -d "$DATA/node_modules" ]; then
    if [ -L "$ROOT/node_modules" ]; then
        # Existing symlink — refresh to current DATA target if drifted
        if [ "$(readlink "$ROOT/node_modules")" != "$DATA/node_modules" ]; then
            rm -f "$ROOT/node_modules"
            ln -s "$DATA/node_modules" "$ROOT/node_modules"
        fi
    elif [ ! -e "$ROOT/node_modules" ]; then
        ln -s "$DATA/node_modules" "$ROOT/node_modules"
    fi
    # If $ROOT/node_modules is a real directory, leave it alone — that's a dev
    # checkout where the developer ran `npm install` against source.
fi
export NODE_PATH="$DATA/node_modules${NODE_PATH:+:$NODE_PATH}"

# 3. Neo4j
need_neo4j_up() {
    ! curl -s -o /dev/null -m 1 http://localhost:7474
}
if [ "${SKILLOGY_SKIP_NEO4J:-}" != "1" ] && need_neo4j_up; then
    if command -v docker >/dev/null && [ -f "$ROOT/docker-compose.yml" ]; then
        log "Starting Neo4j (docker compose)..."
        (cd "$ROOT" && docker compose up -d neo4j) >>"$LOG_DIR/neo4j.log" 2>&1 || log "  neo4j start failed (see $LOG_DIR/neo4j.log)"
    else
        log "  docker or docker-compose.yml missing — skipping Neo4j"
    fi
elif [ "${SKILLOGY_SKIP_NEO4J:-}" != "1" ]; then
    log "Neo4j already up on :7474"
fi

# 4. Incremental indexing (background).
if [ "${SKILLOGY_SKIP_INDEX:-}" != "1" ]; then
    # Wait briefly for Neo4j bolt before forking the indexer.
    for _ in $(seq 1 20); do
        curl -s -o /dev/null -m 1 http://localhost:7474 && break
        sleep 1
    done
    SCOPES="${SKILLOGY_INDEX_SCOPES:-user,project}"
    log "Triggering incremental indexing in background (scopes=$SCOPES) → $LOG_DIR/index.log"
    nohup node "$ROOT/dist/cli/index.js" index --incremental --workers 8 --scopes "$SCOPES" \
        >"$LOG_DIR/index.log" 2>&1 &
    disown
fi

log "Bootstrap done. ROOT=$ROOT  DATA=$DATA  Logs=$LOG_DIR/"
exit 0
