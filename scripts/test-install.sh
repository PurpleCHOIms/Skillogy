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

# 4. Run bootstrap with services skipped — must be fast and not require npm install.
START=$(date +%s)
SKILLOGY_SKIP_NEO4J=1 SKILLOGY_SKIP_INDEX=1 \
    SKILLOGY_LOG_DIR="$DEST/.logs" \
    bash "$DEST/scripts/skillogy-bootstrap.sh" >/dev/null 2>&1 || {
    echo "[install-test] FAIL: bootstrap exited non-zero"
    exit 1
}
ELAPSED=$(( $(date +%s) - START ))
echo "[install-test] bootstrap took ${ELAPSED}s"

if [ "$ELAPSED" -gt 15 ]; then
    echo "[install-test] FAIL: bootstrap took ${ELAPSED}s (>15s) — bundles probably not in tarball"
    exit 1
fi
if [ -d "$DEST/node_modules" ]; then
    echo "[install-test] FAIL: node_modules was created — bundles should make npm install unnecessary"
    exit 1
fi

# 5. Fire the hook with sample stdin. With Neo4j skipped, the cold-start UX
#    branch will detect "down" and emit a friendly notification (passthrough
#    is also acceptable — both are valid hookSpecificOutput shapes).
SAMPLE='{"prompt":"build a rag system","hook_event_name":"UserPromptSubmit","session_id":"test"}'
HOOK_OUT=$(echo "$SAMPLE" | XDG_STATE_HOME="$DEST/.state" \
    NEO4J_URI=bolt://localhost:1 \
    SKILLOGY_HEALTH_PROBE_MS=300 \
    timeout 10 node "$DEST/dist/adapters/hook.js" 2>"$DEST/.logs/hook.stderr" || echo "TIMEOUT")
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
