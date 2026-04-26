"""python -m skillogy index [--limit N] [--workers N] [--roots PATH ...]

Runs scanner -> extractor -> init_schema -> build_graph(clear_first=True) -> enrich_with_parsed.
LLM extraction runs in parallel (default 5 workers) since calls are I/O-bound.
Prints summary.
"""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def cmd_index(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO)

    from skillogy.infra.llm import get_llm_client  # noqa: PLC0415
    from skillogy.infra.db import close_driver, get_driver  # noqa: PLC0415
    from skillogy.core.extractor import extract  # noqa: PLC0415
    from skillogy.core.graph import build_graph, enrich_with_parsed, init_schema  # noqa: PLC0415
    from skillogy.infra.scanner import scan_skills  # noqa: PLC0415

    roots = [Path(r) for r in args.roots] if args.roots else None
    parsed_skills = scan_skills(roots=roots)
    if args.limit:
        parsed_skills = parsed_skills[: args.limit]
    print(f"Scanned {len(parsed_skills)} skills")

    llm = get_llm_client()
    workers = args.workers
    extracted = []
    done = 0

    def _extract_one(p):  # type: ignore[no-untyped-def]
        return extract(p, llm)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_extract_one, p): p for p in parsed_skills}
        for fut in as_completed(futures):
            p = futures[fut]
            done += 1
            try:
                extracted.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                print(f"  skipped {p.name}: {exc}")
            if done % 5 == 0 or done == len(parsed_skills):
                print(f"  extracted {done}/{len(parsed_skills)}")

    driver = get_driver()
    try:
        init_schema(driver)
        summary = build_graph(extracted, driver=driver, clear_first=True)
        parsed_lookup = {p.name: p for p in parsed_skills}
        enriched = enrich_with_parsed(parsed_lookup, driver=driver)
        # Round 12 schema dropped 'capabilities' bucket; Round 13 adds 'related_to'.
        # Use defensive .get() so this works regardless of which keys the builder returns.
        parts = [f"{k}={v}" for k, v in summary.items()]
        print("Graph built: " + ", ".join(parts))
        print(f"Enriched {enriched} skill nodes with metadata.")
    finally:
        close_driver()


def main() -> None:
    ap = argparse.ArgumentParser(prog="skillogy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--workers", type=int, default=5, metavar="N", help="Parallel LLM extraction workers (default 5)")
    p.add_argument("--roots", nargs="+", metavar="PATH", help="Scan only these root dirs (bypasses default_roots)")
    p.set_defaults(func=cmd_index)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
