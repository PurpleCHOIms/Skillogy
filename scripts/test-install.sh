#!/usr/bin/env bash
# End-to-end install smoke test.
# Mirrors the OSS user experience: produce the exact npm tarball that
# `/plugin install` would deliver (via `npm pack`), extract it into a scratch
# directory WITHOUT node_modules, run the SessionStart bootstrap, then fire
# the hook with sample stdin and assert the contract holds.
#
# Hermetic by design — Neo4j and indexing are skipped so the test runs in CI
# without external services. The success path proves: (1) the npm tarball
# carries the bundled dist/, (2) bootstrap is fast and does no npm install
# because dist/ is already there, (3) the hook never crashes the user's session.
#
# Exit 0 = pass. Non-zero = fail.

set -euo pipefail

SRC_ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:-/tmp/skillogy-install-test}

echo "[install-test] src=$SRC_ROOT  dest=$DEST"

# 1. Build bundles + pack into the same tarball npm publish would produce.
(cd "$SRC_ROOT" && npm run build >/dev/null 2>&1)
TARBALL=$(cd "$SRC_ROOT" && npm pack --silent)
echo "[install-test] packed: $TARBALL ($(wc -c <"$SRC_ROOT/$TARBALL") bytes)"

# 2. Extract into clean scratch dir (no node_modules, no source).
rm -rf "$DEST"
mkdir -p "$DEST"
tar -xzf "$SRC_ROOT/$TARBALL" -C "$DEST" --strip-components=1
rm -f "$SRC_ROOT/$TARBALL"

# 3. Sanity: the bundled hook.js MUST be present (this is the whole point of
#    shipping pre-bundled dist/ in the npm package).
if [ ! -f "$DEST/dist/adapters/hook.js" ]; then
    echo "[install-test] FAIL: dist/adapters/hook.js missing in npm tarball — check package.json files[]"
    exit 1
fi
echo "[install-test] dist/adapters/hook.js present ($(wc -c <"$DEST/dist/adapters/hook.js") bytes)"

# 4. Run bootstrap. Drive the install in BLOCKING mode so the test exercises
#    the full happy path inside one process (background mode is for real
#    SessionStart use). Do NOT pass SKILLOGY_DATA — let bootstrap pick its
#    own fallback so we exercise the symlink-into-ROOT branch the production
#    install path actually depends on.
START=$(date +%s)
SKILLOGY_INSTALL_BLOCKING=1 \
    SKILLOGY_SKIP_NEO4J=1 SKILLOGY_SKIP_INDEX=1 \
    SKILLOGY_LOG_DIR="$DEST/.logs" \
    bash "$DEST/scripts/skillogy-bootstrap.sh" >/dev/null 2>&1 || {
    echo "[install-test] FAIL: bootstrap exited non-zero"
    cat "$DEST/.logs/install.log" 2>/dev/null | tail -10 >&2 || true
    exit 1
}
ELAPSED=$(( $(date +%s) - START ))
echo "[install-test] bootstrap took ${ELAPSED}s (includes npm install)"

if [ "$ELAPSED" -gt 120 ]; then
    echo "[install-test] FAIL: bootstrap took ${ELAPSED}s (>120s)"
    exit 1
fi
# DATA defaulted to $DEST/.skillogy-data (CLAUDE_PLUGIN_DATA unset, ROOT inferred).
DATA_FALLBACK="$DEST/.skillogy-data"
if [ ! -d "$DATA_FALLBACK/node_modules/@anthropic-ai/claude-agent-sdk" ]; then
    echo "[install-test] FAIL: $DATA_FALLBACK/node_modules/@anthropic-ai/claude-agent-sdk missing — npm install did not bring SDK"
    exit 1
fi
# Critical: the symlink-into-ROOT branch must have run so default ESM
# resolution can find the deps from dist/cli/index.js.
if [ ! -L "$DEST/node_modules" ] || [ ! -e "$DEST/node_modules/@anthropic-ai/claude-agent-sdk" ]; then
    echo "[install-test] FAIL: $DEST/node_modules symlink missing/broken — the bug we shipped in v0.0.4"
    ls -la "$DEST/node_modules" 2>&1 >&2 || true
    exit 1
fi
DATA="$DATA_FALLBACK"

# 5. Fire the hook with sample stdin. With Neo4j skipped, the cold-start UX
#    branch will detect "down" and emit a friendly notification (passthrough
#    is also acceptable — both are valid hookSpecificOutput shapes).
#    SKILLOGY_DATA must be honored so the hook sets NODE_PATH correctly.
SAMPLE='{"prompt":"build a rag system","hook_event_name":"UserPromptSubmit","session_id":"test"}'
HOOK_OUT=$(echo "$SAMPLE" | XDG_STATE_HOME="$DEST/.state" \
    SKILLOGY_DATA="$DATA" \
    NEO4J_URI=bolt://localhost:1 \
    SKILLOGY_HEALTH_PROBE_MS=300 \
    timeout 15 bash "$DEST/scripts/skillogy-hook.sh" 2>"$DEST/.logs/hook.stderr" || echo "TIMEOUT")
if [ "$HOOK_OUT" = "TIMEOUT" ]; then
    echo "[install-test] FAIL: hook timed out (>10s)"
    cat "$DEST/.logs/hook.stderr" >&2
    exit 1
fi

# 6. Validate JSON shape.
echo "$HOOK_OUT" | node -e "
let raw = '';
process.stdin.on('data', (c) => raw += c);
process.stdin.on('end', () => {
  try {
    const j = JSON.parse(raw);
    if (j.hookSpecificOutput?.hookEventName !== 'UserPromptSubmit') {
      console.error('FAIL: bad hookEventName:', JSON.stringify(j));
      process.exit(2);
    }
    if (typeof j.hookSpecificOutput.additionalContext !== 'string') {
      console.error('FAIL: additionalContext not a string:', JSON.stringify(j));
      process.exit(2);
    }
    console.log('[install-test] hook returned valid JSON; additionalContext length=' + j.hookSpecificOutput.additionalContext.length);
  } catch (e) {
    console.error('FAIL: hook output is not valid JSON:', raw);
    process.exit(2);
  }
});
"

# Cleanup unless caller wants to inspect.
if [ "${SKILLOGY_INSTALL_TEST_KEEP:-0}" != "1" ]; then
    rm -rf "$DEST"
fi

echo "[install-test] PASS"
