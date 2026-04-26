"""Tests for skillogy.core.router — backend-agnostic via FakeStore."""

from __future__ import annotations

import re
from pathlib import Path

from skillogy.core.router import Router

from tests.conftest import FakeStore


# ---------------------------------------------------------------------------
# MockLLM
# ---------------------------------------------------------------------------

class MockLLM:
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
# Routing
# ---------------------------------------------------------------------------

def test_routing_picks_top_scored_skill() -> None:
    store = FakeStore(canned_score_rows=[
        {
            "name": "typescript-build-error",
            "description": "Fixes TypeScript compilation errors",
            "source_path": None,
            "scope": "user",
            "score": 3.0,
            "hits": [{"kind": "Intent", "id": "fix"}, {"kind": "Signal", "id": "tsc"}],
        }
    ])
    llm = MockLLM(['{"intents": ["fix"], "signals": [{"kind": "keyword", "value": "tsc"}]}'])

    router = Router(store=store, llm=llm)
    result = router.find_skill("TypeScript build error", top_k=5, judge=False)

    assert result.skill_name == "typescript-build-error"
    assert result.score == 3.0

    edge_types = {etype for _, etype, _ in result.reasoning_path}
    assert "triggered_by" in edge_types

    # Store was queried with the extracted intent + signal pair
    assert store.score_calls == [{
        "intents": ["fix"],
        "signal_pairs": [("keyword", "tsc")],
        "top_k": 5,
    }]


def test_routing_returns_empty_when_no_candidates() -> None:
    store = FakeStore(canned_score_rows=[])
    llm = MockLLM(['{"intents": ["format"], "signals": []}'])

    router = Router(store=store, llm=llm)
    result = router.find_skill("format my code", top_k=5, judge=False)

    assert result.skill_name == ""
    assert result.skill_body == ""
    assert result.reasoning_path == []
    assert result.alternatives == []
    assert result.score == 0.0


def test_routing_passes_signal_pairs_to_store() -> None:
    """The router must forward extracted signal pairs verbatim — store handles the rest."""
    store = FakeStore(canned_score_rows=[])
    llm = MockLLM(['{"intents": ["fix"], "signals": [{"kind": "file_ext", "value": ".js"}]}'])

    router = Router(store=store, llm=llm)
    router.find_skill("fix this .js file", top_k=5, judge=False)

    assert store.score_calls[0]["signal_pairs"] == [("file_ext", ".js")]


def test_no_vector_embeddings_used() -> None:
    router_path = Path(__file__).parent.parent.parent.parent / "src" / "skillogy" / "core" / "router.py"
    source = router_path.read_text(encoding="utf-8")
    forbidden = re.compile(r"\b(embed|embedding|embeddings|cosine)\b", re.IGNORECASE)
    matches = forbidden.findall(source)
    assert matches == [], f"Found forbidden terms in router.py: {matches}"


def test_judge_picks_winner_from_multiple_candidates() -> None:
    primary = [
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
    store = FakeStore(canned_score_rows=primary)
    llm = MockLLM([
        '{"intents": ["fix"], "signals": [{"kind": "keyword", "value": "tsc"}]}',
        '{"winner": "typescript-build-error", "reason": "Direct match."}',
    ])

    router = Router(store=store, llm=llm)
    result = router.find_skill("TypeScript build error", top_k=5, judge=True)

    assert result.skill_name == "typescript-build-error"
    assert len(result.alternatives) == 1
    assert result.alternatives[0]["name"] == "runtime-debugger"


def test_skill_body_loaded_from_disk(tmp_path: Path) -> None:
    skill_md = tmp_path / "SKILL.md"
    body = "# TypeScript Build Error\n\nFixes TS compiler failures."
    skill_md.write_text(body, encoding="utf-8")

    store = FakeStore(canned_score_rows=[{
        "name": "typescript-build-error",
        "description": "Fixes TypeScript compilation errors",
        "source_path": str(skill_md),
        "scope": "user",
        "score": 3.0,
        "hits": [{"kind": "Signal", "id": "tsc"}],
    }])
    llm = MockLLM(['{"intents": ["fix"], "signals": [{"kind": "keyword", "value": "tsc"}]}'])

    router = Router(store=store, llm=llm)
    result = router.find_skill("tsc fix", top_k=5, judge=False)

    assert result.skill_name == "typescript-build-error"
    assert result.skill_body == body


# ---------------------------------------------------------------------------
# RELATES_TO companion skills
# ---------------------------------------------------------------------------

def test_alternatives_includes_relates_to_neighbors() -> None:
    store = FakeStore(
        canned_score_rows=[{
            "name": "skill-a", "description": "Skill A", "source_path": None,
            "scope": "user", "score": 3.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        }],
        canned_related_rows=[
            {"name": "skill-b", "description": "Skill B", "scope": "user"},
            {"name": "skill-c", "description": "Skill C", "scope": "user"},
        ],
    )
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(store=store, llm=llm)
    result = router.find_skill("fix something", judge=False)

    assert result.skill_name == "skill-a"
    assert len(result.alternatives) == 2
    assert all(a.get("via") == "relates_to" for a in result.alternatives)
    names = [a["name"] for a in result.alternatives]
    assert "skill-b" in names
    assert "skill-c" in names


def test_relates_to_dedups_against_primary_alternatives() -> None:
    store = FakeStore(
        canned_score_rows=[
            {
                "name": "skill-a", "description": "Skill A", "source_path": None,
                "scope": "user", "score": 4.0,
                "hits": [{"kind": "Intent", "id": "fix"}],
            },
            {
                "name": "skill-b", "description": "Skill B", "source_path": None,
                "scope": "user", "score": 2.0,
                "hits": [{"kind": "Intent", "id": "fix"}],
            },
        ],
        canned_related_rows=[
            {"name": "skill-b", "description": "Skill B", "scope": "user"},
            {"name": "skill-c", "description": "Skill C", "scope": "user"},
        ],
    )
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(store=store, llm=llm)
    result = router.find_skill("fix something", judge=False)

    names = [a["name"] for a in result.alternatives]
    # skill-b stays only as the primary alt; skill-c added via relates_to
    assert names.count("skill-b") == 1
    assert "skill-c" in names

    skill_b_entry = next(a for a in result.alternatives if a["name"] == "skill-b")
    assert "via" not in skill_b_entry
    skill_c_entry = next(a for a in result.alternatives if a["name"] == "skill-c")
    assert skill_c_entry["via"] == "relates_to"


def test_relates_to_respects_env_limit(monkeypatch) -> None:
    monkeypatch.setenv("SKILLOGY_RELATES_K", "1")
    store = FakeStore(
        canned_score_rows=[{
            "name": "skill-a", "description": "", "source_path": None,
            "scope": "user", "score": 3.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        }],
        canned_related_rows=[
            {"name": "skill-b", "description": "", "scope": "user"},
        ],
    )
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(store=store, llm=llm)
    result = router.find_skill("fix something", judge=False)

    assert store.related_call_kwargs is not None
    assert store.related_call_kwargs["k"] == 1
    relates = [a for a in result.alternatives if a.get("via") == "relates_to"]
    assert len(relates) == 1


def test_no_relates_to_query_when_no_match() -> None:
    store = FakeStore(canned_score_rows=[])
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(store=store, llm=llm)
    result = router.find_skill("fix something", judge=False)

    assert result.skill_name == ""
    assert result.alternatives == []
    assert store.related_call_kwargs is None


def test_relates_to_handles_zero_neighbors() -> None:
    store = FakeStore(
        canned_score_rows=[{
            "name": "skill-a", "description": "", "source_path": None,
            "scope": "user", "score": 3.0,
            "hits": [{"kind": "Intent", "id": "fix"}],
        }],
        canned_related_rows=[],
    )
    llm = MockLLM(['{"intents": ["fix"], "signals": []}'])

    router = Router(store=store, llm=llm)
    result = router.find_skill("fix something", judge=False)

    assert result.skill_name == "skill-a"
    assert result.alternatives == []
