"""Tests for bench.chart — verifies PNG files are produced without real data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.chart import make_charts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_dummy_summary(path: Path) -> None:
    summary = [
        {
            "condition": "native",
            "n": 10,
            "trigger_accuracy": 0.70,
            "trigger_accuracy_ci_low": 0.60,
            "trigger_accuracy_ci_high": 0.80,
            "recall_at_5": 0.90,
            "mean_input_tokens": 500.0,
            "p95_latency_ms": 350.0,
        },
        {
            "condition": "vector",
            "n": 10,
            "trigger_accuracy": 0.75,
            "trigger_accuracy_ci_low": 0.65,
            "trigger_accuracy_ci_high": 0.85,
            "recall_at_5": 0.92,
            "mean_input_tokens": 420.0,
            "p95_latency_ms": 280.0,
        },
        {
            "condition": "sog",
            "n": 10,
            "trigger_accuracy": 0.85,
            "trigger_accuracy_ci_low": 0.77,
            "trigger_accuracy_ci_high": 0.93,
            "recall_at_5": 0.97,
            "mean_input_tokens": 800.0,
            "p95_latency_ms": 450.0,
        },
    ]
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. test_make_charts_creates_pngs
# ---------------------------------------------------------------------------

def test_make_charts_creates_pngs(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    _write_dummy_summary(summary_path)

    make_charts(summary_path, tmp_path)

    expected_files = [
        "chart-trigger-accuracy.png",
        "chart-tokens.png",
        "chart-latency.png",
    ]
    for fname in expected_files:
        fpath = tmp_path / fname
        assert fpath.exists(), f"Expected {fname} to be created"
        assert fpath.stat().st_size > 0, f"Expected {fname} to be non-empty"


# ---------------------------------------------------------------------------
# 2. test_make_charts_creates_output_dir
# ---------------------------------------------------------------------------

def test_make_charts_creates_output_dir(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    _write_dummy_summary(summary_path)

    out_dir = tmp_path / "nested" / "charts"
    assert not out_dir.exists()

    make_charts(summary_path, out_dir)

    assert out_dir.exists()
    assert (out_dir / "chart-trigger-accuracy.png").exists()


# ---------------------------------------------------------------------------
# 3. test_make_charts_single_condition
# ---------------------------------------------------------------------------

def test_make_charts_single_condition(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    summary = [
        {
            "condition": "native",
            "n": 5,
            "trigger_accuracy": 0.60,
            "trigger_accuracy_ci_low": 0.40,
            "trigger_accuracy_ci_high": 0.80,
            "recall_at_5": 0.80,
            "mean_input_tokens": 300.0,
            "p95_latency_ms": 200.0,
        }
    ]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    make_charts(summary_path, tmp_path)

    # All three charts must be produced even for a single condition
    assert (tmp_path / "chart-trigger-accuracy.png").exists()
    assert (tmp_path / "chart-tokens.png").exists()
    assert (tmp_path / "chart-latency.png").exists()
