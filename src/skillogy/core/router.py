"""GraphRAG routing engine — trigger-surface-based skill selection (no vector similarity)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from neo4j import Driver, RoutingControl

from skillogy.infra.llm import LLMClient, get_llm_client
from skillogy.infra.db import get_driver
from skillogy.domain.types import Signal

logger = logging.getLogger(__name__)


@dataclass
class RoutingResult:
    skill_name: str
    skill_body: str                                # full SKILL.md content
    reasoning_path: list[tuple[str, str, str]]    # [(src_node, etype, dst_node), ...]
    alternatives: list[dict]                       # [{"name": str, "score": float}, ...]
    score: float = 0.0


_QUERY_EXTRACTION_SYSTEM = """\
You are parsing a developer's message to extract trigger signals for routing.
Output STRICT JSON: {"intents": [...], "signals": [{"kind": ..., "value": ...}, ...]}
- intents: lowercase short user-goal phrases derived from the message.
- signals: concrete tokens — keywords, tool names, file extensions, error codes — that may match a skill's trigger surface. kind ∈ {keyword, file_ext, tool_name, error_pattern, pattern}.
Return ONLY the JSON object."""


_JUDGE_SYSTEM = """\
You will receive a developer's request and a shortlist of candidate Claude Code skills with
their descriptions and graph-derived scores. Pick the SINGLE best skill for this request.
Output STRICT JSON: {"winner": "<skill_name>", "reason": "<one short sentence>"}
Return ONLY the JSON object, no preamble."""


# Cypher for fetching RELATES_TO companion skills
# SKILLOGY_RELATES_K env var controls the limit (default 3)
_RELATED_NEIGHBORS_CYPHER = """
MATCH (s:Skill {name: $name})-[:RELATES_TO]->(t:Skill)
WHERE NOT t.name IN $exclude
RETURN t.name AS name, t.description AS description, t.scope AS scope
LIMIT $k
"""

# Cypher for collecting + scoring candidates via trigger surface
_COLLECT_SCORE_CYPHER = """\
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


class Router:
    """Pure GraphRAG router. No vector similarity — trigger surface graph only."""

    def __init__(self, driver: Driver | None = None, llm: LLMClient | None = None) -> None:
        self.driver = driver or get_driver()
        self.llm = llm or get_llm_client()

    def find_skill(self, query: str, top_k: int = 5, judge: bool = True, extract: bool = True, load_body: bool = True) -> RoutingResult:
        # Step 1: extract Intent + Signal nodes from query
        if extract:
            intents, signals = self._extract_query_nodes(query)
        else:
            # Keyword-only fast path (no LLM call) — for hook use
            kws = [w.lower() for w in query.split() if len(w) > 2]
            intents = []
            signals = [Signal(kind="keyword", value=k) for k in kws[:10]]

        # Step 2: collect + score candidates via single Cypher query
        rows = self._collect_and_score(intents, signals, top_k=top_k)

        if not rows:
            return RoutingResult(
                skill_name="",
                skill_body="",
                reasoning_path=[],
                alternatives=[],
                score=0.0,
            )

        # Step 3: optional LLM judge for final pick when multiple candidates
        if judge and len(rows) > 1:
            winner_name = self._llm_judge(query, rows)
        else:
            winner_name = rows[0]["name"]

        # Find winner row
        winner_row = next((r for r in rows if r["name"] == winner_name), rows[0])
        winner_score = winner_row["score"]

        # Build reasoning path from hits in the winner row
        reasoning_path = self._reasoning_path_from_hits(winner_name, winner_row["hits"])

        # Read winner skill body from disk via source_path attr (only when requested)
        skill_body = ""
        if load_body:
            source_path = winner_row.get("source_path") or ""
            if source_path:
                try:
                    skill_body = Path(source_path).read_text(encoding="utf-8")
                except OSError as exc:
                    logger.warning("Could not read SKILL.md at %s: %s", source_path, exc)

        alternatives = [
            {"name": r["name"], "score": r["score"]}
            for r in rows
            if r["name"] != winner_name
        ]

        relates_k = int(os.environ.get("SKILLOGY_RELATES_K", "3"))
        exclude: set[str] = {winner_name} | {a["name"] for a in alternatives if a.get("name")}
        related = self._fetch_related(winner_name, exclude, relates_k)
        alternatives = list(alternatives) + related

        return RoutingResult(
            skill_name=winner_name,
            skill_body=skill_body,
            reasoning_path=reasoning_path,
            alternatives=alternatives,
            score=winner_score,
        )

    # ------------------------------------------------------------------ internals

    def _extract_query_nodes(self, query: str) -> tuple[list[str], list[Signal]]:
        """Single LLM call to parse query into Intent + Signal node candidates."""
        try:
            raw = self.llm.complete(
                prompt=query,
                system=_QUERY_EXTRACTION_SYSTEM,
                max_tokens=2048,
                temperature=0.0,
            )
            data = json.loads(raw.strip())
            intents = [str(i).lower() for i in data.get("intents", [])]
            signals = []
            for s in data.get("signals", []):
                if isinstance(s, dict) and "kind" in s and "value" in s:
                    signals.append(Signal(kind=str(s["kind"]).lower(), value=str(s["value"])))
            return intents, signals
        except (json.JSONDecodeError, ValueError, TypeError, RuntimeError) as exc:
            logger.warning("Query extraction failed (%s); falling back to keyword splitting.", exc)
            kws = [w.lower() for w in query.split() if len(w) > 2]
            return [], [Signal(kind="keyword", value=k) for k in kws[:10]]

    def _collect_and_score(
        self,
        intents: list[str],
        signals: list[Signal],
        top_k: int = 5,
    ) -> list[dict]:
        """Execute single Cypher query to collect and score candidate skills."""
        signal_pairs = [[s.kind, s.value] for s in signals]

        records, _, _ = self.driver.execute_query(
            _COLLECT_SCORE_CYPHER,
            intents=intents,
            signal_pairs=signal_pairs,
            top_k=top_k,
            routing_=RoutingControl.READ,
        )

        rows = []
        for record in records:
            rows.append({
                "name": record["name"],
                "description": record["description"],
                "source_path": record["source_path"],
                "scope": record["scope"],
                "score": record["score"],
                "hits": list(record["hits"]),
            })
        return rows

    def _reasoning_path_from_hits(
        self,
        skill_name: str,
        hits: list[dict],
    ) -> list[tuple[str, str, str]]:
        """Derive reasoning path from hits returned by _collect_and_score."""
        path: list[tuple[str, str, str]] = []
        for hit in hits:
            node_id = hit.get("id", "")
            node_kind = hit.get("kind", "")
            if node_id:
                path.append((skill_name, "triggered_by", node_id))
        return path

    def _fetch_related(self, top_skill_name: str, exclude: set, k: int) -> list[dict]:
        """Fetch RELATES_TO companion skills for the top-matched skill.

        Returns up to k related skills that are not already in exclude.
        Each entry carries via="relates_to" so consumers can distinguish
        trigger-based picks from companion picks.
        """
        if not top_skill_name:
            return []
        records, _, _ = self.driver.execute_query(
            _RELATED_NEIGHBORS_CYPHER,
            name=top_skill_name,
            exclude=list(exclude),
            k=k,
        )
        return [
            {
                "name": r["name"],
                "description": r["description"] or "",
                "scope": r["scope"] or "user",
                "via": "relates_to",
            }
            for r in records
        ]

    def _llm_judge(self, query: str, rows: list[dict]) -> str:
        """Pick best from candidates using a single LLM call."""
        candidates_text = "\n".join(
            f"- {r['name']} (score={r['score']:.2f}): {(r.get('description') or '')[:300]}"
            for r in rows
        )
        prompt = f"""User request: {query}\n\nCandidates:\n{candidates_text}\n\nPick the best one."""
        try:
            raw = self.llm.complete(prompt=prompt, system=_JUDGE_SYSTEM, max_tokens=200, temperature=0.0)
            data = json.loads(raw.strip())
            winner = data.get("winner")
            if winner and any(r["name"] == winner for r in rows):
                return winner
        except (json.JSONDecodeError, ValueError, TypeError, RuntimeError) as exc:
            logger.warning("LLM judge failed (%s); falling back to top score.", exc)
        return rows[0]["name"]
