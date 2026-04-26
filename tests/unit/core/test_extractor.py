"""Tests for skill_router.core.extractor — no real API calls made."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from skill_router.core.extractor import extract
from skill_router.domain.types import ParsedSkill, Signal, TriggerSurface


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parsed(
    name: str = "test-skill",
    description: str = "A test skill that does things.",
    body: str = "This skill reads files and edits code.",
) -> ParsedSkill:
    """Build a minimal ParsedSkill for testing."""
    return ParsedSkill(
        name=name,
        description=description,
        body=body,
        source_path=Path("/tmp/test-skill/SKILL.md"),
        raw_frontmatter={},
        warnings=[],
    )


class MockLLM:
    """Mock LLM client that returns a pre-configured response and counts calls."""

    def __init__(self, response: str | None = None, responses: list[str] | None = None) -> None:
        self._static = response
        self._responses = responses or []
        self.call_count = 0
        self.calls: list[dict] = []

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        self.call_count += 1
        self.calls.append({"prompt": prompt, "system": system})
        if self._responses:
            idx = min(self.call_count - 1, len(self._responses) - 1)
            return self._responses[idx]
        return self._static or ""


_FULL_RESPONSE = """{
  "intents": ["debug", "fix", "diagnose"],
  "signals": [
    {"kind": "keyword", "value": "tsc"},
    {"kind": "file_ext", "value": ".ts"}
  ],
  "exclusions": [
    {"kind": "file_ext", "value": ".js"}
  ]
}"""

_MINIMAL_RESPONSE = """{
  "intents": ["run"],
  "signals": [],
  "exclusions": []
}"""


# ---------------------------------------------------------------------------
# test_extract_with_full_response
# ---------------------------------------------------------------------------

def test_extract_with_full_response() -> None:
    """Mock LLM returns valid JSON with all fields; verify TriggerSurface populated correctly."""
    llm = MockLLM(response=_FULL_RESPONSE)
    parsed = _make_parsed()

    result = extract(parsed, llm)

    assert isinstance(result, TriggerSurface)
    assert result.skill_name == "test-skill"
    assert "debug" in result.intents
    assert "fix" in result.intents
    assert "diagnose" in result.intents

    assert len(result.signals) == 2
    signal_tuples = {(s.kind, s.value) for s in result.signals}
    assert ("keyword", "tsc") in signal_tuples
    assert ("file_ext", ".ts") in signal_tuples

    assert len(result.exclusions) == 1
    assert result.exclusions[0].kind == "file_ext"
    assert result.exclusions[0].value == ".js"

    assert result.extraction_cost_usd >= 0.0
    assert result.extraction_warnings == []
    assert llm.call_count == 1


# ---------------------------------------------------------------------------
# test_extract_with_minimal_response
# ---------------------------------------------------------------------------

def test_extract_with_minimal_response() -> None:
    """Mock LLM returns JSON with empty arrays except intents; no error raised."""
    llm = MockLLM(response=_MINIMAL_RESPONSE)
    parsed = _make_parsed()

    result = extract(parsed, llm)

    assert isinstance(result, TriggerSurface)
    assert result.skill_name == "test-skill"
    assert result.intents == ["run"]
    assert result.signals == []
    assert result.exclusions == []
    assert result.extraction_warnings == []


# ---------------------------------------------------------------------------
# test_extract_handles_invalid_json
# ---------------------------------------------------------------------------

def test_extract_handles_invalid_json() -> None:
    """Mock LLM returns malformed text; TriggerSurface has empty fields and a warning."""
    llm = MockLLM(response="Sorry, I cannot help with that. Here is some prose instead.")
    parsed = _make_parsed()

    result = extract(parsed, llm)

    assert isinstance(result, TriggerSurface)
    assert result.skill_name == "test-skill"
    assert result.intents == []
    assert result.signals == []
    assert result.exclusions == []
    assert len(result.extraction_warnings) > 0
    assert any("JSON" in w or "parse" in w.lower() for w in result.extraction_warnings)


# ---------------------------------------------------------------------------
# test_extract_normalizes_case_and_dedupes
# ---------------------------------------------------------------------------

def test_extract_normalizes_case_and_dedupes() -> None:
    """LLM returns mixed-case and duplicate intents; verify lowercase + deduped."""
    response = """{
  "intents": ["Fix", "fix", "DIAGNOSE"],
  "signals": [],
  "exclusions": []
}"""
    llm = MockLLM(response=response)
    parsed = _make_parsed()

    result = extract(parsed, llm)

    assert set(result.intents) == {"fix", "diagnose"}
    # All values must be lowercase
    for intent in result.intents:
        assert intent == intent.lower(), f"Intent not lowercased: {intent!r}"
    # No duplicates
    assert len(result.intents) == len(set(result.intents))


# ---------------------------------------------------------------------------
# test_extract_dedupes_signals
# ---------------------------------------------------------------------------

def test_extract_dedupes_signals() -> None:
    """Duplicate signals are deduplicated."""
    response = """{
  "intents": [],
  "signals": [
    {"kind": "keyword", "value": "tsc"},
    {"kind": "keyword", "value": "tsc"}
  ],
  "exclusions": []
}"""
    llm = MockLLM(response=response)
    parsed = _make_parsed()

    result = extract(parsed, llm)

    assert len(result.signals) == 1
    assert result.signals[0].kind == "keyword"
    assert result.signals[0].value == "tsc"


# ---------------------------------------------------------------------------
# test_extract_empty_arrays_acceptable
# ---------------------------------------------------------------------------

def test_extract_empty_arrays_acceptable() -> None:
    """All-empty response is valid; returns empty TriggerSurface with no warnings."""
    response = '{"intents": [], "signals": [], "exclusions": []}'
    llm = MockLLM(response=response)
    parsed = _make_parsed()

    result = extract(parsed, llm)

    assert isinstance(result, TriggerSurface)
    assert result.intents == []
    assert result.signals == []
    assert result.exclusions == []
    assert result.extraction_warnings == []


# ---------------------------------------------------------------------------
# test_extract_large_body_logs_warning
# ---------------------------------------------------------------------------

def test_extract_large_body_logs_warning() -> None:
    """Body > 100K chars produces a warning but still calls the LLM once."""
    large_body = "x" * 110_000
    response = '{"intents": ["big skill"], "signals": [], "exclusions": []}'
    llm = MockLLM(response=response)
    parsed = _make_parsed(body=large_body)

    result = extract(parsed, llm)

    assert llm.call_count == 1, "Must still make a single LLM call even for large bodies"
    assert len(result.extraction_warnings) >= 1
    assert any("100" in w or "chars" in w.lower() for w in result.extraction_warnings)


# ---------------------------------------------------------------------------
# test_extract_single_call_only
# ---------------------------------------------------------------------------

def test_extract_single_call_only() -> None:
    """extract() must make exactly one LLM call regardless of body size (within 100K)."""
    response = '{"intents": ["run"], "signals": [], "exclusions": []}'
    llm = MockLLM(response=response)
    parsed = _make_parsed(body="Normal sized body. " * 100)

    extract(parsed, llm)

    assert llm.call_count == 1


# ---------------------------------------------------------------------------
# test_extract_related_skills
# ---------------------------------------------------------------------------

def test_extract_related_skills() -> None:
    """LLM returns related_skills with mixed case and duplicates; verify lowercased + deduped."""
    response = '{"intents": [], "signals": [], "exclusions": [], "related_skills": ["foo", "Bar", "foo"]}'
    llm = MockLLM(response=response)
    parsed = _make_parsed(name="my-skill")

    result = extract(parsed, llm)

    assert result.related_skills == ["foo", "bar"]


# ---------------------------------------------------------------------------
# test_extract_drops_self_reference
# ---------------------------------------------------------------------------

def test_extract_drops_self_reference() -> None:
    """LLM returns related_skills containing the skill's own name; it must be stripped."""
    response = '{"intents": [], "signals": [], "exclusions": [], "related_skills": ["other-skill", "my-skill"]}'
    llm = MockLLM(response=response)
    parsed = _make_parsed(name="my-skill")

    result = extract(parsed, llm)

    assert "my-skill" not in result.related_skills
    assert result.related_skills == ["other-skill"]


# ---------------------------------------------------------------------------
# test_extract_handles_missing_related_skills_key
# ---------------------------------------------------------------------------

def test_extract_handles_missing_related_skills_key() -> None:
    """Old-style response without related_skills key yields empty list (graceful default)."""
    response = '{"intents": ["run"], "signals": [], "exclusions": []}'
    llm = MockLLM(response=response)
    parsed = _make_parsed()

    result = extract(parsed, llm)

    assert result.related_skills == []


# ---------------------------------------------------------------------------
# test_smoke_real_sample (skipped without API key)
# ---------------------------------------------------------------------------

def test_smoke_real_sample() -> None:
    """Extract from a real local SKILL.md using the real LLM client.

    Skipped if ANTHROPIC_API_KEY is unset and claude_agent_sdk is not importable.
    """
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_sdk = False
    try:
        import claude_agent_sdk  # noqa: F401
        has_sdk = True
    except ImportError:
        pass

    if not has_api_key and not has_sdk:
        pytest.skip("No LLM credentials available (ANTHROPIC_API_KEY unset and claude_agent_sdk not importable)")

    from skill_router.infra.llm import get_llm_client
    from skill_router.infra.scanner import scan_skills

    skills_dir = Path.home() / ".claude" / "skills"
    if not skills_dir.exists():
        pytest.skip(f"Skills directory not found: {skills_dir}")

    skills = scan_skills([skills_dir])
    if not skills:
        pytest.skip("No skills found in ~/.claude/skills/")

    sample = skills[0]
    llm = get_llm_client(model="claude-haiku-4-5")
    result = extract(sample, llm)

    assert isinstance(result, TriggerSurface)
    assert result.skill_name == sample.name
    assert len(result.intents) >= 1, (
        f"Expected >=1 intent, got {result.intents}. "
        f"Warnings: {result.extraction_warnings}"
    )
