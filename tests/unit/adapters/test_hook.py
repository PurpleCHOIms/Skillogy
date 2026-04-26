"""Unit tests for skillogy.adapters.hook (UserPromptSubmit hook entry)."""

from __future__ import annotations

import io
import json
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASSTHROUGH_BODY = {
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "",
    }
}


def _make_router_result(*, skill_name="test-skill", skill_body="# Test\nBody content", score=3.0):
    """Return a canned RoutingResult-like object."""
    from skillogy.core.router import RoutingResult
    return RoutingResult(
        skill_name=skill_name,
        skill_body=skill_body,
        reasoning_path=[],
        alternatives=[],
        score=score,
    )


def _run_main(monkeypatch, stdin_text: str, env: dict | None = None) -> dict:
    """Run hook.main() with patched stdin, return parsed stdout JSON."""
    import skillogy.adapters.hook as hook_mod

    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))

    env = env or {}
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    return hook_mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_passthrough_when_disabled(monkeypatch, capsys):
    """SKILLOGY_DISABLE=1 must emit passthrough and return 0."""
    import skillogy.adapters.hook as hook_mod

    monkeypatch.setenv("SKILLOGY_DISABLE", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO('{"prompt": "some prompt"}'))

    rc = hook_mod.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == PASSTHROUGH_BODY


def test_passthrough_when_no_prompt(monkeypatch, capsys):
    """Stdin with empty prompt must emit passthrough."""
    import skillogy.adapters.hook as hook_mod

    monkeypatch.delenv("SKILLOGY_DISABLE", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"prompt": "   "}'))

    mock_router_cls = MagicMock()
    with patch("skillogy.core.router.Router", mock_router_cls):
        rc = hook_mod.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == PASSTHROUGH_BODY
    # Router must not have been called
    mock_router_cls.assert_not_called()


def test_injection_with_match(monkeypatch, capsys):
    """High-score match must inject additionalContext with skill name and body."""
    import skillogy.adapters.hook as hook_mod

    monkeypatch.delenv("SKILLOGY_DISABLE", raising=False)
    monkeypatch.delenv("SKILLOGY_MIN_SCORE", raising=False)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"prompt": "TypeScript build is broken"}'),
    )

    canned = _make_router_result(skill_name="ts-build-fix", skill_body="# TS Build Fix\nRun tsc.", score=3.5)
    mock_router_instance = MagicMock()
    mock_router_instance.find_skill.return_value = canned
    mock_router_cls = MagicMock(return_value=mock_router_instance)

    with patch("skillogy.core.router.Router", mock_router_cls):
        rc = hook_mod.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    hook_out = out["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "UserPromptSubmit"
    ctx = hook_out["additionalContext"]
    assert "ts-build-fix" in ctx
    assert "3.50" in ctx
    assert "# TS Build Fix" in ctx
    assert "---SKILL.md---" in ctx
    assert "---END SKILL.md---" in ctx


def test_passthrough_when_score_below_threshold(monkeypatch, capsys):
    """Score below SKILLOGY_MIN_SCORE must emit passthrough."""
    import skillogy.adapters.hook as hook_mod

    monkeypatch.delenv("SKILLOGY_DISABLE", raising=False)
    monkeypatch.setenv("SKILLOGY_MIN_SCORE", "5.0")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"prompt": "fix my code"}'),
    )

    canned = _make_router_result(score=2.0)  # below threshold of 5.0
    mock_router_instance = MagicMock()
    mock_router_instance.find_skill.return_value = canned
    mock_router_cls = MagicMock(return_value=mock_router_instance)

    with patch("skillogy.core.router.Router", mock_router_cls):
        rc = hook_mod.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == PASSTHROUGH_BODY


def test_passthrough_on_router_exception(monkeypatch, capsys):
    """Router raising must emit passthrough and not crash (exit 0)."""
    import skillogy.adapters.hook as hook_mod

    monkeypatch.delenv("SKILLOGY_DISABLE", raising=False)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"prompt": "do something"}'),
    )

    mock_router_instance = MagicMock()
    mock_router_instance.find_skill.side_effect = RuntimeError("neo4j is down")
    mock_router_cls = MagicMock(return_value=mock_router_instance)

    with patch("skillogy.core.router.Router", mock_router_cls):
        rc = hook_mod.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == PASSTHROUGH_BODY


def test_passthrough_on_invalid_json(monkeypatch, capsys):
    """Malformed stdin JSON must emit passthrough without crashing."""
    import skillogy.adapters.hook as hook_mod

    monkeypatch.delenv("SKILLOGY_DISABLE", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("this is not json {{{"))

    rc = hook_mod.main()

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == PASSTHROUGH_BODY


def test_latency_smoke(monkeypatch, capsys):
    """End-to-end main() with a mocked instant router must complete in < 100 ms."""
    import skillogy.adapters.hook as hook_mod

    monkeypatch.delenv("SKILLOGY_DISABLE", raising=False)
    monkeypatch.delenv("SKILLOGY_MIN_SCORE", raising=False)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"prompt": "run the build pipeline"}'),
    )

    canned = _make_router_result(score=2.0)
    mock_router_instance = MagicMock()
    mock_router_instance.find_skill.return_value = canned
    mock_router_cls = MagicMock(return_value=mock_router_instance)

    with patch("skillogy.core.router.Router", mock_router_cls):
        start = time.perf_counter()
        rc = hook_mod.main()
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert rc == 0
    assert elapsed_ms < 100, f"main() took {elapsed_ms:.1f} ms, expected < 100 ms"
