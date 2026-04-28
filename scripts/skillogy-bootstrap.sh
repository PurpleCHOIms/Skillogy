#!/usr/bin/env bash
# Skillogy SessionStart bootstrap.
# Idempotent: ensures node_modules + dist + Neo4j are ready, then triggers incremental indexing.
#
# Honors:
#   SKILLOGY_ROOT             → repo root (default: ${CLAUDE_PLUGIN_ROOT} or pwd)
#   SKILLOGY_SKIP_BOOTSTRAP=1 → no-op
#   SKILLOGY_SKIP_NEO4J=1     → skip Neo4j docker bringup
#   SKILLOGY_SKIP_INDEX=1     → skip background indexing
#   SKILLOGY_SKIP_BUILD=1     → skip npm install + bundle build
#   SKILLOGY_DEV=1            → force rebuild from source (dev workflow); without
#                               this, bundled dist/ shipped in the repo is used
#                               and node_modules is not required at runtime

set -e

if [ "${SKILLOGY_SKIP_BOOTSTRAP:-}" = "1" ]; then
    exit 0
fi

# Derive ROOT in this priority:
#   1. SKILLOGY_ROOT  — explicit override
#   2. CLAUDE_PLUGIN_ROOT — set by Claude Code when invoked from a hook
#   3. The script's own parent dir — works even when this script is run
#      directly from a slash command's `!` block where the env var is not
#      propagated into the subshell. Falling back to $(pwd) here would point
#      at the user's working directory, which has no dist/ or package.json.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SKILLOGY_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
LOG_DIR="${SKILLOGY_LOG_DIR:-/tmp/skillogy}"
mkdir -p "$LOG_DIR"

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
    # Surface this to the hook so the user sees a one-time actionable message
    node "$ROOT/scripts/state-set.mjs" docker-missing 1 2>/dev/null || true
else
    # Docker is available — clear any prior docker-missing flag the hook left
    node "$ROOT/scripts/state-set.mjs" docker-missing 0 2>/dev/null || true
fi

# Load .env
if [ -f "$ROOT/.env" ]; then
    set -a; source "$ROOT/.env"; set +a
fi

# 1. node_modules + dist
#
# In OSS install path the repo ships pre-bundled dist/ (esbuild --bundle inlines
# every npm dep), so node_modules is NOT required at runtime. We only run
# npm install + build when:
#   (a) bundled dist/ entrypoints are missing (broken checkout / dev clone), OR
#   (b) the developer opts in via SKILLOGY_DEV=1 to use source-edit workflow
have_bundles() {
    [ -f "$ROOT/dist/cli/index.js" ] && [ -f "$ROOT/dist/adapters/hook.js" ] \
        && [ -f "$ROOT/dist/adapters/mcp_server.js" ] && [ -f "$ROOT/dist/adapters/web_api.js" ]
}

if [ "${SKILLOGY_SKIP_BUILD:-}" != "1" ]; then
    needs_build=0
    if ! have_bundles; then
        needs_build=1
    fi
    if [ "${SKILLOGY_DEV:-}" = "1" ]; then
        needs_build=1
    fi

    if [ "$needs_build" = "1" ]; then
        if [ ! -d "$ROOT/node_modules" ]; then
            log "Installing node_modules (npm install) — see $LOG_DIR/install.log"
            (cd "$ROOT" && npm install) >>"$LOG_DIR/install.log" 2>&1 || {
                err "npm install failed — see $LOG_DIR/install.log"
                exit 1
            }
        fi
        log "Building bundles (npm run build) — see $LOG_DIR/build.log"
        (cd "$ROOT" && npm run build) >>"$LOG_DIR/build.log" 2>&1 || {
            err "npm run build failed — see $LOG_DIR/build.log"
            exit 1
        }
    else
        log "Bundles present — skipping npm install + build (set SKILLOGY_DEV=1 to force rebuild)"
    fi
fi

# 2. Neo4j
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

# 3. Incremental indexing (background)
if [ "${SKILLOGY_SKIP_INDEX:-}" != "1" ]; then
    # Wait briefly for Neo4j bolt
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

log "Bootstrap done. Logs: $LOG_DIR/"
exit 0
