#!/usr/bin/env bash
# Set up an isolated Claude Code HOME for benchmarking.
# Only testbed skills are visible; user skills and plugins are excluded.
# Hooks are disabled so the hook under test doesn't fire during native condition.
#
# Usage:
#   source scripts/bench-home-setup.sh [TESTBED_ROOT]
#   # or
#   BENCH_HOME=$(bash scripts/bench-home-setup.sh [TESTBED_ROOT] && echo /tmp/bench-home)

set -euo pipefail

TESTBED_ROOT="${1:-$HOME/skill-router-testbed/skills}"
BENCH_HOME="/tmp/skill-router-bench-home"

echo "Setting up bench HOME at $BENCH_HOME" >&2
echo "Testbed root: $TESTBED_ROOT" >&2

# Clean and recreate
rm -rf "$BENCH_HOME"
mkdir -p "$BENCH_HOME/.claude/skills"

# Minimal settings.json — no hooks, no plugins
cat > "$BENCH_HOME/.claude/settings.json" <<'EOF'
{
  "hooks": {}
}
EOF

# Symlink auth sessions from real HOME so claude binary can authenticate
REAL_CLAUDE="$HOME/.claude"
for auth_dir in sessions statsig; do
    if [ -d "$REAL_CLAUDE/$auth_dir" ]; then
        ln -sf "$REAL_CLAUDE/$auth_dir" "$BENCH_HOME/.claude/$auth_dir"
    fi
done

# Symlink each testbed skill directory
linked=0
for skill_dir in "$TESTBED_ROOT"/*/; do
    skill_name="$(basename "$skill_dir")"
    if [ -f "$skill_dir/SKILL.md" ]; then
        ln -sf "$skill_dir" "$BENCH_HOME/.claude/skills/$skill_name"
        linked=$((linked + 1))
    fi
done

echo "Linked $linked testbed skills into $BENCH_HOME/.claude/skills/" >&2
echo "$BENCH_HOME"
