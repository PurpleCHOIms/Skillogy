"""Benchmark chart generator — produces three matplotlib PNGs from summary.json."""

from __future__ import annotations

import json
from pathlib import Path


def make_charts(summary_path: Path, out_dir: Path) -> None:
    """Generate three PNG charts from a summary.json produced by run_bench().

    Charts created:
    - chart-trigger-accuracy.png  (HERO: grouped bar with CI error bars)
    - chart-tokens.png            (mean_input_tokens per condition)
    - chart-latency.png           (p95_latency_ms per condition)
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)

    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    conditions = [s["condition"] for s in raw]
    accuracies = [s["trigger_accuracy"] for s in raw]
    ci_lows = [s["trigger_accuracy_ci_low"] for s in raw]
    ci_highs = [s["trigger_accuracy_ci_high"] for s in raw]
    tokens = [s["mean_input_tokens"] for s in raw]
    latencies = [s["p95_latency_ms"] for s in raw]

    # Error bar deltas (symmetric from point, but CI may be asymmetric so use per-side)
    yerr_low = [acc - lo for acc, lo in zip(accuracies, ci_lows)]
    yerr_high = [hi - acc for acc, hi in zip(accuracies, ci_highs)]

    x = list(range(len(conditions)))
    colors = ["#4C72B0", "#DD8452", "#55A868"]

    # ---- HERO: trigger accuracy with CI ----
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(x, accuracies, color=colors[: len(conditions)], width=0.5, zorder=2)
    ax.errorbar(
        x,
        accuracies,
        yerr=[yerr_low, yerr_high],
        fmt="none",
        color="black",
        capsize=6,
        linewidth=1.5,
        zorder=3,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(conditions, fontsize=12)
    ax.set_ylabel("Trigger Accuracy", fontsize=12)
    ax.set_title("Skill Trigger Rate — Accuracy by Condition (with 95% CI)", fontsize=13)
    ax.set_ylim(0, 1.05)
    ax.yaxis.grid(True, linestyle="--", alpha=0.7, zorder=1)
    ax.set_axisbelow(True)
    for bar, acc in zip(bars, accuracies):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            acc + 0.02,
            f"{acc:.3f}",
            ha="center",
            va="bottom",
            fontsize=10,
        )
    fig.tight_layout()
    fig.savefig(out_dir / "chart-trigger-accuracy.png", dpi=150)
    plt.close(fig)

    # ---- tokens ----
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(x, tokens, color=colors[: len(conditions)], width=0.5, zorder=2)
    ax.set_xticks(x)
    ax.set_xticklabels(conditions, fontsize=12)
    ax.set_ylabel("Mean Input Tokens", fontsize=12)
    ax.set_title("Mean Input Tokens per Condition", fontsize=13)
    ax.yaxis.grid(True, linestyle="--", alpha=0.7, zorder=1)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out_dir / "chart-tokens.png", dpi=150)
    plt.close(fig)

    # ---- p95 latency ----
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(x, latencies, color=colors[: len(conditions)], width=0.5, zorder=2)
    ax.set_xticks(x)
    ax.set_xticklabels(conditions, fontsize=12)
    ax.set_ylabel("p95 Latency (ms)", fontsize=12)
    ax.set_title("p95 Latency per Condition", fontsize=13)
    ax.yaxis.grid(True, linestyle="--", alpha=0.7, zorder=1)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out_dir / "chart-latency.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="bench.chart")
    ap.add_argument("--summary", required=True, help="Path to summary.json")
    ap.add_argument("--out-dir", required=True, help="Directory to write PNGs into")
    args = ap.parse_args()
    make_charts(Path(args.summary), Path(args.out_dir))
    print(f"Charts written to {args.out_dir}")
