"""Graph builder and persistence.

Thin orchestration layer over GraphStore — backend (Neo4j or Kuzu) is selected
by SKILLOGY_DB; this module never touches Cypher directly.

Models the TRIGGER SURFACE of each skill. Nodes: Skill, Intent, Signal.
Edges: TRIGGERED_BY (positive trigger), EXCLUDED_BY (negative trigger),
RELATES_TO (companion skill, soft boost in routing).
"""

from __future__ import annotations

from skillogy.infra.db import GraphStore, get_store
from skillogy.domain.types import ParsedSkill, TriggerSurface


def init_schema(store: GraphStore | None = None) -> None:
    """Ensure backend schema exists (idempotent)."""
    (store or get_store()).init_schema()


def build_graph(
    extracted: list[TriggerSurface],
    store: GraphStore | None = None,
    clear_first: bool = False,
) -> dict[str, int]:
    """Persist extracted trigger surfaces into the graph store.

    Returns a summary dict with counts of merged nodes/edges.
    """
    s = store or get_store()

    if clear_first:
        s.clear()

    counts: dict[str, int] = {
        "skills": 0,
        "intents": 0,
        "signals": 0,
        "exclusions": 0,
        "edges": 0,
        "related_to": 0,
    }

    for surface in extracted:
        s.merge_skill(surface.skill_name)
        counts["skills"] += 1

        for label in surface.intents:
            s.link_triggered_intent(surface.skill_name, label)
            counts["intents"] += 1
            counts["edges"] += 1

        for sig in surface.signals:
            s.link_triggered_signal(surface.skill_name, sig.kind, sig.value)
            counts["signals"] += 1
            counts["edges"] += 1

        for exc in surface.exclusions:
            s.link_excluded_signal(surface.skill_name, exc.kind, exc.value)
            counts["exclusions"] += 1
            counts["edges"] += 1

    # Pass 2: RELATES_TO inter-skill edges (only when target Skill exists).
    for ex in extracted:
        for target_name in ex.related_skills:
            if not target_name or target_name == ex.skill_name:
                continue
            created = s.link_relates_to(ex.skill_name, target_name)
            if created:
                counts["edges"] += 1
                counts["related_to"] += 1

    return counts


def enrich_with_parsed(
    parsed_lookup: dict[str, ParsedSkill],
    store: GraphStore | None = None,
) -> int:
    """Populate description / source_path / body_length / scope on Skill nodes.

    Returns the number of skills enriched.
    """
    s = store or get_store()
    enriched = 0
    for name, parsed in parsed_lookup.items():
        if s.set_skill_metadata(
            name=name,
            description=parsed.description,
            source_path=str(parsed.source_path),
            body_length=len(parsed.body),
            scope=parsed.scope,
        ):
            enriched += 1
    return enriched


def export_graph_json(store: GraphStore | None = None) -> dict:
    """Return ``{"nodes": [...], "edges": [...]}`` for /api/graph."""
    return (store or get_store()).export_graph()


def clear_graph(store: GraphStore | None = None) -> None:
    """Delete all nodes and relationships."""
    (store or get_store()).clear()
