"""Unit tests for skillogy.adapters.mcp_server.

Tests invoke the internal async handler functions directly, mocking Router and
scan_skills so no Neo4j or LLM is needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from skillogy.core.router import RoutingResult
from skillogy.domain.types import ParsedSkill
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_routing_result(**kwargs) -> RoutingResult:
    defaults: dict[str, Any] = {
        "skill_name": "test-skill",
        "skill_body": "# Test Skill\nDoes something useful.",
        "reasoning_path": [("test-skill", "triggered_by", "intent::fix")],
        "alternatives": [{"name": "other-skill", "score": 1.5}],
        "score": 3.0,
    }
    defaults.update(kwargs)
    return RoutingResult(**defaults)


def _make_parsed_skill(name: str, description: str = "") -> ParsedSkill:
    return ParsedSkill(
        name=name,
        description=description or f"Description for {name}",
        body="",
        source_path=Path(f"/fake/{name}/SKILL.md"),
        raw_frontmatter={},
    )


# ---------------------------------------------------------------------------
# Grab the handler functions from the module after import
# ---------------------------------------------------------------------------

import skillogy.adapters.mcp_server as mcp_mod


# ---------------------------------------------------------------------------
# 1. test_find_skill_tool_registered
# ---------------------------------------------------------------------------

def test_find_skill_tool_registered():
    """find_skill handler returns correct response shape with a mocked Router."""
    mock_result = _make_routing_result()
    mock_router = MagicMock()
    mock_router.find_skill.return_value = mock_result

    with patch.object(mcp_mod, "_get_router", return_value=mock_router):
        raw = asyncio.run(mcp_mod._handle_find_skill({"query": "fix a bug", "top_k": 3}))

    assert len(raw) == 1
    payload = json.loads(raw[0].text)

    assert payload["skill_name"] == "test-skill"
    assert payload["skill_body"] == "# Test Skill\nDoes something useful."
    assert payload["reasoning_path"] == [["test-skill", "triggered_by", "intent::fix"]]
    assert payload["alternatives"] == [{"name": "other-skill", "score": 1.5}]
    assert payload["score"] == pytest.approx(3.0)

    mock_router.find_skill.assert_called_once_with(query="fix a bug", top_k=3, judge=True)


# ---------------------------------------------------------------------------
# 2. test_list_skills_filter
# ---------------------------------------------------------------------------

def test_list_skills_filter():
    """list_skills with a filter returns only matching skills (case-insensitive)."""
    skills = [
        _make_parsed_skill("typescript-lint"),
        _make_parsed_skill("python-formatter"),
        _make_parsed_skill("ts-migrate"),
    ]

    with patch.object(mcp_mod, "scan_skills", return_value=skills):
        raw = asyncio.run(mcp_mod._handle_list_skills({"filter": "lint"}))

    payload = json.loads(raw[0].text)
    names = [item["name"] for item in payload]
    assert "typescript-lint" in names
    assert "ts-migrate" not in names
    assert "python-formatter" not in names


# ---------------------------------------------------------------------------
# 3. test_list_skills_no_filter
# ---------------------------------------------------------------------------

def test_list_skills_no_filter():
    """list_skills with no filter returns all skills."""
    skills = [
        _make_parsed_skill("skill-a"),
        _make_parsed_skill("skill-b"),
        _make_parsed_skill("skill-c"),
    ]

    with patch.object(mcp_mod, "scan_skills", return_value=skills):
        raw = asyncio.run(mcp_mod._handle_list_skills({}))

    payload = json.loads(raw[0].text)
    assert len(payload) == 3
    names = {item["name"] for item in payload}
    assert names == {"skill-a", "skill-b", "skill-c"}


# ---------------------------------------------------------------------------
# 4. test_find_skill_handles_router_exception
# ---------------------------------------------------------------------------

def test_find_skill_handles_router_exception():
    """When Router.find_skill raises, handler returns a controlled error structure."""
    mock_router = MagicMock()
    mock_router.find_skill.side_effect = RuntimeError("Neo4j unreachable")

    with patch.object(mcp_mod, "_get_router", return_value=mock_router):
        raw = asyncio.run(mcp_mod._handle_find_skill({"query": "deploy service"}))

    payload = json.loads(raw[0].text)
    assert "error" in payload
    assert "Neo4j unreachable" in payload["error"]
    assert payload["skill_name"] == ""
    assert payload["skill_body"] == ""
    assert payload["reasoning_path"] == []
    assert payload["alternatives"] == []
    assert payload["score"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 5. Integration: subprocess smoke test (skipped by default)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("MCP_INTEGRATION") and not os.environ.get("NEO4J_INTEGRATION"),
    reason="Set MCP_INTEGRATION=1 or NEO4J_INTEGRATION=1 to run MCP integration tests",
)
def test_mcp_server_subprocess_lists_tools():
    """Spin a real MCP server subprocess and verify it responds to initialize."""
    import threading

    proc = subprocess.Popen(
        [sys.executable, "-m", "skillogy.adapters.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Send a JSON-RPC initialize request over stdin
    init_request = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1"},
        },
    }) + "\n"

    try:
        stdout, stderr = proc.communicate(input=init_request.encode(), timeout=5)
        # Server should have written at least one JSON-RPC response line
        lines = [l for l in stdout.decode().splitlines() if l.strip()]
        assert lines, f"No output from server. stderr: {stderr.decode()}"
        response = json.loads(lines[0])
        assert response.get("id") == 1
        assert "result" in response
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("MCP server subprocess timed out")
    finally:
        proc.stdout.close() if proc.stdout else None
        proc.stdin.close() if proc.stdin else None
        proc.wait()
