"""Skill-trigger-rate benchmark runner. Three conditions: Native / Vector / SOG."""

from __future__ import annotations
import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from skillogy.infra.llm import LLMClient, get_llm_client
from skillogy.infra.scanner import ParsedSkill, scan_skills

logger = logging.getLogger(__name__)


@dataclass
class QueryTrace:
    id: str
    query: str
    gold: str
    condition: str
    picked: str
    top_k: list[str]
    correct: bool
    input_tokens: int
    latency_ms: float


@dataclass
class ConditionStats:
    condition: str
    n: int
    trigger_accuracy: float
    trigger_accuracy_ci_low: float
    trigger_accuracy_ci_high: float
    recall_at_5: float
    mean_input_tokens: float
    p95_latency_ms: float


# ---------------- Baseline-Native ----------------

_NATIVE_SYSTEM = """You are simulating Claude Code's native skill discovery.
Given a user request and a list of available skills, pick the ONE skill whose
description best matches what the user is asking for.

Output STRICT JSON: {"top_k": [name1, name2, ...]} — list the 5 most relevant
skill names in priority order. Return ONLY the JSON object."""


def native_top_k(
    query: str, skills: list[ParsedSkill], llm: LLMClient, k: int = 5
) -> tuple[list[str], int, float]:
    catalog = "\n".join(f"- {s.name}: {s.description[:200]}" for s in skills)
    prompt = f"USER REQUEST: {query}\n\nAVAILABLE SKILLS:\n{catalog}"
    start = time.perf_counter()
    raw = llm.complete(prompt=prompt, system=_NATIVE_SYSTEM, max_tokens=400, temperature=0.0)
    latency_ms = (time.perf_counter() - start) * 1000
    try:
        data = json.loads(raw.strip())
        top_k = [str(n).strip().lower() for n in data.get("top_k", [])][:k]
    except (json.JSONDecodeError, ValueError, TypeError):
        top_k = []
    # Estimate tokens roughly: 1 token ≈ 4 chars
    input_tokens = (len(prompt) + len(_NATIVE_SYSTEM)) // 4
    return top_k, input_tokens, latency_ms


# ---------------- Baseline-Vector ----------------

class _VectorIndex:
    """sentence-transformers fallback if no API key."""

    def __init__(self, skills: list[ParsedSkill]) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer("all-MiniLM-L6-v2")
        self._skills = skills
        texts = [f"{s.name}: {s.description}" for s in skills]
        self._embeddings = self._model.encode(texts, normalize_embeddings=True)

    def topk(self, query: str, k: int = 5) -> list[str]:
        import numpy as np

        q = self._model.encode([query], normalize_embeddings=True)[0]
        scores = self._embeddings @ q
        order = np.argsort(-scores)[:k]
        return [self._skills[i].name for i in order]


_VECTOR_JUDGE_SYSTEM = """Pick the single best skill from the shortlist for the user's request.
Output STRICT JSON: {"winner": name, "top_k": [n1, n2, ...]} ordered by relevance."""


def vector_top_k(
    query: str,
    skills: list[ParsedSkill],
    llm: LLMClient,
    vec_index: _VectorIndex,
    k: int = 5,
) -> tuple[list[str], int, float]:
    start = time.perf_counter()
    shortlist = vec_index.topk(query, k=10)
    shortlist_set = set(shortlist)
    catalog = "\n".join(
        f"- {s.name}: {s.description[:200]}"
        for s in skills
        if s.name in shortlist_set
    )
    prompt = f"USER REQUEST: {query}\n\nSHORTLIST:\n{catalog}"
    raw = llm.complete(prompt=prompt, system=_VECTOR_JUDGE_SYSTEM, max_tokens=300, temperature=0.0)
    latency_ms = (time.perf_counter() - start) * 1000
    try:
        data = json.loads(raw.strip())
        top_k_list = [str(n).strip().lower() for n in data.get("top_k", [])][:k]
        if not top_k_list and data.get("winner"):
            top_k_list = [str(data["winner"]).strip().lower()]
    except (json.JSONDecodeError, ValueError, TypeError):
        top_k_list = shortlist[:k]
    input_tokens = (len(prompt) + len(_VECTOR_JUDGE_SYSTEM)) // 4
    return top_k_list, input_tokens, latency_ms


# ---------------- Ours-SOG ----------------

def sog_top_k(query: str, router: object) -> tuple[list[str], int, float]:
    start = time.perf_counter()
    try:
        result = router.find_skill(query=query, top_k=5, judge=True)  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning("SOG router error: %s", exc)
        return [], 0, (time.perf_counter() - start) * 1000
    latency_ms = (time.perf_counter() - start) * 1000
    top_k: list[str] = []
    if result.skill_name:
        top_k.append(result.skill_name.lower())
    for alt in result.alternatives or []:
        n = str(alt.get("name", "")).lower()
        if n and n not in top_k:
            top_k.append(n)
        if len(top_k) >= 5:
            break
    # SOG token cost: best-effort estimate (1 extraction call + 1 judge call)
    input_tokens = 800
    return top_k, input_tokens, latency_ms


# ---------------- Claude Code CLI conditions ----------------

def claude_native_top_k(query: str) -> tuple[list[str], int, float]:
    """Run Claude Code with hook disabled. Detect native skill trigger."""
    from bench.claude_runner import run_claude_query
    result = run_claude_query(query)
    top_k = result["skill_calls"][:5]  # skills actually called
    # If no tool calls, try text detection (empty top_k = no trigger)
    latency_ms = result["latency_ms"]
    input_tokens = 0  # not tracked for real Claude runs
    return top_k, input_tokens, latency_ms


def claude_hook_top_k(query: str) -> tuple[list[str], int, float]:
    """Run real Claude Code with our hook injected via --settings."""
    from bench.claude_runner import run_claude_query_with_hook
    result = run_claude_query_with_hook(query)
    top_k = result["skill_calls"][:5]
    return top_k, 0, result["latency_ms"]


# ---------------- Aggregation + bootstrap CI ----------------

def _bootstrap_ci(
    corrects: list[int], n_samples: int = 1000, alpha: float = 0.05
) -> tuple[float, float]:
    if not corrects:
        return 0.0, 0.0
    rng = random.Random(42)
    n = len(corrects)
    means: list[float] = []
    for _ in range(n_samples):
        sample = [corrects[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    low = means[int(n_samples * alpha / 2)]
    high = means[int(n_samples * (1 - alpha / 2))]
    return low, high


def aggregate(traces: list[QueryTrace]) -> list[ConditionStats]:
    by_cond: dict[str, list[QueryTrace]] = {}
    for t in traces:
        by_cond.setdefault(t.condition, []).append(t)
    out: list[ConditionStats] = []
    for cond, ts in by_cond.items():
        n = len(ts)
        corrects = [1 if t.correct else 0 for t in ts]
        recall5 = sum(1 for t in ts if t.gold in [s.lower() for s in t.top_k]) / n
        toks = [t.input_tokens for t in ts]
        lats = sorted(t.latency_ms for t in ts)
        p95 = lats[int(0.95 * (n - 1))] if n > 0 else 0.0
        acc = sum(corrects) / n
        lo, hi = _bootstrap_ci(corrects)
        out.append(
            ConditionStats(
                condition=cond,
                n=n,
                trigger_accuracy=acc,
                trigger_accuracy_ci_low=lo,
                trigger_accuracy_ci_high=hi,
                recall_at_5=recall5,
                mean_input_tokens=sum(toks) / n,
                p95_latency_ms=p95,
            )
        )
    return out


# ---------------- Public CLI entry ----------------

def run_bench(
    eval_path: Path,
    out_dir: Path,
    conditions: Iterable[str],
    use_real_router: bool = True,
) -> None:
    skills = scan_skills()
    llm = get_llm_client()
    vec_index: _VectorIndex | None = None
    router = None

    out_dir.mkdir(parents=True, exist_ok=True)

    eval_entries = [
        json.loads(line)
        for line in eval_path.read_text().splitlines()
        if line.strip()
    ]
    traces: list[QueryTrace] = []

    for cond in conditions:
        if cond == "vector" and vec_index is None:
            try:
                vec_index = _VectorIndex(skills)
            except Exception as exc:
                logger.warning("Vector index unavailable (%s); skipping condition", exc)
                continue
        if cond == "sog" and router is None and use_real_router:
            from skillogy.core.router import Router

            router = Router(llm=llm)

        if cond in ("claude_native", "claude_hook"):
            # Check binary exists (FileNotFoundError fast-fails; don't run full query)
            import shutil
            from bench.claude_runner import CLAUDE_BIN
            if not shutil.which(CLAUDE_BIN):
                logger.warning("claude binary not found; skipping %s condition", cond)
                continue

        n_entries = len(eval_entries)
        # Both claude_* conditions run full Claude Code CLI — rate-limit to avoid overload
        _WORKERS = {"claude_hook": 3, "claude_native": 5}
        workers = _WORKERS.get(cond, 1)
        print(f"\n[{cond}] Starting {n_entries} queries (workers={workers})...", flush=True)

        counter_lock = threading.Lock()
        counter = [0]

        def _run_entry(entry: dict) -> QueryTrace | None:
            qid = entry["id"]
            q = entry["query"]
            gold = entry["gold_skill_name"].lower()
            q_short = q[:55] + ("…" if len(q) > 55 else "")
            print(f"  [{cond}] → starting {qid}  q={q_short}", flush=True)
            if cond == "native":
                top_k, toks, lat = native_top_k(q, skills, llm)
            elif cond == "vector":
                if vec_index is None:
                    return None
                top_k, toks, lat = vector_top_k(q, skills, llm, vec_index)
            elif cond == "sog":
                if router is None:
                    return None
                top_k, toks, lat = sog_top_k(q, router)
            elif cond == "claude_native":
                top_k, toks, lat = claude_native_top_k(q)
            elif cond == "claude_hook":
                top_k, toks, lat = claude_hook_top_k(q)
            else:
                return None
            picked = top_k[0] if top_k else ""
            correct = picked == gold
            with counter_lock:
                counter[0] += 1
                ei = counter[0]
            mark = "✓" if correct else "✗"
            q_short = q[:60] + ("…" if len(q) > 60 else "")
            picked_display = picked if picked else "(none)"
            print(
                f"  [{cond}] {ei:>3}/{n_entries} {mark} {lat/1000:>5.1f}s"
                f"  gold={gold:<28} picked={picked_display:<28}"
                f"  q={q_short}",
                flush=True,
            )
            return QueryTrace(
                id=qid,
                query=q,
                gold=gold,
                condition=cond,
                picked=picked,
                top_k=top_k,
                correct=correct,
                input_tokens=toks,
                latency_ms=lat,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_entry, e) for e in eval_entries]
            for fut in as_completed(futures):
                result = fut.result()
                if result is not None:
                    traces.append(result)

    # Persist traces
    (out_dir / "results.json").write_text(
        json.dumps(
            {"traces": [t.__dict__ for t in traces]}, ensure_ascii=False, indent=2
        )
    )

    stats = aggregate(traces)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps([s.__dict__ for s in stats], indent=2))

    # Print summary table
    print(
        f"{'Condition':<18} {'Acc':<8} {'CI95':<22} {'R@5':<8} {'Tokens':<10} {'p95 ms':<10}"
    )
    for s in stats:
        ci = f"[{s.trigger_accuracy_ci_low:.3f}, {s.trigger_accuracy_ci_high:.3f}]"
        print(
            f"{s.condition:<18} {s.trigger_accuracy:.3f}    {ci:<22} {s.recall_at_5:.3f}"
            f"    {int(s.mean_input_tokens):<10} {s.p95_latency_ms:<10.1f}"
        )
