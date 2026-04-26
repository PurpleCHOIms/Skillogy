"""Tests for skillogy.core.graph — Neo4j-backed implementation.

Unit tests use a FakeDriver that records executed Cypher + params and returns
canned results. Integration tests (marked @pytest.mark.integration) require a
live Neo4j instance and are skipped by default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from skillogy.core.graph import (
    build_graph,
    clear_graph,
    enrich_with_parsed,
    export_graph_json,
    init_schema,
)
from skillogy.domain.types import ParsedSkill, Signal, TriggerSurface


# ---------------------------------------------------------------------------
# Fake driver infrastructure
# ---------------------------------------------------------------------------

class _DictRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str):
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def data(self):
        return dict(self._data)


class FakeDriver:
    """Records all execute_query calls; returns canned results."""

    def __init__(self, canned_results: list[tuple[list[dict], object, list]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._canned = list(canned_results or [])
        self._call_index = 0

    def execute_query(self, query: str, *args, **kwargs) -> tuple[list, object, list]:
        self.calls.append({"query": query, "kwargs": kwargs})
        if self._call_index < len(self._canned):
            result = self._canned[self._call_index]
            self._call_index += 1
            records_raw, summary, keys = result
            records = [_DictRecord(r) for r in records_raw]
            return records, summary, keys
        return [], MagicMock(), []

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_surface(
    skill_name: str = "alpha",
    intents: list[str] | None = None,
    signals: list[Signal] | None = None,
    exclusions: list[Signal] | None = None,
) -> TriggerSurface:
    return TriggerSurface(
        skill_name=skill_name,
        intents=intents or [],
        signals=signals or [],
        exclusions=exclusions or [],
        extraction_cost_usd=0.0,
        extraction_warnings=[],
    )


def _make_parsed(
    name: str = "alpha",
    description: str = "Alpha skill.",
    body: str = "Does alpha things.",
    source_path: Path | None = None,
    scope: str = "user",
) -> ParsedSkill:
    return ParsedSkill(
        name=name,
        description=description,
        body=body,
        source_path=source_path or Path(f"/tmp/{name}/SKILL.md"),
        raw_frontmatter={},
        warnings=[],
        scope=scope,
    )


# ---------------------------------------------------------------------------
# test_init_schema: verifies three constraint/index statements are issued
# ---------------------------------------------------------------------------

def test_init_schema() -> None:
    """init_schema must issue exactly three CREATE CONSTRAINT/INDEX statements."""
    driver = FakeDriver()
    init_schema(driver)

    queries = [c["query"].strip() for c in driver.calls]
    assert len(queries) == 3, f"Expected 3 schema statements, got {len(queries)}: {queries}"

    expected_patterns = [
        "CREATE CONSTRAINT skill_name_unique",
        "CREATE INDEX intent_label_idx",
        "CREATE INDEX signal_kind_value_idx",
    ]
    for pattern in expected_patterns:
        assert any(pattern in q for q in queries), (
            f"Expected pattern '{pattern}' not found in schema queries: {queries}"
        )

    # Capability index must NOT exist
    assert not any("capability" in q.lower() for q in queries), (
        "Capability index must be removed from schema"
    )


# ---------------------------------------------------------------------------
# test_build_graph_emits_triggered_by_only
# ---------------------------------------------------------------------------

def test_build_graph_emits_triggered_by_only() -> None:
    """build_graph must emit TRIGGERED_BY and EXCLUDED_BY edges only — no SOLVES/REQUIRES."""
    surface_a = _make_surface(
        skill_name="skill-a",
        intents=["fix"],
        signals=[Signal(kind="keyword", value="tsc")],
    )
    surface_b = _make_surface(
        skill_name="skill-b",
        exclusions=[Signal(kind="file_ext", value=".js")],
    )

    driver = FakeDriver()
    summary = build_graph([surface_a, surface_b], driver=driver)

    all_queries = " ".join(c["query"] for c in driver.calls)

    # Must use MERGE for Skill nodes
    assert any("MERGE" in c["query"] and "Skill" in c["query"] for c in driver.calls)

    # Must use TRIGGERED_BY for intents and signals
    assert "TRIGGERED_BY" in all_queries

    # Must use EXCLUDED_BY for exclusions
    assert "EXCLUDED_BY" in all_queries

    # Must NOT use old predicates
    assert "SOLVES" not in all_queries, "SOLVES predicate must be removed"
    assert "REQUIRES" not in all_queries, "REQUIRES predicate must be removed"
    assert "COMPOSES_WITH" not in all_queries, "COMPOSES_WITH predicate must be removed"
    assert "FORBIDDEN_BY" not in all_queries, "FORBIDDEN_BY predicate must be removed"

    # Summary must report correct counts
    assert summary["skills"] == 2
    assert summary["intents"] == 1
    assert summary["signals"] == 1
    assert summary["exclusions"] == 1


# ---------------------------------------------------------------------------
# test_build_graph_intent_nodes
# ---------------------------------------------------------------------------

def test_build_graph_intent_nodes() -> None:
    """build_graph must MERGE Intent nodes with label property."""
    surface = _make_surface(
        skill_name="skill-a",
        intents=["fix typescript error", "debug build"],
    )

    driver = FakeDriver()
    build_graph([surface], driver=driver)

    intent_queries = [c for c in driver.calls if "Intent" in c["query"] and "TRIGGERED_BY" in c["query"]]
    assert len(intent_queries) == 2, f"Expected 2 Intent TRIGGERED_BY queries, got {len(intent_queries)}"

    # Verify label params
    labels_used = {c["kwargs"].get("label") for c in intent_queries}
    assert "fix typescript error" in labels_used
    assert "debug build" in labels_used


# ---------------------------------------------------------------------------
# test_build_graph_signal_nodes
# ---------------------------------------------------------------------------

def test_build_graph_signal_nodes() -> None:
    """build_graph must MERGE Signal nodes with kind and value properties."""
    surface = _make_surface(
        skill_name="skill-a",
        signals=[Signal(kind="keyword", value="tsc"), Signal(kind="file_ext", value=".ts")],
    )

    driver = FakeDriver()
    build_graph([surface], driver=driver)

    signal_queries = [c for c in driver.calls if "Signal" in c["query"] and "TRIGGERED_BY" in c["query"]]
    assert len(signal_queries) == 2

    kinds_used = {c["kwargs"].get("kind") for c in signal_queries}
    values_used = {c["kwargs"].get("value") for c in signal_queries}
    assert "keyword" in kinds_used
    assert "file_ext" in kinds_used
    assert "tsc" in values_used
    assert ".ts" in values_used


# ---------------------------------------------------------------------------
# test_build_graph_exclusion_nodes
# ---------------------------------------------------------------------------

def test_build_graph_exclusion_nodes() -> None:
    """build_graph must MERGE Signal nodes with EXCLUDED_BY for exclusions."""
    surface = _make_surface(
        skill_name="skill-a",
        exclusions=[Signal(kind="file_ext", value=".js")],
    )

    driver = FakeDriver()
    build_graph([surface], driver=driver)

    excl_queries = [c for c in driver.calls if "EXCLUDED_BY" in c["query"]]
    assert len(excl_queries) >= 1

    excl_q = excl_queries[0]
    assert excl_q["kwargs"].get("kind") == "file_ext"
    assert excl_q["kwargs"].get("value") == ".js"


# ---------------------------------------------------------------------------
# test_export_graph_json_shape
# ---------------------------------------------------------------------------

def test_export_graph_json_shape() -> None:
    """export_graph_json must return {nodes: [...], edges: [...]} with correct shapes."""

    class FakeNode:
        def __init__(self, labels, props):
            self.labels = frozenset(labels)
            self._props = props
            self.element_id = str(id(self))

        def __iter__(self):
            return iter(self._props.keys())

        def __getitem__(self, key):
            return self._props[key]

        def __len__(self):
            return len(self._props)

        def keys(self):
            return self._props.keys()

        def get(self, key, default=None):
            return self._props.get(key, default)

    class FakeRel:
        def __init__(self, rel_type):
            self.type = rel_type

    class FakeRecord:
        def __init__(self, data):
            self._data = data
        def __getitem__(self, key):
            return self._data[key]

    skill_n = FakeNode(["Skill"], {"name": "skill-a", "description": "Does stuff", "source_path": "/tmp/SKILL.md", "body_length": 10, "scope": "user"})
    intent_n = FakeNode(["Intent"], {"label": "fix"})
    signal_n = FakeNode(["Signal"], {"kind": "keyword", "value": "tsc"})

    node_records = [
        FakeRecord({"n": skill_n}),
        FakeRecord({"n": intent_n}),
        FakeRecord({"n": signal_n}),
    ]
    edge_records = [
        FakeRecord({"a": skill_n, "r": FakeRel("TRIGGERED_BY"), "b": intent_n}),
        FakeRecord({"a": skill_n, "r": FakeRel("EXCLUDED_BY"), "b": signal_n}),
    ]

    driver = FakeDriver()
    call_count = [0]
    raw_returns = [node_records, edge_records]

    def fake_execute_query(query, *args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(raw_returns):
            return raw_returns[idx], MagicMock(), []
        return [], MagicMock(), []

    driver.execute_query = fake_execute_query  # type: ignore[method-assign]

    result = export_graph_json(driver=driver)

    assert "nodes" in result
    assert "edges" in result

    # Skill node shape
    skill_entry = next((n for n in result["nodes"] if n.get("kind") == "Skill"), None)
    assert skill_entry is not None
    assert skill_entry["id"] == "skill-a"
    assert skill_entry["scope"] == "user"

    # Intent node shape
    intent_entry = next((n for n in result["nodes"] if n.get("kind") == "Intent"), None)
    assert intent_entry is not None
    assert intent_entry["id"] == "intent::fix"

    # Signal node shape
    signal_entry = next((n for n in result["nodes"] if n.get("kind") == "Signal"), None)
    assert signal_entry is not None
    assert signal_entry["id"] == "signal::keyword::tsc"

    # No Capability nodes
    assert not any(n.get("kind") == "Capability" for n in result["nodes"]), \
        "Capability nodes must not appear in export"

    # Edge shapes
    assert len(result["edges"]) == 2
    etypes = {e["etype"] for e in result["edges"]}
    assert "triggered_by" in etypes
    assert "excluded_by" in etypes

    # No old predicate edges
    assert "solves" not in etypes
    assert "requires" not in etypes
    assert "composes_with" not in etypes
    assert "forbidden_by" not in etypes


# ---------------------------------------------------------------------------
# test_enrich_with_parsed: enrichment issues SET query with scope
# ---------------------------------------------------------------------------

def test_enrich_with_parsed_issues_set_query() -> None:
    """enrich_with_parsed must issue a Cypher SET for each skill including scope."""
    canned = [
        ([{"name": "skill-a"}], MagicMock(), ["name"]),
    ]
    driver = FakeDriver(canned_results=canned)

    parsed = _make_parsed(name="skill-a", description="Alpha", body="Body text",
                          source_path=Path("/tmp/skill-a/SKILL.md"), scope="project")
    count = enrich_with_parsed({"skill-a": parsed}, driver=driver)

    assert count == 1

    set_queries = [c for c in driver.calls if "SET" in c["query"]]
    assert len(set_queries) >= 1

    set_q = set_queries[0]
    assert set_q["kwargs"].get("name") == "skill-a"
    assert set_q["kwargs"].get("description") == "Alpha"
    assert set_q["kwargs"].get("source_path") == "/tmp/skill-a/SKILL.md"
    assert set_q["kwargs"].get("body_length") == len("Body text")
    assert set_q["kwargs"].get("scope") == "project"


# ---------------------------------------------------------------------------
# test_clear_graph: issues DETACH DELETE
# ---------------------------------------------------------------------------

def test_clear_graph() -> None:
    """clear_graph must issue MATCH (n) DETACH DELETE n."""
    driver = FakeDriver()
    clear_graph(driver=driver)

    assert len(driver.calls) == 1
    assert "DETACH DELETE" in driver.calls[0]["query"]


# ---------------------------------------------------------------------------
# test_build_graph_emits_relates_to
# ---------------------------------------------------------------------------

def test_build_graph_emits_relates_to() -> None:
    """build_graph must emit RELATES_TO Cypher for skill_a -> skill_b."""
    surface_a = TriggerSurface(
        skill_name="skill_a",
        intents=[],
        signals=[],
        exclusions=[],
        related_skills=["skill_b"],
    )
    surface_b = TriggerSurface(
        skill_name="skill_b",
        intents=[],
        signals=[],
        exclusions=[],
        related_skills=[],
    )

    driver = FakeDriver()
    build_graph([surface_a, surface_b], driver=driver)

    relates_calls = [
        c for c in driver.calls
        if "RELATES_TO" in c["query"]
    ]
    assert len(relates_calls) >= 1, "Expected at least one RELATES_TO Cypher call"

    call = relates_calls[0]
    assert call["kwargs"].get("skill_name") == "skill_a"
    assert call["kwargs"].get("target_name") == "skill_b"


# ---------------------------------------------------------------------------
# test_build_graph_skips_dangling_relates_to
# ---------------------------------------------------------------------------

def test_build_graph_skips_dangling_relates_to() -> None:
    """build_graph still issues the RELATES_TO Cypher for a non-existent target (OPTIONAL MATCH guards it)."""
    surface_a = TriggerSurface(
        skill_name="skill_a",
        intents=[],
        signals=[],
        exclusions=[],
        related_skills=["non-existent"],
    )

    driver = FakeDriver()
    build_graph([surface_a], driver=driver)

    relates_calls = [c for c in driver.calls if "RELATES_TO" in c["query"]]
    assert len(relates_calls) >= 1, "Cypher should still be issued; OPTIONAL MATCH guards nulls"


# ---------------------------------------------------------------------------
# test_build_graph_skips_self_loop
# ---------------------------------------------------------------------------

def test_build_graph_skips_self_loop() -> None:
    """build_graph must NOT emit RELATES_TO Cypher when related_skills contains the skill's own name."""
    surface_a = TriggerSurface(
        skill_name="skill_a",
        intents=[],
        signals=[],
        exclusions=[],
        related_skills=["skill_a"],
    )

    driver = FakeDriver()
    build_graph([surface_a], driver=driver)

    relates_calls = [c for c in driver.calls if "RELATES_TO" in c["query"]]
    assert len(relates_calls) == 0, "Self-loop must be filtered in Python before issuing Cypher"


# ---------------------------------------------------------------------------
# test_export_graph_json_includes_relates_to
# ---------------------------------------------------------------------------

def test_export_graph_json_includes_relates_to() -> None:
    """export_graph_json must include edges with etype='relates_to'."""

    class FakeNode:
        def __init__(self, labels, props):
            self.labels = frozenset(labels)
            self._props = props
            self.element_id = str(id(self))

        def __iter__(self):
            return iter(self._props.keys())

        def __getitem__(self, key):
            return self._props[key]

        def __len__(self):
            return len(self._props)

        def keys(self):
            return self._props.keys()

        def get(self, key, default=None):
            return self._props.get(key, default)

    class FakeRel:
        def __init__(self, rel_type):
            self.type = rel_type

    class FakeRecord:
        def __init__(self, data):
            self._data = data
        def __getitem__(self, key):
            return self._data[key]

    skill_a = FakeNode(["Skill"], {"name": "skill-a", "description": "", "source_path": "", "body_length": 0, "scope": "user"})
    skill_b = FakeNode(["Skill"], {"name": "skill-b", "description": "", "source_path": "", "body_length": 0, "scope": "user"})

    node_records = [FakeRecord({"n": skill_a}), FakeRecord({"n": skill_b})]
    edge_records = [FakeRecord({"a": skill_a, "r": FakeRel("RELATES_TO"), "b": skill_b})]

    call_count = [0]
    raw_returns = [node_records, edge_records]

    driver = FakeDriver()

    def fake_execute_query(query, *args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(raw_returns):
            return raw_returns[idx], MagicMock(), []
        return [], MagicMock(), []

    driver.execute_query = fake_execute_query  # type: ignore[method-assign]

    result = export_graph_json(driver=driver)

    etypes = {e["etype"] for e in result["edges"]}
    assert "relates_to" in etypes, f"Expected 'relates_to' in edge types, got {etypes}"


# ---------------------------------------------------------------------------
# Integration test: full roundtrip (skipped without NEO4J_INTEGRATION=1)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_full_roundtrip() -> None:
    """Spin up Neo4j via testcontainers, build -> enrich -> export -> assert content."""
    if not os.environ.get("NEO4J_INTEGRATION"):
        pytest.skip("Set NEO4J_INTEGRATION=1 to run integration tests")

    from testcontainers.neo4j import Neo4jContainer  # type: ignore[import]
    from neo4j import GraphDatabase

    with Neo4jContainer("neo4j:5-community").with_env("NEO4J_AUTH", "neo4j/skillrouter") as container:
        uri = container.get_connection_url()
        driver = GraphDatabase.driver(uri, auth=("neo4j", "skillrouter"))

        try:
            init_schema(driver)

            surface_a = _make_surface(
                skill_name="skill-a",
                intents=["fix"],
                signals=[Signal(kind="keyword", value="tsc")],
            )
            surface_b = _make_surface(
                skill_name="skill-b",
                exclusions=[Signal(kind="file_ext", value=".js")],
            )

            summary = build_graph([surface_a, surface_b], driver=driver, clear_first=True)
            assert summary["skills"] == 2

            parsed_lookup = {
                "skill-a": _make_parsed("skill-a"),
                "skill-b": _make_parsed("skill-b"),
            }
            enriched = enrich_with_parsed(parsed_lookup, driver=driver)
            assert enriched == 2

            exported = export_graph_json(driver=driver)
            node_ids = {n["id"] for n in exported["nodes"]}
            assert "skill-a" in node_ids
            assert "skill-b" in node_ids
            assert "intent::fix" in node_ids
            assert "signal::keyword::tsc" in node_ids

            edge_etypes = {e["etype"] for e in exported["edges"]}
            assert "triggered_by" in edge_etypes
            assert "excluded_by" in edge_etypes
            assert "solves" not in edge_etypes
        finally:
            driver.close()
