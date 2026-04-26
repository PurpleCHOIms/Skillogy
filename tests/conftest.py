"""Pytest configuration and shared test helpers.

`FakeStore` mimics the GraphStore interface so unit tests can assert on
*semantic* operations (merge_skill, link_triggered_intent, ...) instead of
backend-specific Cypher strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from skillogy.infra.db import GraphStore


@dataclass
class FakeStore(GraphStore):
    """In-memory recorder + canned-result fake for the GraphStore interface."""

    schema_inited: bool = False
    cleared: bool = False
    merged_skills: list[str] = field(default_factory=list)
    triggered_intents: list[tuple[str, str]] = field(default_factory=list)
    triggered_signals: list[tuple[str, str, str]] = field(default_factory=list)
    excluded_signals: list[tuple[str, str, str]] = field(default_factory=list)
    relates_to_calls: list[tuple[str, str]] = field(default_factory=list)
    metadata_calls: list[dict] = field(default_factory=list)

    # Canned routes — populated per test
    canned_score_rows: list[dict] = field(default_factory=list)
    canned_related_rows: list[dict] = field(default_factory=list)
    related_call_kwargs: dict | None = None
    canned_export: dict | None = None
    score_calls: list[dict] = field(default_factory=list)

    # Behavioural toggles
    relates_to_existing_targets: set[str] | None = None  # None => all targets exist
    metadata_existing_skills: set[str] | None = None     # None => all match

    def init_schema(self) -> None:
        self.schema_inited = True

    def clear(self) -> None:
        self.cleared = True

    def merge_skill(self, name: str) -> None:
        self.merged_skills.append(name)

    def link_triggered_intent(self, skill: str, label: str) -> None:
        self.triggered_intents.append((skill, label))

    def link_triggered_signal(self, skill: str, kind: str, value: str) -> None:
        self.triggered_signals.append((skill, kind, value))

    def link_excluded_signal(self, skill: str, kind: str, value: str) -> None:
        self.excluded_signals.append((skill, kind, value))

    def link_relates_to(self, src: str, target: str) -> int:
        if src == target:
            return 0
        if self.relates_to_existing_targets is not None and target not in self.relates_to_existing_targets:
            self.relates_to_calls.append((src, target))
            return 0
        self.relates_to_calls.append((src, target))
        return 1

    def set_skill_metadata(
        self,
        name: str,
        description: str,
        source_path: str,
        body_length: int,
        scope: str,
    ) -> bool:
        self.metadata_calls.append({
            "name": name,
            "description": description,
            "source_path": source_path,
            "body_length": body_length,
            "scope": scope,
        })
        if self.metadata_existing_skills is not None and name not in self.metadata_existing_skills:
            return False
        return True

    def score_candidates(
        self,
        intents: list[str],
        signal_pairs: list[tuple[str, str]],
        top_k: int,
    ) -> list[dict]:
        self.score_calls.append({
            "intents": list(intents),
            "signal_pairs": list(signal_pairs),
            "top_k": top_k,
        })
        return [dict(r) for r in self.canned_score_rows[:top_k]]

    def fetch_related(self, skill_name: str, exclude: list[str], k: int) -> list[dict]:
        self.related_call_kwargs = {"skill_name": skill_name, "exclude": list(exclude), "k": k}
        excl = set(exclude)
        return [dict(r) for r in self.canned_related_rows if r["name"] not in excl][:k]

    def export_graph(self) -> dict:
        return self.canned_export or {"nodes": [], "edges": []}

    def close(self) -> None:
        pass
