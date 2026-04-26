"""GraphRAG routing engine — trigger-surface-based skill selection (no vector similarity)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from skillogy.infra.llm import LLMClient, get_llm_client
from skillogy.infra.db import GraphStore, get_store
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


class Router:
    """Pure GraphRAG router. No vector similarity — trigger surface graph only."""

    def __init__(self, store: GraphStore | None = None, llm: LLMClient | None = None) -> None:
        self.store = store or get_store()
        self.llm = llm or get_llm_client()

    def find_skill(
        self,
        query: str,
        top_k: int = 5,
        judge: bool = True,
        extract: bool = True,
        load_body: bool = True,
    ) -> RoutingResult:
        # Step 1: extract Intent + Signal nodes from query
        if extract:
            intents, signals = self._extract_query_nodes(query)
        else:
            kws = [w.lower() for w in query.split() if len(w) > 2]
            intents = []
            signals = [Signal(kind="keyword", value=k) for k in kws[:10]]

        signal_pairs = [(s.kind, s.value) for s in signals]

        # Step 2: collect + score candidates via the store
        rows = self.store.score_candidates(intents=intents, signal_pairs=signal_pairs, top_k=top_k)

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

        winner_row = next((r for r in rows if r["name"] == winner_name), rows[0])
        winner_score = winner_row["score"]

        reasoning_path = [
            (winner_name, "triggered_by", hit.get("id", ""))
            for hit in winner_row.get("hits", [])
            if hit.get("id")
        ]

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
        exclude_set: set[str] = {winner_name} | {a["name"] for a in alternatives if a.get("name")}
        related = self.store.fetch_related(
            skill_name=winner_name,
            exclude=list(exclude_set),
            k=relates_k,
        )
        for r in related:
            r["via"] = "relates_to"
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

    def _llm_judge(self, query: str, rows: list[dict]) -> str:
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
