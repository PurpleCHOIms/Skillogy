#!/usr/bin/env bash
# Skillogy SessionStart bootstrap.
# Idempotent. Returns within seconds — heavy work (npm install, indexing) is
# forked in the background so the SessionStart 30s timeout never bites.
#
# Honors:
#   SKILLOGY_ROOT             → repo root (default: ${CLAUDE_PLUGIN_ROOT}, then script's parent dir)
#   SKILLOGY_DATA             → persistent dep dir (default: ${CLAUDE_PLUGIN_DATA}, then $ROOT/.skillogy-data)
#   SKILLOGY_SKIP_BOOTSTRAP=1 → no-op
#   SKILLOGY_SKIP_NEO4J=1     → skip Neo4j docker bringup
#   SKILLOGY_SKIP_INDEX=1     → skip background indexing
#   SKILLOGY_SKIP_INSTALL=1   → skip npm install in DATA
#   SKILLOGY_DEV=1            → also rebuild dist/ from source (requires source checkout)
#   SKILLOGY_INSTALL_BLOCKING=1 → run install in foreground (used by test-install.sh)

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

# 0. Prereq check.
if ! command -v node >/dev/null; then err "node is required (>= 20)."; exit 1; fi
if ! command -v npm  >/dev/null; then err "npm is required.";          exit 1; fi
if ! command -v docker >/dev/null; then
    log "WARNING: docker not found — Neo4j auto-start will be skipped."
    node "$SCRIPT_DIR/state-set.mjs" docker-missing 1 2>/dev/null || true
else
    node "$SCRIPT_DIR/state-set.mjs" docker-missing 0 2>/dev/null || true
fi

# Load .env (dev workflow only).
if [ -f "$ROOT/.env" ]; then
    set -a; source "$ROOT/.env"; set +a
fi

# 1. (Optional) dev rebuild from source.
if [ "${SKILLOGY_DEV:-}" = "1" ] && [ -f "$ROOT/scripts/build.mjs" ]; then
    if [ ! -d "$ROOT/node_modules" ]; then
        log "[dev] npm install in $ROOT → $LOG_DIR/install.log"
        (cd "$ROOT" && npm install) >>"$LOG_DIR/install.log" 2>&1 || {
            err "[dev] npm install in $ROOT failed — see $LOG_DIR/install.log"; exit 1;
        }
    fi
    log "[dev] Rebuilding bundles → $LOG_DIR/build.log"
    (cd "$ROOT" && npm run build) >>"$LOG_DIR/build.log" 2>&1 || {
        err "[dev] npm run build failed — see $LOG_DIR/build.log"; exit 1;
    }
fi

# 2. Sync the plugin's runtime npm dependencies into ${DATA}.
#    Uses an `.installing` sentinel + delayed package.json commit so a killed
#    install never leaves a permanent half-state. The hook reads .installing
#    to surface a one-time "installing dependencies" message.
needs_install=0
if [ "${SKILLOGY_SKIP_INSTALL:-}" != "1" ]; then
    if [ -f "$DATA/.installing" ]; then needs_install=1; fi
    if ! diff -q "$ROOT/package.json" "$DATA/package.json" >/dev/null 2>&1; then needs_install=1; fi
fi

run_install() {
    # Body of the install — runs either inline (BLOCKING=1) or as a backgrounded
    # subshell. Writes the plugin's runtime deps into $DATA/node_modules,
    # commits package.json + symlink only on success, and clears the sentinel.
    set +e
    {
        echo "[skillogy-bootstrap] $(date -Iseconds) starting npm install in $DATA"
        # Stage the package.json into a sibling file so a kill mid-install
        # doesn't leave a "matching" cached package.json that suppresses retry.
        cp "$ROOT/package.json" "$DATA/package.json"
        if (cd "$DATA" && npm install --omit=dev --no-audit --no-fund); then
            echo "[skillogy-bootstrap] $(date -Iseconds) npm install OK; linking node_modules"
            ln -sfn "$DATA/node_modules" "$ROOT/node_modules" 2>/dev/null || \
                echo "[skillogy-bootstrap] WARN: could not symlink $ROOT/node_modules (read-only?)"
            rm -f "$DATA/.installing" "$DATA/.installing-notified"
            return 0
        else
            echo "[skillogy-bootstrap] $(date -Iseconds) npm install FAILED — leaving sentinel for retry"
            rm -f "$DATA/package.json"   # so next bootstrap diffs as changed
            return 1
        fi
    } >>"$LOG_DIR/install.log" 2>&1
}

if [ "$needs_install" = 1 ]; then
    touch "$DATA/.installing"
    if [ "${SKILLOGY_INSTALL_BLOCKING:-}" = "1" ]; then
        log "Installing plugin dependencies (blocking) → $LOG_DIR/install.log"
        run_install || { err "npm install failed — see $LOG_DIR/install.log"; exit 1; }
    else
        log "Forking dependency install in background → $LOG_DIR/install.log"
        # `bash -c` ensures the function runs in a detached subshell.
        nohup bash -c "$(declare -f run_install); ROOT='$ROOT' DATA='$DATA' LOG_DIR='$LOG_DIR' run_install" \
            >/dev/null 2>&1 &
        disown
    fi
fi

# Even when install runs in the background, expose NODE_PATH for any inline
# node invocation that follows. Default ESM resolution still needs the symlink
# (which the install path creates after completion).
export NODE_PATH="$DATA/node_modules${NODE_PATH:+:$NODE_PATH}"

# 3. Neo4j (cheap when already up).
need_neo4j_up() { ! curl -s -o /dev/null -m 1 http://localhost:7474; }
if [ "${SKILLOGY_SKIP_NEO4J:-}" != "1" ] && need_neo4j_up; then
    if command -v docker >/dev/null && [ -f "$ROOT/docker-compose.yml" ]; then
        log "Starting Neo4j (docker compose)..."
        (cd "$ROOT" && docker compose up -d neo4j) >>"$LOG_DIR/neo4j.log" 2>&1 || \
            log "  neo4j start failed (see $LOG_DIR/neo4j.log)"
    else
        log "  docker or docker-compose.yml missing — skipping Neo4j"
    fi
elif [ "${SKILLOGY_SKIP_NEO4J:-}" != "1" ]; then
    log "Neo4j already up on :7474"
fi

# 4. Background indexing — defer until install is done so the indexer can
#    actually load neo4j-driver. Watcher exits as soon as deps are ready or
#    after a generous timeout (5 min) to avoid orphan loops.
if [ "${SKILLOGY_SKIP_INDEX:-}" != "1" ]; then
    SCOPES="${SKILLOGY_INDEX_SCOPES:-user,project}"
    log "Scheduling background indexing (scopes=$SCOPES) → $LOG_DIR/index.log"
    nohup bash -c "
        set +e
        # Wait for install to finish (at most 5 min).
        for _ in \$(seq 1 300); do
            [ ! -f '$DATA/.installing' ] && [ -d '$DATA/node_modules' ] && break
            sleep 1
        done
        # Wait for Neo4j bolt (at most 60s).
        for _ in \$(seq 1 60); do
            curl -s -o /dev/null -m 1 http://localhost:7474 && break
            sleep 1
        done
        echo \"[skillogy-bootstrap] \$(date -Iseconds) starting indexer\" >>'$LOG_DIR/index.log'
        node '$ROOT/dist/cli/index.js' index --incremental --workers 8 --scopes '$SCOPES' \
            >>'$LOG_DIR/index.log' 2>&1
    " >/dev/null 2>&1 &
    disown
fi

log "Bootstrap done. ROOT=$ROOT  DATA=$DATA  Logs=$LOG_DIR/"
exit 0
