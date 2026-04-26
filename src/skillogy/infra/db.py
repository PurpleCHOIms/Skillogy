"""Graph store abstraction with Neo4j and Kuzu backends.

Selects backend via SKILLOGY_DB env var:
  - "kuzu"  (default) — embedded, single .db directory, no external server.
  - "neo4j" — server-based; requires a running Neo4j instance.

Both backends expose the same GraphStore interface so the rest of the codebase
never sees raw Cypher.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_NEO4J_URI = "bolt://localhost:7687"
_DEFAULT_NEO4J_USER = "neo4j"
_DEFAULT_NEO4J_PASS = "skillrouter"
_DEFAULT_KUZU_PATH = "~/.skillogy/graph.kuzu"


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class GraphStore(ABC):
    """Backend-agnostic interface for the skill trigger graph."""

    @abstractmethod
    def init_schema(self) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def merge_skill(self, name: str) -> None: ...

    @abstractmethod
    def link_triggered_intent(self, skill: str, label: str) -> None: ...

    @abstractmethod
    def link_triggered_signal(self, skill: str, kind: str, value: str) -> None: ...

    @abstractmethod
    def link_excluded_signal(self, skill: str, kind: str, value: str) -> None: ...

    @abstractmethod
    def link_relates_to(self, src: str, target: str) -> int:
        """Create RELATES_TO src→target only if target Skill already exists.

        Returns 1 if the edge was (re)merged, 0 if target was missing.
        """

    @abstractmethod
    def set_skill_metadata(
        self,
        name: str,
        description: str,
        source_path: str,
        body_length: int,
        scope: str,
    ) -> bool:
        """Set metadata on an existing Skill node. Returns True if matched."""

    @abstractmethod
    def score_candidates(
        self,
        intents: list[str],
        signal_pairs: list[tuple[str, str]],
        top_k: int,
    ) -> list[dict]:
        """Return up to top_k candidate skills, scored by triggered edges and
        filtered by exclusions. Each row:
          {name, description, source_path, scope, score, hits}
        hits is a list of {kind: 'Intent'|'Signal', id: str}.
        """

    @abstractmethod
    def fetch_related(
        self,
        skill_name: str,
        exclude: list[str],
        k: int,
    ) -> list[dict]:
        """Return up to k RELATES_TO neighbors of skill_name, omitting `exclude`.

        Each row: {name, description, scope}.
        """

    @abstractmethod
    def export_graph(self) -> dict:
        """Return {nodes: [...], edges: [...]} for visualization."""

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Neo4j implementation
# ---------------------------------------------------------------------------

_NEO4J_SCHEMA = [
    "CREATE CONSTRAINT skill_name_unique IF NOT EXISTS FOR (s:Skill) REQUIRE s.name IS UNIQUE",
    "CREATE INDEX intent_label_idx IF NOT EXISTS FOR (i:Intent) ON (i.label)",
    "CREATE INDEX signal_kind_value_idx IF NOT EXISTS FOR (s:Signal) ON (s.kind, s.value)",
]

_NEO4J_SCORE_CYPHER = """\
MATCH (s:Skill)-[:TRIGGERED_BY]->(n)
WHERE (n:Intent AND n.label IN $intents)
   OR (n:Signal AND [n.kind, n.value] IN $signal_pairs)
WITH s,
     sum(CASE WHEN n:Intent THEN 2.0 ELSE 1.0 END) AS score,
     collect(DISTINCT {kind: head(labels(n)), id: coalesce(n.label, n.value)}) AS hits
WHERE NOT EXISTS {
  MATCH (s)-[:EXCLUDED_BY]->(e:Signal)
  WHERE [e.kind, e.value] IN $signal_pairs
}
RETURN s.name AS name,
       s.description AS description,
       s.source_path AS source_path,
       s.scope AS scope,
       score,
       hits
ORDER BY score DESC
LIMIT $top_k
"""

_NEO4J_RELATED_CYPHER = """\
MATCH (s:Skill {name: $name})-[:RELATES_TO]->(t:Skill)
WHERE NOT t.name IN $exclude
RETURN t.name AS name, t.description AS description, t.scope AS scope
LIMIT $k
"""


class Neo4jStore(GraphStore):
    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None) -> None:
        from neo4j import GraphDatabase  # noqa: PLC0415
        self._driver = GraphDatabase.driver(
            uri or os.environ.get("NEO4J_URI", _DEFAULT_NEO4J_URI),
            auth=(
                user or os.environ.get("NEO4J_USER", _DEFAULT_NEO4J_USER),
                password or os.environ.get("NEO4J_PASSWORD", _DEFAULT_NEO4J_PASS),
            ),
        )

    @property
    def driver(self):
        return self._driver

    def init_schema(self) -> None:
        for stmt in _NEO4J_SCHEMA:
            self._driver.execute_query(stmt)

    def clear(self) -> None:
        self._driver.execute_query("MATCH (n) DETACH DELETE n")

    def merge_skill(self, name: str) -> None:
        self._driver.execute_query("MERGE (s:Skill {name: $name})", name=name)

    def link_triggered_intent(self, skill: str, label: str) -> None:
        self._driver.execute_query(
            "MERGE (i:Intent {label: $label}) "
            "WITH i MATCH (s:Skill {name: $skill}) "
            "MERGE (s)-[:TRIGGERED_BY]->(i)",
            label=label,
            skill=skill,
        )

    def link_triggered_signal(self, skill: str, kind: str, value: str) -> None:
        self._driver.execute_query(
            "MERGE (n:Signal {kind: $kind, value: $value}) "
            "WITH n MATCH (s:Skill {name: $skill}) "
            "MERGE (s)-[:TRIGGERED_BY]->(n)",
            kind=kind,
            value=value,
            skill=skill,
        )

    def link_excluded_signal(self, skill: str, kind: str, value: str) -> None:
        self._driver.execute_query(
            "MERGE (n:Signal {kind: $kind, value: $value}) "
            "WITH n MATCH (s:Skill {name: $skill}) "
            "MERGE (s)-[:EXCLUDED_BY]->(n)",
            kind=kind,
            value=value,
            skill=skill,
        )

    def link_relates_to(self, src: str, target: str) -> int:
        records, _, _ = self._driver.execute_query(
            """
            MATCH (s:Skill {name: $src})
            OPTIONAL MATCH (t:Skill {name: $target})
            WITH s, t WHERE t IS NOT NULL AND s <> t
            MERGE (s)-[:RELATES_TO]->(t)
            RETURN 1 AS created
            """,
            src=src,
            target=target,
        )
        return 1 if records else 0

    def set_skill_metadata(
        self, name: str, description: str, source_path: str, body_length: int, scope: str
    ) -> bool:
        records, _, _ = self._driver.execute_query(
            """
            MATCH (s:Skill {name: $name})
            SET s.description = $description,
                s.source_path = $source_path,
                s.body_length = $body_length,
                s.scope = $scope
            RETURN s.name AS name
            """,
            name=name,
            description=description,
            source_path=source_path,
            body_length=body_length,
            scope=scope,
        )
        return bool(records)

    def score_candidates(
        self, intents: list[str], signal_pairs: list[tuple[str, str]], top_k: int
    ) -> list[dict]:
        from neo4j import RoutingControl  # noqa: PLC0415
        records, _, _ = self._driver.execute_query(
            _NEO4J_SCORE_CYPHER,
            intents=list(intents),
            signal_pairs=[[k, v] for k, v in signal_pairs],
            top_k=top_k,
            routing_=RoutingControl.READ,
        )
        return [
            {
                "name": r["name"],
                "description": r["description"],
                "source_path": r["source_path"],
                "scope": r["scope"],
                "score": r["score"],
                "hits": list(r["hits"]),
            }
            for r in records
        ]

    def fetch_related(self, skill_name: str, exclude: list[str], k: int) -> list[dict]:
        records, _, _ = self._driver.execute_query(
            _NEO4J_RELATED_CYPHER,
            name=skill_name,
            exclude=list(exclude),
            k=k,
        )
        return [
            {
                "name": r["name"],
                "description": r["description"] or "",
                "scope": r["scope"] or "user",
            }
            for r in records
        ]

    def export_graph(self) -> dict:
        from neo4j import RoutingControl  # noqa: PLC0415

        node_records, _, _ = self._driver.execute_query(
            "MATCH (n) RETURN n", routing_=RoutingControl.READ,
        )
        nodes: list[dict] = []
        for record in node_records:
            n = record["n"]
            labels = list(n.labels)
            kind = labels[0] if labels else "Unknown"
            props = dict(n)
            entry: dict[str, Any] = {"kind": kind}
            if kind == "Skill":
                entry["id"] = props.get("name", "")
                entry["description"] = props.get("description", "")
                entry["source_path"] = props.get("source_path", "")
                entry["body_length"] = props.get("body_length", 0)
                entry["scope"] = props.get("scope", "user")
            elif kind == "Intent":
                entry["id"] = f"intent::{props.get('label', '')}"
                entry["label"] = props.get("label", "")
            elif kind == "Signal":
                entry["id"] = f"signal::{props.get('kind', '')}::{props.get('value', '')}"
                entry["signal_kind"] = props.get("kind", "")
                entry["value"] = props.get("value", "")
            else:
                entry["id"] = str(n.element_id)
            nodes.append(entry)

        edge_records, _, _ = self._driver.execute_query(
            "MATCH (a)-[r]->(b) RETURN a, r, b", routing_=RoutingControl.READ,
        )
        edges: list[dict] = []
        for record in edge_records:
            a, b, r = record["a"], record["b"], record["r"]
            edges.append({
                "src": _neo4j_node_id(a),
                "dst": _neo4j_node_id(b),
                "etype": r.type.lower(),
            })

        return {"nodes": nodes, "edges": edges}

    def close(self) -> None:
        self._driver.close()


def _neo4j_node_id(node) -> str:
    labels = list(node.labels)
    kind = labels[0] if labels else ""
    props = dict(node)
    if kind == "Skill":
        return props.get("name", str(node.element_id))
    if kind == "Intent":
        return f"intent::{props.get('label', '')}"
    if kind == "Signal":
        return f"signal::{props.get('kind', '')}::{props.get('value', '')}"
    return str(node.element_id)


# ---------------------------------------------------------------------------
# Kuzu implementation
# ---------------------------------------------------------------------------

# Kuzu requires upfront NODE/REL TABLE declarations; everything is nullable so
# MERGE-on-PK works the same way as Neo4j MERGE.
_KUZU_SCHEMA = [
    """CREATE NODE TABLE IF NOT EXISTS Skill (
        name STRING,
        description STRING,
        source_path STRING,
        body_length INT64,
        scope STRING,
        PRIMARY KEY (name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Intent (
        label STRING,
        PRIMARY KEY (label)
    )""",
    # Signal uses synthetic id "{kind}::{value}" because Kuzu PK is single-column.
    """CREATE NODE TABLE IF NOT EXISTS Signal (
        id STRING,
        kind STRING,
        value STRING,
        PRIMARY KEY (id)
    )""",
    "CREATE REL TABLE IF NOT EXISTS TRIGGERED_BY (FROM Skill TO Intent, FROM Skill TO Signal)",
    "CREATE REL TABLE IF NOT EXISTS EXCLUDED_BY (FROM Skill TO Signal)",
    "CREATE REL TABLE IF NOT EXISTS RELATES_TO (FROM Skill TO Skill)",
]


def _signal_id(kind: str, value: str) -> str:
    return f"{kind}::{value}"


class KuzuStore(GraphStore):
    def __init__(self, db_path: str | None = None) -> None:
        import kuzu  # noqa: PLC0415

        path = Path(db_path or os.environ.get("SKILLOGY_KUZU_PATH", _DEFAULT_KUZU_PATH)).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(path))
        self._conn = kuzu.Connection(self._db)
        self._path = path

    def _execute(self, query: str, params: dict | None = None):
        return self._conn.execute(query, parameters=params or {})

    @staticmethod
    def _rows(result) -> list[dict]:
        cols = result.get_column_names()
        out: list[dict] = []
        while result.has_next():
            row = result.get_next()
            out.append(dict(zip(cols, row)))
        return out

    def init_schema(self) -> None:
        for stmt in _KUZU_SCHEMA:
            self._execute(stmt)

    def clear(self) -> None:
        # Order matters: rels first, then nodes (DETACH DELETE not always available).
        for rel in ("TRIGGERED_BY", "EXCLUDED_BY", "RELATES_TO"):
            self._execute(f"MATCH ()-[r:{rel}]->() DELETE r")
        for label in ("Skill", "Intent", "Signal"):
            self._execute(f"MATCH (n:{label}) DELETE n")

    def merge_skill(self, name: str) -> None:
        self._execute("MERGE (s:Skill {name: $name})", {"name": name})

    def link_triggered_intent(self, skill: str, label: str) -> None:
        self._execute("MERGE (i:Intent {label: $label})", {"label": label})
        self._execute(
            """
            MATCH (s:Skill {name: $skill}), (i:Intent {label: $label})
            MERGE (s)-[:TRIGGERED_BY]->(i)
            """,
            {"skill": skill, "label": label},
        )

    def link_triggered_signal(self, skill: str, kind: str, value: str) -> None:
        sid = _signal_id(kind, value)
        self._execute(
            """
            MERGE (n:Signal {id: $id})
            ON CREATE SET n.kind = $kind, n.value = $value
            """,
            {"id": sid, "kind": kind, "value": value},
        )
        self._execute(
            """
            MATCH (s:Skill {name: $skill}), (n:Signal {id: $id})
            MERGE (s)-[:TRIGGERED_BY]->(n)
            """,
            {"skill": skill, "id": sid},
        )

    def link_excluded_signal(self, skill: str, kind: str, value: str) -> None:
        sid = _signal_id(kind, value)
        self._execute(
            """
            MERGE (n:Signal {id: $id})
            ON CREATE SET n.kind = $kind, n.value = $value
            """,
            {"id": sid, "kind": kind, "value": value},
        )
        self._execute(
            """
            MATCH (s:Skill {name: $skill}), (n:Signal {id: $id})
            MERGE (s)-[:EXCLUDED_BY]->(n)
            """,
            {"skill": skill, "id": sid},
        )

    def link_relates_to(self, src: str, target: str) -> int:
        if src == target:
            return 0
        # Existence check (Kuzu MERGE on rel doesn't natively gate on counterpart existence).
        result = self._execute(
            "MATCH (t:Skill {name: $name}) RETURN count(t) AS c",
            {"name": target},
        )
        rows = self._rows(result)
        if not rows or rows[0]["c"] == 0:
            return 0
        self._execute(
            """
            MATCH (s:Skill {name: $src}), (t:Skill {name: $target})
            MERGE (s)-[:RELATES_TO]->(t)
            """,
            {"src": src, "target": target},
        )
        return 1

    def set_skill_metadata(
        self, name: str, description: str, source_path: str, body_length: int, scope: str
    ) -> bool:
        result = self._execute(
            "MATCH (s:Skill {name: $name}) RETURN count(s) AS c",
            {"name": name},
        )
        rows = self._rows(result)
        if not rows or rows[0]["c"] == 0:
            return False
        self._execute(
            """
            MATCH (s:Skill {name: $name})
            SET s.description = $description,
                s.source_path = $source_path,
                s.body_length = $body_length,
                s.scope = $scope
            """,
            {
                "name": name,
                "description": description,
                "source_path": source_path,
                "body_length": body_length,
                "scope": scope,
            },
        )
        return True

    def score_candidates(
        self, intents: list[str], signal_pairs: list[tuple[str, str]], top_k: int
    ) -> list[dict]:
        # Two passes: collect hits per skill; subtract skills hit by EXCLUDED_BY.
        signal_ids = [_signal_id(k, v) for k, v in signal_pairs]

        # Skip the query entirely when both filter lists are empty — Kuzu refuses
        # WHERE x IN [] as semantically empty and will return nothing anyway, but
        # the explicit short-circuit avoids backend-specific edge cases.
        if not intents and not signal_ids:
            return []

        intent_rows: list[dict] = []
        if intents:
            r = self._execute(
                """
                MATCH (s:Skill)-[:TRIGGERED_BY]->(i:Intent)
                WHERE i.label IN $intents
                RETURN s.name AS name,
                       s.description AS description,
                       s.source_path AS source_path,
                       s.scope AS scope,
                       i.label AS hit_id,
                       'Intent' AS hit_kind
                """,
                {"intents": list(intents)},
            )
            intent_rows = self._rows(r)

        signal_rows: list[dict] = []
        if signal_ids:
            r = self._execute(
                """
                MATCH (s:Skill)-[:TRIGGERED_BY]->(n:Signal)
                WHERE n.id IN $ids
                RETURN s.name AS name,
                       s.description AS description,
                       s.source_path AS source_path,
                       s.scope AS scope,
                       n.value AS hit_id,
                       'Signal' AS hit_kind
                """,
                {"ids": signal_ids},
            )
            signal_rows = self._rows(r)

        excluded: set[str] = set()
        if signal_ids:
            r = self._execute(
                """
                MATCH (s:Skill)-[:EXCLUDED_BY]->(n:Signal)
                WHERE n.id IN $ids
                RETURN DISTINCT s.name AS name
                """,
                {"ids": signal_ids},
            )
            excluded = {row["name"] for row in self._rows(r)}

        # Aggregate
        by_skill: dict[str, dict] = {}
        for row in intent_rows + signal_rows:
            name = row["name"]
            if name in excluded:
                continue
            entry = by_skill.setdefault(name, {
                "name": name,
                "description": row["description"],
                "source_path": row["source_path"],
                "scope": row["scope"],
                "score": 0.0,
                "hits": [],
                "_seen": set(),
            })
            weight = 2.0 if row["hit_kind"] == "Intent" else 1.0
            entry["score"] += weight
            hit_key = (row["hit_kind"], row["hit_id"])
            if hit_key not in entry["_seen"]:
                entry["_seen"].add(hit_key)
                entry["hits"].append({"kind": row["hit_kind"], "id": row["hit_id"]})

        ranked = sorted(by_skill.values(), key=lambda r: r["score"], reverse=True)[:top_k]
        for r in ranked:
            r.pop("_seen", None)
        return ranked

    def fetch_related(self, skill_name: str, exclude: list[str], k: int) -> list[dict]:
        r = self._execute(
            """
            MATCH (s:Skill {name: $name})-[:RELATES_TO]->(t:Skill)
            RETURN t.name AS name, t.description AS description, t.scope AS scope
            """,
            {"name": skill_name},
        )
        rows = self._rows(r)
        excl = set(exclude)
        out: list[dict] = []
        for row in rows:
            if row["name"] in excl:
                continue
            out.append({
                "name": row["name"],
                "description": row["description"] or "",
                "scope": row["scope"] or "user",
            })
            if len(out) >= k:
                break
        return out

    def export_graph(self) -> dict:
        nodes: list[dict] = []

        r = self._execute(
            "MATCH (s:Skill) RETURN s.name AS name, s.description AS description, "
            "s.source_path AS source_path, s.body_length AS body_length, s.scope AS scope"
        )
        for row in self._rows(r):
            nodes.append({
                "kind": "Skill",
                "id": row["name"] or "",
                "description": row["description"] or "",
                "source_path": row["source_path"] or "",
                "body_length": row["body_length"] or 0,
                "scope": row["scope"] or "user",
            })

        r = self._execute("MATCH (i:Intent) RETURN i.label AS label")
        for row in self._rows(r):
            label = row["label"] or ""
            nodes.append({"kind": "Intent", "id": f"intent::{label}", "label": label})

        r = self._execute("MATCH (n:Signal) RETURN n.kind AS kind, n.value AS value")
        for row in self._rows(r):
            kind = row["kind"] or ""
            value = row["value"] or ""
            nodes.append({
                "kind": "Signal",
                "id": f"signal::{kind}::{value}",
                "signal_kind": kind,
                "value": value,
            })

        edges: list[dict] = []

        r = self._execute(
            "MATCH (s:Skill)-[:TRIGGERED_BY]->(i:Intent) "
            "RETURN s.name AS src, i.label AS dst_label"
        )
        for row in self._rows(r):
            edges.append({
                "src": row["src"],
                "dst": f"intent::{row['dst_label']}",
                "etype": "triggered_by",
            })

        r = self._execute(
            "MATCH (s:Skill)-[:TRIGGERED_BY]->(n:Signal) "
            "RETURN s.name AS src, n.kind AS dst_kind, n.value AS dst_value"
        )
        for row in self._rows(r):
            edges.append({
                "src": row["src"],
                "dst": f"signal::{row['dst_kind']}::{row['dst_value']}",
                "etype": "triggered_by",
            })

        r = self._execute(
            "MATCH (s:Skill)-[:EXCLUDED_BY]->(n:Signal) "
            "RETURN s.name AS src, n.kind AS dst_kind, n.value AS dst_value"
        )
        for row in self._rows(r):
            edges.append({
                "src": row["src"],
                "dst": f"signal::{row['dst_kind']}::{row['dst_value']}",
                "etype": "excluded_by",
            })

        r = self._execute(
            "MATCH (s:Skill)-[:RELATES_TO]->(t:Skill) "
            "RETURN s.name AS src, t.name AS dst"
        )
        for row in self._rows(r):
            edges.append({"src": row["src"], "dst": row["dst"], "etype": "relates_to"})

        return {"nodes": nodes, "edges": edges}

    def close(self) -> None:
        # Kuzu Connection/Database close on GC; nothing required here.
        pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_store_singleton: GraphStore | None = None


def get_store() -> GraphStore:
    """Return the singleton GraphStore selected by SKILLOGY_DB.

    Default backend is "kuzu" — embedded, zero external dependencies. Set
    SKILLOGY_DB=neo4j to use the server-based Neo4j backend instead.
    """
    global _store_singleton  # noqa: PLW0603
    if _store_singleton is None:
        backend = os.environ.get("SKILLOGY_DB", "kuzu").strip().lower()
        if backend == "neo4j":
            _store_singleton = Neo4jStore()
        elif backend == "kuzu":
            _store_singleton = KuzuStore()
        else:
            raise RuntimeError(
                f"Unknown SKILLOGY_DB={backend!r}. Use 'kuzu' (default) or 'neo4j'."
            )
    return _store_singleton


def close_store() -> None:
    global _store_singleton  # noqa: PLW0603
    if _store_singleton is not None:
        _store_singleton.close()
        _store_singleton = None
