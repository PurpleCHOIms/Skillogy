"""python -m bench eval-set --out bench/data/eval.jsonl --n-skills 300 --seed 42
python -m bench run --eval bench/data/eval.jsonl --out-dir bench/results --conditions all
python -m bench chart --summary bench/results/summary.json --out-dir bench/results
"""
import argparse
from pathlib import Path

from .eval_set import build_eval_set


_ALL_CONDITIONS = ["native", "vector", "sog", "claude_native", "claude_hook"]


def main() -> None:
    ap = argparse.ArgumentParser(prog="bench")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # eval-set subcommand (existing)
    p_eval = sub.add_parser("eval-set")
    p_eval.add_argument("--out", default="bench/data/eval.jsonl")
    p_eval.add_argument("--n-skills", type=int, default=300)
    p_eval.add_argument("--seed", type=int, default=42)
    p_eval.add_argument("--roots", nargs="+", metavar="PATH", help="Scan only these roots for eval set generation")

    # run subcommand
    p_run = sub.add_parser("run")
    p_run.add_argument("--eval", required=True, help="Path to eval JSONL file")
    p_run.add_argument("--out-dir", default="bench/results", help="Output directory")
    p_run.add_argument(
        "--conditions",
        default="all",
        help='Comma-separated conditions or "all". Choices: native,vector,sog',
    )
    p_run.add_argument(
        "--no-real-router",
        action="store_true",
        help="Skip live Neo4j router (SOG condition will be skipped)",
    )

    # chart subcommand
    p_chart = sub.add_parser("chart")
    p_chart.add_argument("--summary", required=True, help="Path to summary.json")
    p_chart.add_argument("--out-dir", required=True, help="Directory to write PNGs into")

    args = ap.parse_args()

    if args.cmd == "eval-set":
        skills = None
        if getattr(args, "roots", None):
            from skillogy.infra.scanner import scan_skills
            skills = scan_skills(roots=[Path(r) for r in args.roots])
        counts = build_eval_set(Path(args.out), n_skills=args.n_skills, seed=args.seed, skills=skills)
        print(counts)

    elif args.cmd == "run":
        from .runner import run_bench

        if args.conditions.strip().lower() == "all":
            conditions = _ALL_CONDITIONS
        else:
            conditions = [c.strip().lower() for c in args.conditions.split(",") if c.strip()]

        run_bench(
            eval_path=Path(args.eval),
            out_dir=Path(args.out_dir),
            conditions=conditions,
            use_real_router=not args.no_real_router,
        )

    elif args.cmd == "chart":
        from .chart import make_charts

        make_charts(Path(args.summary), Path(args.out_dir))
        print(f"Charts written to {args.out_dir}")


if __name__ == "__main__":
    main()
