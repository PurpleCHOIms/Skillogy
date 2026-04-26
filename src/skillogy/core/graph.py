"""Graph builder and persistence for skillogy — Neo4j 5 backend.

Models the TRIGGER SURFACE of each skill. Nodes: Skill, Intent, Signal.
Edges: TRIGGERED_BY (positive trigger), EXCLUDED_BY (negative trigger).
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver, RoutingControl

from skillogy.infra.db import get_driver
from skillogy.domain.types import TriggerSurface, ParsedSkill

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT skill_name_unique IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE",
    "CREATE INDEX intent_label_idx IF NOT EXISTS FOR (i:Intent) ON (i.label)",
    "CREATE INDEX signal_kind_value_idx IF NOT EXISTS FOR (s:Signal) ON (s.kind, s.value)",
]


def init_schema(driver: Driver | None = None) -> None:
    """Ensure constraints and indexes exist (idempotent)."""
    drv = driver or get_driver()
    for stmt in _SCHEMA_STATEMENTS:
        drv.execute_query(stmt)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_graph(
    extracted: list[TriggerSurface],
    driver: Driver | None = None,
    clear_first: bool = False,
) -> dict[str, int]:
    """Persist extracted trigger surfaces into Neo4j.

    Parameters
    ----------
    extracted:
        List of TriggerSurface objects from the extractor.
    driver:
        Neo4j driver instance; defaults to the singleton from db.get_driver().
    clear_first:
        When True, wipes all nodes/edges before inserting.

    Returns
    -------
    Summary dict with counts: skills, intents, signals, exclusions, edges.
    """
    drv = driver or get_driver()

    if clear_first:
        drv.execute_query("MATCH (n) DETACH DELETE n")

    counts: dict[str, int] = {"skills": 0, "intents": 0, "signals": 0, "exclusions": 0, "edges": 0}

    for surface in extracted:
        # MERGE skill node
        drv.execute_query(
            "MERGE (s:Skill {name: $name})",
            name=surface.skill_name,
        )
        counts["skills"] += 1

        # TRIGGERED_BY → Intent nodes
        for label in surface.intents:
            drv.execute_query(
                "MERGE (i:Intent {label: $label}) "
                "WITH i MATCH (s:Skill {name: $skill}) "
                "MERGE (s)-[:TRIGGERED_BY]->(i)",
                label=label,
                skill=surface.skill_name,
            )
            counts["intents"] += 1
            counts["edges"] += 1

        # TRIGGERED_BY → Signal nodes (positive triggers)
        for sig in surface.signals:
            drv.execute_query(
                "MERGE (n:Signal {kind: $kind, value: $value}) "
                "WITH n MATCH (s:Skill {name: $skill}) "
                "MERGE (s)-[:TRIGGERED_BY]->(n)",
                kind=sig.kind,
                value=sig.value,
                skill=surface.skill_name,
            )
            counts["signals"] += 1
            counts["edges"] += 1

        # EXCLUDED_BY → Signal nodes (negative triggers)
        for exc in surface.exclusions:
            drv.execute_query(
                "MERGE (n:Signal {kind: $kind, value: $value}) "
                "WITH n MATCH (s:Skill {name: $skill}) "
                "MERGE (s)-[:EXCLUDED_BY]->(n)",
                kind=exc.kind,
                value=exc.value,
                skill=surface.skill_name,
            )
            counts["exclusions"] += 1
            counts["edges"] += 1

    # Pass 3: RELATES_TO inter-skill edges (only when target Skill exists)
    counts["related_to"] = 0
    for ex in extracted:
        for target_name in ex.related_skills:
            if not target_name or target_name == ex.skill_name:
                continue
            drv.execute_query(
                """
                MATCH (s:Skill {name: $skill_name})
                OPTIONAL MATCH (t:Skill {name: $target_name})
                WITH s, t
                WHERE t IS NOT NULL AND s <> t
                MERGE (s)-[:RELATES_TO]->(t)
                """,
                skill_name=ex.skill_name,
                target_name=target_name,
            )
            counts["edges"] += 1
            counts["related_to"] += 1

    return counts


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def enrich_with_parsed(
    parsed_lookup: dict[str, ParsedSkill],
    driver: Driver | None = None,
) -> int:
    """Set description, source_path, body_length, scope on Skill nodes from ParsedSkill data.

    Returns the number of skills enriched.
    """
    drv = driver or get_driver()
    enriched = 0
    for skill_name, parsed in parsed_lookup.items():
        records, _, _ = drv.execute_query(
            """
            MATCH (s:Skill {name: $name})
            SET s.description = $description,
                s.source_path = $source_path,
                s.body_length = $body_length,
                s.scope = $scope
            RETURN s.name AS name
            """,
            name=skill_name,
            description=parsed.description,
            source_path=str(parsed.source_path),
            body_length=len(parsed.body),
            scope=parsed.scope,
        )
        if records:
            enriched += 1
    return enriched


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_graph_json(driver: Driver | None = None) -> dict[str, list[dict[str, Any]]]:
    """Return ``{"nodes": [...], "edges": [...]}`` for /api/graph.

    Node format: {"id": <str>, "kind": <str>, plus type-specific fields}
    Edge format: {"src": <str>, "dst": <str>, "etype": <str>}
    """
    drv = driver or get_driver()

    # Fetch all nodes
    node_records, _, _ = drv.execute_query(
        "MATCH (n) RETURN n",
        routing_=RoutingControl.READ,
    )

    nodes: list[dict[str, Any]] = []
    for record in node_records:
        n = record["n"]
        labels = list(n.labels)
        kind = labels[0] if labels else "Unknown"
        props = dict(n)

        node_entry: dict[str, Any] = {"kind": kind}

        if kind == "Skill":
            node_entry["id"] = props.get("name", "")
            node_entry["description"] = props.get("description", "")
            node_entry["source_path"] = props.get("source_path", "")
            node_entry["body_length"] = props.get("body_length", 0)
            node_entry["scope"] = props.get("scope", "user")
        elif kind == "Intent":
            label = props.get("label", "")
            node_entry["id"] = f"intent::{label}"
            node_entry["label"] = label
        elif kind == "Signal":
            sig_kind = props.get("kind", "")
            value = props.get("value", "")
            node_entry["id"] = f"signal::{sig_kind}::{value}"
            node_entry["signal_kind"] = sig_kind
            node_entry["value"] = value
        else:
            node_entry["id"] = str(n.element_id)

        nodes.append(node_entry)

    # Fetch all edges
    edge_records, _, _ = drv.execute_query(
        "MATCH (a)-[r]->(b) RETURN a, r, b",
        routing_=RoutingControl.READ,
    )

    edges: list[dict[str, Any]] = []
    for record in edge_records:
        a = record["a"]
        b = record["b"]
        r = record["r"]

        a_labels = list(a.labels)
        b_labels = list(b.labels)
        a_kind = a_labels[0] if a_labels else "Unknown"
        b_kind = b_labels[0] if b_labels else "Unknown"

        def _node_id(node: Any, kind: str) -> str:
            props = dict(node)
            if kind == "Skill":
                return props.get("name", str(node.element_id))
            elif kind == "Intent":
                return f"intent::{props.get('label', '')}"
            elif kind == "Signal":
                return f"signal::{props.get('kind', '')}::{props.get('value', '')}"
            return str(node.element_id)

        edges.append({
            "src": _node_id(a, a_kind),
            "dst": _node_id(b, b_kind),
            "etype": r.type.lower(),
        })

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def clear_graph(driver: Driver | None = None) -> None:
    """Delete all nodes and relationships."""
    drv = driver or get_driver()
    drv.execute_query("MATCH (n) DETACH DELETE n")
