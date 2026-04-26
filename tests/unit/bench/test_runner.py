"""Tests for bench.runner — all mocked, no real LLM/network calls."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bench.runner import (
    QueryTrace,
    _bootstrap_ci,
    aggregate,
    native_top_k,
    sog_top_k,
)
from skillogy.domain.types import ParsedSkill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(name: str, description: str = "") -> ParsedSkill:
    return ParsedSkill(
        name=name,
        description=description or f"Handles {name} operations.",
        body="## Usage\nUse this skill.",
        source_path=Path(f"/fake/skills/{name}/SKILL.md"),
        raw_frontmatter={"name": name},
        warnings=[],
    )


def _make_llm(response: str) -> MagicMock:
    llm = MagicMock()
    llm.complete.return_value = response
    return llm


# ---------------------------------------------------------------------------
# 1. test_native_top_k_parses_json
# ---------------------------------------------------------------------------

def test_native_top_k_parses_json() -> None:
    skills = [_make_skill("deploy"), _make_skill("review"), _make_skill("test")]
    llm = _make_llm('{"top_k": ["deploy", "review", "test"]}')

    top_k, tokens, latency = native_top_k("deploy my app", skills, llm)

    assert top_k == ["deploy", "review", "test"]
    assert tokens > 0
    assert latency >= 0.0


def test_native_top_k_limits_to_k() -> None:
    skills = [_make_skill(f"skill-{i}") for i in range(10)]
    names = [f"skill-{i}" for i in range(10)]
    llm = _make_llm(json.dumps({"top_k": names}))

    top_k, _, _ = native_top_k("do something", skills, llm, k=5)

    assert len(top_k) == 5


# ---------------------------------------------------------------------------
# 2. test_native_top_k_handles_invalid_json
# ---------------------------------------------------------------------------

def test_native_top_k_handles_invalid_json() -> None:
    skills = [_make_skill("deploy")]
    llm = _make_llm("not valid json at all !!!")

    top_k, tokens, latency = native_top_k("deploy my app", skills, llm)

    assert top_k == []
    assert latency >= 0.0


def test_native_top_k_handles_partial_json() -> None:
    skills = [_make_skill("deploy")]
    llm = _make_llm('{"top_k": null}')

    top_k, _, _ = native_top_k("query", skills, llm)

    # null → empty list after iteration
    assert top_k == []


# ---------------------------------------------------------------------------
# 3. test_sog_top_k_with_mock_router
# ---------------------------------------------------------------------------

def test_sog_top_k_with_mock_router() -> None:
    mock_result = MagicMock()
    mock_result.skill_name = "deploy"
    mock_result.alternatives = [
        {"name": "review", "score": 2.0},
        {"name": "test", "score": 1.5},
    ]

    mock_router = MagicMock()
    mock_router.find_skill.return_value = mock_result

    top_k, tokens, latency = sog_top_k("deploy my service", mock_router)

    assert top_k[0] == "deploy"
    assert "review" in top_k
    assert "test" in top_k
    assert tokens == 800
    assert latency >= 0.0
    mock_router.find_skill.assert_called_once_with(
        query="deploy my service", top_k=5, judge=True
    )


def test_sog_top_k_deduplicates_alternatives() -> None:
    mock_result = MagicMock()
    mock_result.skill_name = "deploy"
    mock_result.alternatives = [
        {"name": "DEPLOY", "score": 1.0},  # duplicate of winner after lower()
        {"name": "review", "score": 0.5},
    ]

    mock_router = MagicMock()
    mock_router.find_skill.return_value = mock_result

    top_k, _, _ = sog_top_k("query", mock_router)

    assert top_k.count("deploy") == 1
    assert "review" in top_k


# ---------------------------------------------------------------------------
# 4. test_sog_top_k_handles_router_exception
# ---------------------------------------------------------------------------

def test_sog_top_k_handles_router_exception() -> None:
    mock_router = MagicMock()
    mock_router.find_skill.side_effect = RuntimeError("Neo4j unavailable")

    top_k, tokens, latency = sog_top_k("any query", mock_router)

    assert top_k == []
    assert tokens == 0
    assert latency >= 0.0


# ---------------------------------------------------------------------------
# 5. test_aggregate_basic
# ---------------------------------------------------------------------------

def test_aggregate_basic() -> None:
    traces = [
        QueryTrace(
            id="q1", query="q1", gold="skill-a", condition="native",
            picked="skill-a", top_k=["skill-a", "skill-b"],
            correct=True, input_tokens=100, latency_ms=50.0,
        ),
        QueryTrace(
            id="q2", query="q2", gold="skill-b", condition="native",
            picked="skill-c", top_k=["skill-c", "skill-b"],
            correct=False, input_tokens=200, latency_ms=80.0,
        ),
        QueryTrace(
            id="q3", query="q3", gold="skill-c", condition="native",
            picked="skill-c", top_k=["skill-c"],
            correct=True, input_tokens=150, latency_ms=200.0,
        ),
        QueryTrace(
            id="q4", query="q4", gold="skill-d", condition="native",
            picked="skill-d", top_k=["skill-d", "skill-a"],
            correct=True, input_tokens=100, latency_ms=60.0,
        ),
    ]

    stats = aggregate(traces)

    assert len(stats) == 1
    s = stats[0]
    assert s.condition == "native"
    assert s.n == 4
    # 3 correct out of 4
    assert abs(s.trigger_accuracy - 0.75) < 1e-9
    # recall@5: q1 gold in top_k, q2 gold in top_k (skill-b is there), q3 gold in top_k, q4 in top_k → 4/4
    assert abs(s.recall_at_5 - 1.0) < 1e-9
    # mean tokens: (100 + 200 + 150 + 100) / 4 = 137.5
    assert abs(s.mean_input_tokens - 137.5) < 1e-9
    # p95 latency: sorted = [50, 60, 80, 200], index = int(0.95 * 3) = int(2.85) = 2 → 80.0
    assert abs(s.p95_latency_ms - 80.0) < 1e-9


def test_aggregate_multiple_conditions() -> None:
    traces = [
        QueryTrace(
            id="q1", query="q", gold="skill-a", condition="native",
            picked="skill-a", top_k=["skill-a"], correct=True,
            input_tokens=100, latency_ms=10.0,
        ),
        QueryTrace(
            id="q1", query="q", gold="skill-a", condition="sog",
            picked="skill-b", top_k=["skill-b"], correct=False,
            input_tokens=800, latency_ms=120.0,
        ),
    ]

    stats = aggregate(traces)
    conditions = {s.condition: s for s in stats}

    assert "native" in conditions
    assert "sog" in conditions
    assert conditions["native"].trigger_accuracy == 1.0
    assert conditions["sog"].trigger_accuracy == 0.0


# ---------------------------------------------------------------------------
# 6. test_bootstrap_ci_returns_valid_interval
# ---------------------------------------------------------------------------

def test_bootstrap_ci_returns_valid_interval() -> None:
    # 90 correct out of 100
    corrects = [1] * 90 + [0] * 10
    low, high = _bootstrap_ci(corrects, n_samples=1000)

    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert low < high
    # The CI should be centered around 0.9
    assert low < 0.9 < high


def test_bootstrap_ci_empty_input() -> None:
    low, high = _bootstrap_ci([])
    assert low == 0.0
    assert high == 0.0


def test_bootstrap_ci_all_correct() -> None:
    corrects = [1] * 50
    low, high = _bootstrap_ci(corrects, n_samples=500)
    # All correct → CI should be [1.0, 1.0]
    assert low == 1.0
    assert high == 1.0
