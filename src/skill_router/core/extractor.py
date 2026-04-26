"""LLM-based extractor: ParsedSkill -> TriggerSurface.

Single LLM call per skill with the full body. If body > 100K chars, a warning
is logged but the full body is still passed (Gemini Pro handles large context).
Cost tracking is set to 0.0 to avoid model-specific pricing complexity.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from skill_router.infra.llm import LLMClient
from skill_router.domain.types import ParsedSkill, Signal, TriggerSurface

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Closed vocabulary for validation
# ---------------------------------------------------------------------------
_VALID_SIGNAL_KINDS = frozenset({"keyword", "tool_name", "file_ext", "error_pattern", "pattern"})

_LARGE_BODY_THRESHOLD = 100_000  # chars; log warning above this

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a TRIGGER SURFACE ANALYST for Claude Code skills. Your job is to deeply
reason about a skill's content and infer the FULL RANGE of user situations that
should activate it — going beyond what the skill explicitly states.

## Your reasoning process (do this internally before outputting JSON):
1. UNDERSTAND the skill's expertise domain: what knowledge, patterns, or workflows does it encode?
2. INFER user goals: what tasks, problems, or decisions would a user be working on when this skill becomes useful? Think from the USER's perspective, not the skill's perspective.
3. DIVERSIFY intents: cover different user experience levels, phrasings, and angles. A senior engineer and a beginner would describe the same need differently.
4. SCAN the full body for technical signals: tool names, file extensions, error code patterns, framework names, API names, specific keywords that appear in real user messages.
5. IDENTIFY exclusions: only when the body explicitly says "do NOT use for X" or when using the skill for a clearly wrong context would mislead the user.
6. FIND related skills: skill names referenced in backticks, "see also", "use X first/after" patterns, or complementary skills implied by the domain.

## Output STRICT JSON with no preamble or markdown fences:
{
  "intents": [
    "build a rag pipeline with chroma",
    "set up document retrieval for my chatbot",
    "chunk and embed pdfs for search",
    "implement semantic search over documents",
    "connect langchain to a vector database"
  ],
  "signals": [
    {"kind": "keyword",       "value": "rag"},
    {"kind": "keyword",       "value": "retrieval"},
    {"kind": "keyword",       "value": "chroma"},
    {"kind": "keyword",       "value": "faiss"},
    {"kind": "keyword",       "value": "embeddings"},
    {"kind": "keyword",       "value": "vector store"},
    {"kind": "tool_name",     "value": "chromadb"},
    {"kind": "keyword",       "value": "document loader"},
    {"kind": "keyword",       "value": "text splitter"}
  ],
  "exclusions": [],
  "related_skills": ["langchain-fundamentals", "langsmith-trace"]
}

## Intent rules:
- 5–15 phrases. Short, lowercase, conversational. Start with action verbs (build, set up, implement, debug, understand, migrate, configure, use, connect, create).
- Cover MULTIPLE angles: what the user wants to BUILD, what PROBLEM they have, what CONCEPT they want to understand.
- Do NOT copy "when to use" text verbatim — rephrase into natural user language.
- Do NOT include the skill name itself as an intent.

## Signal rules:
- Extract from the FULL BODY: framework names, API names, CLI commands, library imports, error code patterns, config file names, file extensions.
- kind ∈ {keyword, file_ext, tool_name, error_pattern, pattern}
- Values lowercase. Include both short forms and full forms (e.g., "rag" AND "retrieval-augmented generation").
- tool_name: only for actual executable tool/CLI names.
- file_ext: must start with dot (e.g., ".ts", ".py").
- error_pattern: regex fragment that matches real error strings.

## Exclusion rules:
- Only add when there's a clear boundary in the skill body ("do not use for X", "this is NOT for Y").
- Most skills have zero exclusions.

## Related skills rules:
- Look for backtick `skill-name` references, "see also", "use X first/after", or complementary skills implied by the domain.
- Use exact skill names as they appear (lowercase with hyphens).
- Max 5 related skills. Empty array if none.
"""


def _build_prompt(name: str, description: str, body: str, frontmatter: dict | None = None) -> str:
    """Build the user prompt for extraction, including frontmatter context."""
    parts = [f"Skill name: {name}", f"Skill description: {description}"]
    if frontmatter:
        # Include any explicit trigger hints from frontmatter
        for key in ("trigger", "when_to_use", "use_when", "tags", "category"):
            if key in frontmatter:
                parts.append(f"Frontmatter {key}: {frontmatter[key]}")
    parts.append(f"\nSkill body:\n{body}")
    return "\n".join(parts)


def _parse_llm_response(response: str) -> dict[str, Any] | None:
    """Parse JSON from LLM response. Returns None on failure."""
    text = response.strip()
    # Strip markdown code fences if the model added them despite instructions
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_signals(raw: Any, skill_name: str, field_name: str) -> list[Signal]:
    """Parse and validate a list of signal dicts into Signal objects."""
    if not isinstance(raw, list):
        logger.warning("'%s' is not a list for skill %s", field_name, skill_name)
        return []

    signals: list[Signal] = []
    seen: set[tuple[str, str]] = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        kind = str(item.get("kind", "")).strip().lower()
        if kind not in _VALID_SIGNAL_KINDS:
            logger.warning(
                "Dropping %s entry with invalid kind %r for skill %s",
                field_name, kind, skill_name,
            )
            continue

        value = str(item.get("value", "")).strip().lower()
        if not value:
            logger.warning(
                "Dropping %s entry with empty value for skill %s",
                field_name, skill_name,
            )
            continue

        key = (kind, value)
        if key in seen:
            continue
        seen.add(key)
        signals.append(Signal(kind=kind, value=value))

    return signals


def _parse_intents(raw: Any, skill_name: str) -> list[str]:
    """Parse and validate intents list — lowercase strings, deduped."""
    if not isinstance(raw, list):
        logger.warning("'intents' is not a list for skill %s", skill_name)
        return []

    seen: set[str] = set()
    intents: list[str] = []
    for item in raw:
        v = str(item).strip().lower()
        if v and v not in seen:
            seen.add(v)
            intents.append(v)
    return intents


def _parse_related_skills(raw: Any, skill_name: str) -> list[str]:
    """Parse related_skills list — lowercase, deduped, no self-reference, no empty strings."""
    if not isinstance(raw, list):
        return []

    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        v = str(item).strip().lower()
        if not v:
            continue
        if v == skill_name.lower():
            continue
        if v in seen:
            continue
        seen.add(v)
        result.append(v)
    return result


def _empty_surface(parsed: ParsedSkill, warnings: list[str]) -> TriggerSurface:
    """Return an empty TriggerSurface with warnings."""
    return TriggerSurface(
        skill_name=parsed.name,
        intents=[],
        signals=[],
        exclusions=[],
        extraction_cost_usd=0.0,
        extraction_warnings=warnings,
    )


def extract(parsed: ParsedSkill, llm: LLMClient) -> TriggerSurface:
    """Extract the trigger surface from a ParsedSkill via a single LLM call.

    Uses a single call with the full body (no chunking). If body > 100K chars,
    logs a warning but still passes the full content. Cost is set to 0.0 as
    pricing varies by model and is not tracked here.
    """
    warnings: list[str] = []

    if len(parsed.body) > _LARGE_BODY_THRESHOLD:
        msg = f"Body is {len(parsed.body)} chars (> {_LARGE_BODY_THRESHOLD}); passing full body to LLM"
        logger.warning(msg)
        warnings.append(msg)

    prompt = _build_prompt(parsed.name, parsed.description, parsed.body, parsed.raw_frontmatter or None)

    try:
        response = llm.complete(
            prompt=prompt,
            system=_SYSTEM_PROMPT,
            max_tokens=8192,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        msg = f"LLM call failed: {exc}"
        logger.warning(msg)
        warnings.append(msg)
        return _empty_surface(parsed, warnings)

    parsed_data = _parse_llm_response(response)
    if parsed_data is None:
        msg = f"Failed to parse JSON from LLM response: {response[:200]!r}"
        logger.warning(msg)
        warnings.append(msg)
        return _empty_surface(parsed, warnings)

    intents = _parse_intents(parsed_data.get("intents"), parsed.name)
    signals = _parse_signals(parsed_data.get("signals"), parsed.name, "signals")
    exclusions = _parse_signals(parsed_data.get("exclusions"), parsed.name, "exclusions")
    related_skills = _parse_related_skills(parsed_data.get("related_skills"), parsed.name)

    return TriggerSurface(
        skill_name=parsed.name,
        intents=intents,
        signals=signals,
        exclusions=exclusions,
        related_skills=related_skills,
        extraction_cost_usd=0.0,
        extraction_warnings=warnings,
    )


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import dataclasses

    from skill_router.infra.llm import get_llm_client
    from skill_router.infra.scanner import parse_skill_md

    parsed = parse_skill_md(Path(sys.argv[1]))
    if parsed is None:
        print(f"Could not parse {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)
    llm = get_llm_client()
    result = extract(parsed, llm)
    print(json.dumps(dataclasses.asdict(result), default=str, indent=2))
