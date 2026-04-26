"""UserPromptSubmit hook entry — strict trigger path for Claude Code.

Reads JSON from stdin per Claude Code hook protocol, calls the routing core,
emits stdout JSON with hookSpecificOutput.additionalContext containing the
matched SKILL.md body.

Claude Code hook input schema (verified from docs):
  {
    "session_id": str,
    "transcript_path": str,
    "cwd": str,
    "permission_mode": str,
    "hook_event_name": "UserPromptSubmit",
    "prompt": str
  }

Claude Code hook output schema (verified from docs):
  {
    "hookSpecificOutput": {
      "hookEventName": "UserPromptSubmit",
      "additionalContext": str
    }
  }

Non-JSON stdout is also accepted as plain context, but we use the structured
form so Claude Code can distinguish context from control fields.

Honor SKILL_ROUTER_DISABLE=1 to short-circuit (no injection).
Honor SKILL_ROUTER_MIN_SCORE (default 1.0) — below this, no injection.
Latency budget: < 500 ms p95.
"""

from __future__ import annotations

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _emit_passthrough() -> None:
    """Emit empty hookSpecificOutput so Claude Code falls back to native discovery."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ""}}))


def main() -> int:
    if os.environ.get("SKILL_ROUTER_DISABLE") == "1":
        _emit_passthrough()
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        _emit_passthrough()
        return 0

    # Extract user prompt per Claude Code hook input schema
    prompt = payload.get("prompt") or ""
    if not prompt.strip():
        _emit_passthrough()
        return 0

    # Default 0.4 (was 1.0 — too strict, effectively always passthrough)
    min_score = float(os.environ.get("SKILL_ROUTER_MIN_SCORE", "0.4"))

    # Lazy-import the heavy stuff so a disabled hook stays cheap
    from skill_router.core.router import Router

    try:
        router = Router()
        # GraphRAG: LLM extracts intent/signals from query, graph traversal scores skills,
        # LLM re-ranks top-K. load_body=False since we only return the name.
        result = router.find_skill(query=prompt, top_k=3, judge=True, extract=True, load_body=False)
    except Exception as exc:
        logger.warning("skill-router hook: routing failed (%s) — passthrough.", exc)
        _emit_passthrough()
        return 0

    if not result.skill_name or result.score < min_score:
        _emit_passthrough()
        return 0

    # Return skill NAME only — Claude Code loads it natively via Skill tool
    related_names = [
        a["name"] for a in result.alternatives[:2]
        if isinstance(a.get("name"), str) and a.get("via") == "relates_to"
    ]

    lines = [
        f'[skill-router] Relevant skill detected: `{result.skill_name}` (score={result.score:.2f})',
        f'Load it with: Skill({{ skill: "{result.skill_name}" }})',
    ]
    if related_names:
        lines.append(f'Related skills: {", ".join(f"`{r}`" for r in related_names)}')

    additional_context = "\n".join(lines) + "\n"

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
