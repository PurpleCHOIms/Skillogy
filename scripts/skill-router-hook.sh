#!/usr/bin/env bash
# UserPromptSubmit hook wrapper — invoked by Claude Code per turn.
# Reads prompt JSON from stdin, emits additionalContext JSON to stdout.
# Adjust SKILL_ROUTER_ROOT if the project lives elsewhere.

set -e
ROOT="${SKILL_ROUTER_ROOT:-/home/catow/GIT/Hackathon}"
exec "$ROOT/.venv/bin/python" -m skill_router.adapters.hook
