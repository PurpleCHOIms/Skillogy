"""Tests for bench.eval_set — personal skill trigger track only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bench.eval_set import _normalize_skill_name, build_eval_set
from skill_router.domain.types import ParsedSkill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(name: str, description: str = "", path_prefix: str = "/fake/skills") -> ParsedSkill:
    return ParsedSkill(
        name=name,
        description=description or f"This skill handles {name} operations.",
        body="## Usage\nUse this skill for things.",
        source_path=Path(f"{path_prefix}/{name}/SKILL.md"),
        raw_frontmatter={"name": name, "description": description},
        warnings=[],
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


REQUIRED_KEYS = {"id", "query", "gold_skill_name"}


# ---------------------------------------------------------------------------
# 1. test_normalize_skill_name
# ---------------------------------------------------------------------------

def test_normalize_skill_name() -> None:
    assert _normalize_skill_name("MySkill") == "myskill"
    assert _normalize_skill_name("my_skill") == "my-skill"
    assert _normalize_skill_name("  My_Skill  ") == "my-skill"
    assert _normalize_skill_name("UPPER_CASE") == "upper-case"
    assert _normalize_skill_name("already-lower") == "already-lower"


# ---------------------------------------------------------------------------
# 2. test_build_eval_set_basic
# ---------------------------------------------------------------------------

def test_build_eval_set_basic(tmp_path: Path) -> None:
    """5 skills + mocked LLM returning 2 queries each → 10 queries, 5 unique gold names."""
    out_file = tmp_path / "eval.jsonl"

    fake_skills = [_make_skill(f"skill-{i}", f"Description for skill {i}") for i in range(5)]

    mock_llm = MagicMock()
    mock_llm.complete.return_value = '["first query", "second query"]'

    counts = build_eval_set(out_file, n_skills=10, seed=42, llm=mock_llm, skills=fake_skills)

    assert out_file.exists()
    assert counts["queries_written"] == 10
    assert counts["unique_gold"] == 5
    assert counts["skills_sampled"] == 5

    entries = _read_jsonl(out_file)
    assert len(entries) == 10
    gold_names = {e["gold_skill_name"] for e in entries}
    assert len(gold_names) == 5


# ---------------------------------------------------------------------------
# 3. test_build_eval_set_uses_description_fallback_when_no_llm
# ---------------------------------------------------------------------------

def test_build_eval_set_uses_description_fallback_when_no_llm(tmp_path: Path) -> None:
    """When llm=None, description is used verbatim as the single query."""
    out_file = tmp_path / "eval.jsonl"
    description = "Unique description for fallback test"
    fake_skills = [_make_skill("fallback-skill", description)]

    with patch("bench.eval_set.get_llm_client", side_effect=RuntimeError("no creds")):
        counts = build_eval_set(out_file, n_skills=5, seed=42, llm=None, skills=fake_skills)

    entries = _read_jsonl(out_file)
    assert len(entries) == 1
    assert entries[0]["query"] == description
    assert entries[0]["gold_skill_name"] == "fallback-skill"
    assert counts["queries_written"] == 1


# ---------------------------------------------------------------------------
# 4. test_build_eval_set_deterministic_with_seed
# ---------------------------------------------------------------------------

def test_build_eval_set_deterministic_with_seed(tmp_path: Path) -> None:
    """Same seed twice produces identical JSONL bytes."""
    out1 = tmp_path / "run1.jsonl"
    out2 = tmp_path / "run2.jsonl"

    fake_skills = [_make_skill(f"skill-{i}", f"Description {i}") for i in range(10)]

    mock_llm = MagicMock()
    mock_llm.complete.return_value = '["query one", "query two"]'

    build_eval_set(out1, n_skills=5, seed=42, llm=mock_llm, skills=list(fake_skills))
    build_eval_set(out2, n_skills=5, seed=42, llm=mock_llm, skills=list(fake_skills))

    assert out1.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. test_build_eval_set_diversity_guard_raises
# ---------------------------------------------------------------------------

def test_build_eval_set_diversity_guard_raises(tmp_path: Path) -> None:
    """n_skills >= 100 with < 100 unique skills → RuntimeError about diversity."""
    out_file = tmp_path / "eval.jsonl"

    # Only 5 unique skills — far below the 100 unique gold guard
    fake_skills = [_make_skill(f"skill-{i}", f"Description {i}") for i in range(5)]

    mock_llm = MagicMock()
    mock_llm.complete.return_value = '["trigger query"]'

    with pytest.raises(RuntimeError, match="Diversity guard"):
        build_eval_set(out_file, n_skills=100, seed=42, llm=mock_llm, skills=fake_skills)


# ---------------------------------------------------------------------------
# 6. test_build_eval_set_diversity_guard_skipped_for_small_n
# ---------------------------------------------------------------------------

def test_build_eval_set_diversity_guard_skipped_for_small_n(tmp_path: Path) -> None:
    """n_skills < 100 → diversity guard not applied even with few unique skills."""
    out_file = tmp_path / "eval.jsonl"

    fake_skills = [_make_skill(f"skill-{i}", f"Description {i}") for i in range(3)]

    mock_llm = MagicMock()
    mock_llm.complete.return_value = '["trigger query"]'

    # Should not raise even though unique_gold < 100
    counts = build_eval_set(out_file, n_skills=99, seed=42, llm=mock_llm, skills=fake_skills)
    assert counts["unique_gold"] == 3
    assert counts["queries_written"] == 3


# ---------------------------------------------------------------------------
# 7. test_jsonl_schema_valid
# ---------------------------------------------------------------------------

def test_jsonl_schema_valid(tmp_path: Path) -> None:
    """Every output line is valid JSON with id, query, gold_skill_name keys."""
    out_file = tmp_path / "eval.jsonl"

    fake_skills = [_make_skill(f"skill-{i}", f"Description {i}") for i in range(4)]

    mock_llm = MagicMock()
    mock_llm.complete.return_value = '["request one", "request two", "request three"]'

    build_eval_set(out_file, n_skills=10, seed=0, llm=mock_llm, skills=fake_skills)

    raw_lines = out_file.read_text(encoding="utf-8").splitlines()
    assert raw_lines, "Output file must not be empty"

    for i, line in enumerate(raw_lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"Line {i} is not valid JSON: {exc}\nContent: {line!r}")

        assert REQUIRED_KEYS.issubset(obj.keys()), (
            f"Line {i} missing required keys. Found: {set(obj.keys())}"
        )
        assert isinstance(obj["id"], str) and obj["id"], "id must be non-empty string"
        assert isinstance(obj["query"], str) and obj["query"], "query must be non-empty string"
        assert isinstance(obj["gold_skill_name"], str) and obj["gold_skill_name"], (
            "gold_skill_name must be non-empty string"
        )
        # Must NOT have old 'source' field
        assert "source" not in obj, "New schema must not include 'source' field"
