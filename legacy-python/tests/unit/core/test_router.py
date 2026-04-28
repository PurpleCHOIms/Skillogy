"""Tests for skillogy.core.router — Neo4j-backed implementation.

Unit tests mock the Neo4j driver using FakeDriver that returns canned results.
Integration tests are marked @pytest.mark.integration and skipped by default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from skillogy.core.router import Router, RoutingResult
from skillogy.domain.types import Signal


# ---------------------------------------------------------------------------
# Fake driver for router tests
# ---------------------------------------------------------------------------

class _DictRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str):
        return self._data[key]

    def keys(self):
        return self._data.keys()


class FakeDriver:
    """Returns canned query results for router Cypher calls."""

    def __init__(self, canned_rows: list[dict] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._canned_rows = canned_rows or []

    def execute_query(self, query: str, *args, **kwargs) -> tuple[list, object, list]:
        self.calls.append({"query": query, "kwargs": kwargs})
        records = [_DictRecord(r) for r in self._canned_rows]
        return records, MagicMock(), []

    def close(self) -> None:
        pass


class FakeDriverDispatch:
    """FakeDriver that dispatches different canned results based on Cypher query content.

    For RELATES_TO queries, mimics the WHERE NOT t.name IN $exclude and LIMIT $k
    behaviour of the real Cypher so dedup and limit logic can be verified end-to-end.
    """

    def __init__(
        self,
        primary_rows: list[dict],
        related_rows: list[dict],
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._primary_rows = primary_rows
        self._related_rows = related_rows
        self.related_call_kwargs: dict[str, Any] | None = None

    def execute_query(self, query: str, *args, **kwargs) -> tuple[list, object, list]:
        self.calls.append({"query": query, "kwargs": kwargs})
        if "RELATES_TO" in query:
            self.related_call_kwargs = kwargs
            exclude = set(kwargs.get("exclude", []))
            k = kwargs.get("k", len(self._related_rows))
            filtered = [r for r in self._related_rows if r["name"] not in exclude][:k]
            records = [_DictRecord(r) for r in filtered]
        else:
            records = [_DictRecord(r) for r in self._primary_rows]
        return records, MagicMock(), []

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# MockLLM
# ---------------------------------------------------------------------------

class MockLLM:
    """Controllable mock LLM that returns preset responses based on call index."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._call_index = 0

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        if self._call_index >= len(self._responses):
            raise RuntimeError(f"MockLLM: no response configured for call {self._call_index}")
        response = self._responses[self._call_index]
        self._call_index += 1
        return response


# ---------------------------------------------------------------------------
# test_routing_picks_typescript_skill_with_mock_driver
# ---------------------------------------------------------------------------

def test_routing_picks_typescript_skill_with_mock_driver() -> None:
    """Router returns typescript-build-error when driver returns it as top candidate."""
    canned_rows = [
        {
            "name": "typescript-build-error",
            "description": "Fixes TypeScript compilation errors",
            "source_path": None,
            "scope": "user",
            "score": 3.0,
            "hits": [
                {"kind": "Intent", "id": "fix"},
                {"kind": "Signal", "id": "tsc"},
            ],
        }
    ]

    driver = FakeDriver(canned_rows=canned_rows)

    extraction_response = (
        '{"intents": ["fix"], '
        '"signals": [{"kind": "keyword", "value": "tsc"}]}'
    )
    llm = MockLLM([extraction_response])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("TypeScript build error", top_k=5, judge=False)

    assert result.skill_name == "typescript-build-error"
    assert result.score == 3.0

    # reasoning_path must include triggered_by edges
    edge_types = {etype for _, etype, _ in result.reasoning_path}
    assert "triggered_by" in edge_types

    # Verify the primary Cypher query was called (RELATES_TO query also fires, so >= 1)
    assert len(driver.calls) >= 1
    q = driver.calls[0]["query"]
    assert "MATCH (s:Skill)-[:TRIGGERED_BY]->(n)" in q
    assert "TRIGGERED_BY" in q


# ---------------------------------------------------------------------------
# test_excluded_by_in_cypher
# ---------------------------------------------------------------------------

def test_excluded_by_in_cypher() -> None:
    """The Cypher query must contain WHERE NOT EXISTS { MATCH ... EXCLUDED_BY } clause."""
    driver = FakeDriver(canned_rows=[])

    extraction_response = (
        '{"intents": ["fix"], '
        '"signals": [{"kind": "file_ext", "value": ".js"}]}'
    )
    llm = MockLLM([extraction_response])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("fix this .js file", top_k=5, judge=False)

    # Empty result because driver returned no rows
    assert result.skill_name == ""
    assert result.alternatives == []

    # Verify the Cypher query contains EXCLUDED_BY exclusion clause
    assert len(driver.calls) >= 1
    cypher = driver.calls[0]["query"]
    assert "EXCLUDED_BY" in cypher, f"Expected EXCLUDED_BY in cypher: {cypher}"
    assert "NOT EXISTS" in cypher, f"Expected NOT EXISTS clause in cypher: {cypher}"

    # Old FORBIDDEN_BY must NOT appear
    assert "FORBIDDEN_BY" not in cypher, "FORBIDDEN_BY must be replaced by EXCLUDED_BY"


# ---------------------------------------------------------------------------
# test_intent_weight_higher_than_signal
# ---------------------------------------------------------------------------

def test_intent_weight_higher_than_signal() -> None:
    """The Cypher query must score Intent matches at 2.0 and Signal matches at 1.0."""
    driver = FakeDriver(canned_rows=[])
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(driver=driver, llm=llm)
    router.find_skill("fix something", judge=False)

    cypher = driver.calls[0]["query"]
    # Must contain Intent weight 2.0 and default weight 1.0 (ELSE)
    assert "2.0" in cypher, "Intent weight 2.0 must appear in query"
    assert "1.0" in cypher or "ELSE" in cypher, "Signal weight 1.0 must appear in query"


# ---------------------------------------------------------------------------
# test_no_old_predicates_in_cypher
# ---------------------------------------------------------------------------

def test_no_old_predicates_in_cypher() -> None:
    """The Cypher query must not reference SOLVES, REQUIRES, COMPOSES_WITH, or FORBIDDEN_BY."""
    driver = FakeDriver(canned_rows=[])
    llm = MockLLM(['{"intents": [], "signals": []}'])

    router = Router(driver=driver, llm=llm)
    router.find_skill("anything", judge=False)

    cypher = driver.calls[0]["query"]
    forbidden_predicates = ["SOLVES", "REQUIRES", "COMPOSES_WITH", "FORBIDDEN_BY"]
    for pred in forbidden_predicates:
        assert pred not in cypher, f"Old predicate {pred} must not appear in router Cypher"


# ---------------------------------------------------------------------------
# test_no_vector_embeddings_used
# ---------------------------------------------------------------------------

def test_no_vector_embeddings_used() -> None:
    """router.py must not contain any embedding/vector-similarity logic."""
    router_path = Path(__file__).parent.parent.parent.parent / "src" / "skillogy" / "core" / "router.py"
    source = router_path.read_text(encoding="utf-8")
    forbidden = re.compile(r"\b(embed|embedding|embeddings|cosine)\b", re.IGNORECASE)
    matches = forbidden.findall(source)
    assert matches == [], f"Found forbidden terms in router.py: {matches}"


# ---------------------------------------------------------------------------
# test_judge_picks_winner_from_multiple_candidates
# ---------------------------------------------------------------------------

def test_judge_picks_winner_from_multiple_candidates() -> None:
    """When multiple candidates exist and judge=True, LLM judge selects winner."""
    primary_rows = [
        {
            "name": "typescript-build-error",
            "description": "Fixes TypeScript compilation errors",
            "source_path": None,
            "scope": "user",
            "score": 4.0,
            "hits": [{"kind": "Intent", "id": "fix"}, {"kind": "Signal", "id": "tsc"}],
        },
        {
            "name": "runtime-debugger",
            "description": "Debugs runtime errors",
            "source_path": None,
            "scope": "user",
            "score": 2.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        },
    ]

    driver = FakeDriverDispatch(primary_rows=primary_rows, related_rows=[])

    extraction_response = (
        '{"intents": ["fix"], '
        '"signals": [{"kind": "keyword", "value": "tsc"}]}'
    )
    judge_response = '{"winner": "typescript-build-error", "reason": "Directly targets TypeScript build failures."}'
    llm = MockLLM([extraction_response, judge_response])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("TypeScript build error", top_k=5, judge=True)

    assert result.skill_name == "typescript-build-error"
    assert len(result.alternatives) == 1
    assert result.alternatives[0]["name"] == "runtime-debugger"


# ---------------------------------------------------------------------------
# test_skill_body_loaded_from_disk
# ---------------------------------------------------------------------------

def test_skill_body_loaded_from_disk(tmp_path: Path) -> None:
    """When source_path is set in the driver result, skill_body equals the file content."""
    skill_md = tmp_path / "SKILL.md"
    expected_body = "# TypeScript Build Error\n\nFixes TS compiler failures."
    skill_md.write_text(expected_body, encoding="utf-8")

    canned_rows = [
        {
            "name": "typescript-build-error",
            "description": "Fixes TypeScript compilation errors",
            "source_path": str(skill_md),
            "scope": "user",
            "score": 3.0,
            "hits": [{"kind": "Signal", "id": "tsc"}],
        }
    ]

    driver = FakeDriver(canned_rows=canned_rows)

    extraction_response = (
        '{"intents": ["fix"], '
        '"signals": [{"kind": "keyword", "value": "tsc"}]}'
    )
    llm = MockLLM([extraction_response])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("tsc fix", top_k=5, judge=False)

    assert result.skill_name == "typescript-build-error"
    assert result.skill_body == expected_body


# ---------------------------------------------------------------------------
# test_empty_result_when_no_candidates
# ---------------------------------------------------------------------------

def test_empty_result_when_no_candidates() -> None:
    """Router returns empty RoutingResult when no candidates match."""
    driver = FakeDriver(canned_rows=[])

    extraction_response = '{"intents": ["format"], "signals": []}'
    llm = MockLLM([extraction_response])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("format my code", top_k=5, judge=False)

    assert result.skill_name == ""
    assert result.skill_body == ""
    assert result.reasoning_path == []
    assert result.alternatives == []
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# test_scope_included_in_result_rows
# ---------------------------------------------------------------------------

def test_scope_included_in_result_rows() -> None:
    """Router query must include scope in SELECT columns."""
    driver = FakeDriver(canned_rows=[])
    llm = MockLLM(['{"intents": [], "signals": []}'])

    router = Router(driver=driver, llm=llm)
    router.find_skill("anything", judge=False)

    cypher = driver.calls[0]["query"]
    assert "scope" in cypher, "scope must be returned in the Cypher query"


# ---------------------------------------------------------------------------
# test_alternatives_includes_relates_to_neighbors
# ---------------------------------------------------------------------------

def test_alternatives_includes_relates_to_neighbors() -> None:
    """RELATES_TO neighbors are appended to alternatives with via='relates_to'."""
    primary_rows = [
        {
            "name": "skill-a",
            "description": "Skill A",
            "source_path": None,
            "scope": "user",
            "score": 3.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        }
    ]
    related_rows = [
        {"name": "skill-b", "description": "Skill B", "scope": "user"},
        {"name": "skill-c", "description": "Skill C", "scope": "user"},
    ]

    driver = FakeDriverDispatch(primary_rows=primary_rows, related_rows=related_rows)
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("fix something", judge=False)

    assert result.skill_name == "skill-a"
    assert len(result.alternatives) == 2
    assert all(a["via"] == "relates_to" for a in result.alternatives)
    names = [a["name"] for a in result.alternatives]
    assert "skill-b" in names
    assert "skill-c" in names


# ---------------------------------------------------------------------------
# test_relates_to_dedups_against_primary
# ---------------------------------------------------------------------------

def test_relates_to_dedups_against_primary() -> None:
    """RELATES_TO neighbors that are already in primary alternatives are not duplicated."""
    primary_rows = [
        {
            "name": "skill-a",
            "description": "Skill A",
            "source_path": None,
            "scope": "user",
            "score": 4.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        },
        {
            "name": "skill-b",
            "description": "Skill B",
            "source_path": None,
            "scope": "user",
            "score": 2.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        },
    ]
    # RELATES_TO returns skill-b (already a primary alt) and skill-c (new)
    related_rows = [
        {"name": "skill-b", "description": "Skill B", "scope": "user"},
        {"name": "skill-c", "description": "Skill C", "scope": "user"},
    ]

    driver = FakeDriverDispatch(primary_rows=primary_rows, related_rows=related_rows)
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("fix something", judge=False)

    assert result.skill_name == "skill-a"
    names = [a["name"] for a in result.alternatives]
    # skill-b appears once (as primary), skill-c added via relates_to
    assert names.count("skill-b") == 1
    assert "skill-c" in names
    # skill-b primary entry has no via key; skill-c has via='relates_to'
    skill_b_entry = next(a for a in result.alternatives if a["name"] == "skill-b")
    assert "via" not in skill_b_entry
    skill_c_entry = next(a for a in result.alternatives if a["name"] == "skill-c")
    assert skill_c_entry["via"] == "relates_to"


# ---------------------------------------------------------------------------
# test_relates_to_respects_env_limit
# ---------------------------------------------------------------------------

def test_relates_to_respects_env_limit(monkeypatch) -> None:
    """SKILLOGY_RELATES_K env var controls the LIMIT passed to the RELATES_TO query."""
    monkeypatch.setenv("SKILLOGY_RELATES_K", "1")

    primary_rows = [
        {
            "name": "skill-a",
            "description": "Skill A",
            "source_path": None,
            "scope": "user",
            "score": 3.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        }
    ]
    # Driver would return 3 related rows but limit should constrain the query param
    related_rows = [
        {"name": "skill-b", "description": "Skill B", "scope": "user"},
    ]

    driver = FakeDriverDispatch(primary_rows=primary_rows, related_rows=related_rows)
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("fix something", judge=False)

    # Verify the k=1 was passed to the RELATES_TO query
    assert driver.related_call_kwargs is not None
    assert driver.related_call_kwargs["k"] == 1
    # And only 1 entry was added
    relates_alts = [a for a in result.alternatives if a.get("via") == "relates_to"]
    assert len(relates_alts) == 1


# ---------------------------------------------------------------------------
# test_no_relates_to_query_when_no_match
# ---------------------------------------------------------------------------

def test_no_relates_to_query_when_no_match() -> None:
    """When no primary skill matches, _fetch_related is not called."""
    driver = FakeDriverDispatch(primary_rows=[], related_rows=[])
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("fix something", judge=False)

    assert result.skill_name == ""
    assert result.alternatives == []
    # No RELATES_TO query should have been fired
    relates_calls = [c for c in driver.calls if "RELATES_TO" in c["query"]]
    assert len(relates_calls) == 0


# ---------------------------------------------------------------------------
# test_relates_to_handles_zero_neighbors
# ---------------------------------------------------------------------------

def test_relates_to_handles_zero_neighbors() -> None:
    """When RELATES_TO query returns nothing, alternatives equals only primary alternatives."""
    primary_rows = [
        {
            "name": "skill-a",
            "description": "Skill A",
            "source_path": None,
            "scope": "user",
            "score": 3.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        }
    ]

    driver = FakeDriverDispatch(primary_rows=primary_rows, related_rows=[])
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(driver=driver, llm=llm)
    result = router.find_skill("fix something", judge=False)

    assert result.skill_name == "skill-a"
    assert result.alternatives == []


# ---------------------------------------------------------------------------
# Integration test: routing against real Neo4j (skipped without NEO4J_INTEGRATION=1)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_routing_real_neo4j() -> None:
    """Build a real graph in Neo4j via testcontainers and verify routing works."""
    if not os.environ.get("NEO4J_INTEGRATION"):
        pytest.skip("Set NEO4J_INTEGRATION=1 to run integration tests")

    from testcontainers.neo4j import Neo4jContainer  # type: ignore[import]
    from neo4j import GraphDatabase

    from skillogy.core.graph import build_graph, init_schema
    from skillogy.domain.types import TriggerSurface

    with Neo4jContainer("neo4j:5-community").with_env("NEO4J_AUTH", "neo4j/skillrouter") as container:
        uri = container.get_connection_url()
        driver = GraphDatabase.driver(uri, auth=("neo4j", "skillrouter"))

        try:
            init_schema(driver)

            surfaces = [
                TriggerSurface(
                    skill_name="typescript-build-error",
                    intents=["fix", "diagnose"],
                    signals=[Signal(kind="keyword", value="tsc")],
                    exclusions=[Signal(kind="file_ext", value=".js")],
                    extraction_cost_usd=0.0,
                    extraction_warnings=[],
                )
            ]
            build_graph(surfaces, driver=driver, clear_first=True)

            extraction_response = (
                '{"intents": ["fix"], '
                '"signals": [{"kind": "keyword", "value": "tsc"}]}'
            )
            llm = MockLLM([extraction_response])

            router = Router(driver=driver, llm=llm)
            result = router.find_skill("TypeScript build error", top_k=5, judge=False)

            assert result.skill_name == "typescript-build-error"
            assert result.score > 0
        finally:
            driver.close()
