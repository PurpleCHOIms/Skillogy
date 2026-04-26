"""Personal skill trigger eval set generator.

For each scanned SKILL.md, synthesize 2-3 natural user utterances that should
trigger that skill. Persist as JSONL with fields: id, query, gold_skill_name.

No MetaTool track — the previous version's API-selection benchmark was the
wrong domain for measuring SKILL trigger rate. This version evaluates ONLY
the user's local SKILL.md ecosystem.
"""

import json
import logging
import random
from pathlib import Path

from skill_router.infra.llm import LLMClient, get_llm_client
from skill_router.infra.scanner import ParsedSkill, scan_skills

logger = logging.getLogger(__name__)


_QUERY_SYNTHESIS_SYSTEM = """You generate realistic developer utterances. Given a Claude Code skill (its name and description), write 2 to 3 short user requests that a developer might type into Claude Code which SHOULD trigger this skill. Output STRICT JSON: a list of strings. Return ONLY the JSON list, no preamble or wrapping."""


def _normalize_skill_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _synthesize_queries(skill: ParsedSkill, llm: LLMClient | None) -> list[str]:
    """Use LLM to synthesize trigger-worthy user utterances. Falls back to using
    the description verbatim if no LLM is available."""
    if llm is None:
        return [skill.description.strip()] if skill.description.strip() else []
    user_prompt = f"Skill name: {skill.name}\nDescription: {skill.description}"
    try:
        raw = llm.complete(prompt=user_prompt, system=_QUERY_SYNTHESIS_SYSTEM, max_tokens=1024, temperature=0.0)
        data = json.loads(raw.strip())
        if isinstance(data, list):
            return [str(s).strip() for s in data if str(s).strip()][:3]
    except (json.JSONDecodeError, ValueError, TypeError, RuntimeError) as exc:
        logger.warning("LLM synthesis failed for %s (%s); falling back to description.", skill.name, exc)
    return [skill.description.strip()] if skill.description.strip() else []


def build_eval_set(
    out_path: Path,
    n_skills: int = 300,
    seed: int = 42,
    llm: LLMClient | None = None,
    skills: list[ParsedSkill] | None = None,
) -> dict[str, int]:
    """Stratified-sample n_skills from local SKILL.md, synthesize 2-3 queries each.

    Returns counts dict: {"skills_sampled": N, "queries_written": M, "unique_gold": K}.
    Diversity guard: raises RuntimeError if unique_gold < 100 (configurable cap removed
    if n_skills < 100; the assertion only fires when n_skills>=100).
    """
    rng = random.Random(seed)

    if skills is None:
        skills = scan_skills()
    if not skills:
        raise RuntimeError("No SKILL.md found by scan_skills() — cannot build eval set.")

    if llm is None:
        try:
            llm = get_llm_client()
        except RuntimeError as exc:
            logger.warning("No LLM available (%s); using description-fallback for all entries.", exc)
            llm = None

    # Stratify by source root prefix (best-effort — split by 3 buckets: user, plugin, project)
    def _bucket(s: ParsedSkill) -> str:
        p = str(s.source_path)
        if "/.claude/plugins/" in p:
            return "plugin"
        if "/.claude/skills/" in p:
            return "user"
        return "project"

    by_bucket: dict[str, list[ParsedSkill]] = {}
    for s in skills:
        by_bucket.setdefault(_bucket(s), []).append(s)

    # Round-robin sample n_skills across buckets to maximize diversity
    selected: list[ParsedSkill] = []
    bucket_keys = sorted(by_bucket.keys())
    for k in bucket_keys:
        rng.shuffle(by_bucket[k])
    while len(selected) < n_skills and any(by_bucket[k] for k in bucket_keys):
        for k in bucket_keys:
            if not by_bucket[k]:
                continue
            selected.append(by_bucket[k].pop())
            if len(selected) >= n_skills:
                break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    queries_written = 0
    unique_gold: set[str] = set()
    with out_path.open("w", encoding="utf-8") as f:
        for si, skill in enumerate(selected, 1):
            print(f"  [{si}/{len(selected)}] {skill.name}", flush=True)
            for idx, q in enumerate(_synthesize_queries(skill, llm)):
                gold = _normalize_skill_name(skill.name)
                entry = {"id": f"{gold}-{idx}", "query": q, "gold_skill_name": gold}
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                queries_written += 1
                unique_gold.add(gold)

    counts = {
        "skills_sampled": len(selected),
        "queries_written": queries_written,
        "unique_gold": len(unique_gold),
    }
    if n_skills >= 100 and counts["unique_gold"] < 100:
        raise RuntimeError(f"Diversity guard: only {counts['unique_gold']} unique gold names (< 100).")
    return counts
