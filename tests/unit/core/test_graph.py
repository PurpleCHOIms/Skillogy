"""Tests for skillogy.core.graph — backend-agnostic via FakeStore.

Unit tests assert on the *semantic* operations the graph builder issues against
a GraphStore (merge_skill, link_triggered_intent, …) — independent of whether
the underlying backend is Neo4j or Kuzu.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from skillogy.core.graph import (
    build_graph,
    clear_graph,
    enrich_with_parsed,
    export_graph_json,
    init_schema,
)
from skillogy.domain.types import ParsedSkill, Signal, TriggerSurface

from tests.conftest import FakeStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_surface(
    skill_name: str = "alpha",
    intents: list[str] | None = None,
    signals: list[Signal] | None = None,
    exclusions: list[Signal] | None = None,
    related_skills: list[str] | None = None,
) -> TriggerSurface:
    return TriggerSurface(
        skill_name=skill_name,
        intents=intents or [],
        signals=signals or [],
        exclusions=exclusions or [],
        related_skills=related_skills or [],
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
# Schema init
# ---------------------------------------------------------------------------

def test_init_schema_invokes_store() -> None:
    store = FakeStore()
    init_schema(store)
    assert store.schema_inited is True


# ---------------------------------------------------------------------------
# build_graph: trigger surfaces
# ---------------------------------------------------------------------------

def test_build_graph_merges_skills_and_links_triggers() -> None:
    surface_a = _make_surface(
        skill_name="skill-a",
        intents=["fix"],
        signals=[Signal(kind="keyword", value="tsc")],
    )
    surface_b = _make_surface(
        skill_name="skill-b",
        exclusions=[Signal(kind="file_ext", value=".js")],
    )

    store = FakeStore()
    summary = build_graph([surface_a, surface_b], store=store)

    assert store.merged_skills == ["skill-a", "skill-b"]
    assert store.triggered_intents == [("skill-a", "fix")]
    assert store.triggered_signals == [("skill-a", "keyword", "tsc")]
    assert store.excluded_signals == [("skill-b", "file_ext", ".js")]

    assert summary["skills"] == 2
    assert summary["intents"] == 1
    assert summary["signals"] == 1
    assert summary["exclusions"] == 1


def test_build_graph_intent_links() -> None:
    surface = _make_surface(
        skill_name="skill-a",
        intents=["fix typescript error", "debug build"],
    )
    store = FakeStore()
    build_graph([surface], store=store)

    assert ("skill-a", "fix typescript error") in store.triggered_intents
    assert ("skill-a", "debug build") in store.triggered_intents
    assert len(store.triggered_intents) == 2


def test_build_graph_signal_links() -> None:
    surface = _make_surface(
        skill_name="skill-a",
        signals=[Signal(kind="keyword", value="tsc"), Signal(kind="file_ext", value=".ts")],
    )
    store = FakeStore()
    build_graph([surface], store=store)

    assert ("skill-a", "keyword", "tsc") in store.triggered_signals
    assert ("skill-a", "file_ext", ".ts") in store.triggered_signals


def test_build_graph_exclusion_links() -> None:
    surface = _make_surface(
        skill_name="skill-a",
        exclusions=[Signal(kind="file_ext", value=".js")],
    )
    store = FakeStore()
    build_graph([surface], store=store)

    assert store.excluded_signals == [("skill-a", "file_ext", ".js")]


def test_build_graph_clear_first_clears_store() -> None:
    store = FakeStore()
    build_graph([_make_surface()], store=store, clear_first=True)
    assert store.cleared is True


# ---------------------------------------------------------------------------
# build_graph: RELATES_TO
# ---------------------------------------------------------------------------

def test_build_graph_emits_relates_to_for_existing_target() -> None:
    a = _make_surface(skill_name="skill-a", related_skills=["skill-b"])
    b = _make_surface(skill_name="skill-b")

    store = FakeStore(relates_to_existing_targets={"skill-a", "skill-b"})
    summary = build_graph([a, b], store=store)

    assert ("skill-a", "skill-b") in store.relates_to_calls
    assert summary["related_to"] == 1


def test_build_graph_skips_dangling_relates_to_when_target_missing() -> None:
    a = _make_surface(skill_name="skill-a", related_skills=["non-existent"])

    store = FakeStore(relates_to_existing_targets={"skill-a"})
    summary = build_graph([a], store=store)

    # The store was asked, but reported the edge wasn't created.
    assert ("skill-a", "non-existent") in store.relates_to_calls
    assert summary["related_to"] == 0


def test_build_graph_skips_self_loop() -> None:
    a = _make_surface(skill_name="skill-a", related_skills=["skill-a"])

    store = FakeStore()
    build_graph([a], store=store)

    # Self-loops are filtered before the store is touched.
    assert store.relates_to_calls == []


# ---------------------------------------------------------------------------
# enrich_with_parsed
# ---------------------------------------------------------------------------

def test_enrich_with_parsed_invokes_metadata_setter() -> None:
    store = FakeStore(metadata_existing_skills={"skill-a"})
    parsed = _make_parsed(
        name="skill-a",
        description="Alpha",
        body="Body text",
        source_path=Path("/tmp/skill-a/SKILL.md"),
        scope="project",
    )

    count = enrich_with_parsed({"skill-a": parsed}, store=store)

    assert count == 1
    assert store.metadata_calls == [{
        "name": "skill-a",
        "description": "Alpha",
        "source_path": "/tmp/skill-a/SKILL.md",
        "body_length": len("Body text"),
        "scope": "project",
    }]


def test_enrich_with_parsed_returns_zero_when_skill_missing() -> None:
    store = FakeStore(metadata_existing_skills=set())  # nothing matches
    parsed = _make_parsed(name="ghost")
    count = enrich_with_parsed({"ghost": parsed}, store=store)
    assert count == 0


# ---------------------------------------------------------------------------
# clear_graph
# ---------------------------------------------------------------------------

def test_clear_graph_invokes_store_clear() -> None:
    store = FakeStore()
    clear_graph(store=store)
    assert store.cleared is True


# ---------------------------------------------------------------------------
# export_graph_json
# ---------------------------------------------------------------------------

def test_export_graph_json_returns_store_payload() -> None:
    payload = {
        "nodes": [
            {"kind": "Skill", "id": "skill-a", "description": "", "source_path": "", "body_length": 0, "scope": "user"},
            {"kind": "Intent", "id": "intent::fix", "label": "fix"},
            {"kind": "Signal", "id": "signal::keyword::tsc", "signal_kind": "keyword", "value": "tsc"},
        ],
        "edges": [
            {"src": "skill-a", "dst": "intent::fix", "etype": "triggered_by"},
            {"src": "skill-a", "dst": "signal::keyword::tsc", "etype": "excluded_by"},
        ],
    }
    store = FakeStore(canned_export=payload)
    result = export_graph_json(store=store)
    assert result == payload


# ---------------------------------------------------------------------------
# Integration tests (skipped by default; require a live backend)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_full_roundtrip_neo4j() -> None:
    """Spin up Neo4j via testcontainers, build → enrich → export → assert."""
    if not os.environ.get("NEO4J_INTEGRATION"):
        pytest.skip("Set NEO4J_INTEGRATION=1 to run integration tests")

    from testcontainers.neo4j import Neo4jContainer  # type: ignore[import]

    from skillogy.infra.db import Neo4jStore

    with Neo4jContainer("neo4j:5-community").with_env("NEO4J_AUTH", "neo4j/skillrouter") as container:
        uri = container.get_connection_url()
        store = Neo4jStore(uri=uri, user="neo4j", password="skillrouter")
        try:
            init_schema(store)

            surface_a = _make_surface(
                skill_name="skill-a",
                intents=["fix"],
                signals=[Signal(kind="keyword", value="tsc")],
            )
            surface_b = _make_surface(
                skill_name="skill-b",
                exclusions=[Signal(kind="file_ext", value=".js")],
            )
            summary = build_graph([surface_a, surface_b], store=store, clear_first=True)
            assert summary["skills"] == 2

            parsed_lookup = {"skill-a": _make_parsed("skill-a"), "skill-b": _make_parsed("skill-b")}
            enriched = enrich_with_parsed(parsed_lookup, store=store)
            assert enriched == 2

            exported = export_graph_json(store=store)
            node_ids = {n["id"] for n in exported["nodes"]}
            assert "skill-a" in node_ids
            assert "skill-b" in node_ids
            assert "intent::fix" in node_ids
            assert "signal::keyword::tsc" in node_ids

            edge_etypes = {e["etype"] for e in exported["edges"]}
            assert "triggered_by" in edge_etypes
            assert "excluded_by" in edge_etypes
        finally:
            store.close()


@pytest.mark.integration
def test_full_roundtrip_kuzu(tmp_path: Path) -> None:
    """Drive the same flow against an embedded KuzuStore."""
    pytest.importorskip("kuzu")

    from skillogy.infra.db import KuzuStore

    store = KuzuStore(db_path=str(tmp_path / "graph.kuzu"))
    try:
        init_schema(store)

        surface_a = _make_surface(
            skill_name="skill-a",
            intents=["fix"],
            signals=[Signal(kind="keyword", value="tsc")],
        )
        surface_b = _make_surface(
            skill_name="skill-b",
            exclusions=[Signal(kind="file_ext", value=".js")],
        )
        summary = build_graph([surface_a, surface_b], store=store, clear_first=True)
        assert summary["skills"] == 2

        parsed_lookup = {"skill-a": _make_parsed("skill-a"), "skill-b": _make_parsed("skill-b")}
        enriched = enrich_with_parsed(parsed_lookup, store=store)
        assert enriched == 2

        exported = export_graph_json(store=store)
        node_ids = {n["id"] for n in exported["nodes"]}
        assert "skill-a" in node_ids
        assert "skill-b" in node_ids
        assert "intent::fix" in node_ids
        assert "signal::keyword::tsc" in node_ids
    finally:
        store.close()
