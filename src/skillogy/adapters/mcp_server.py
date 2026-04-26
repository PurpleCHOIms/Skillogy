"""MCP server exposing the SOG router as cross-agent tools (stdio transport)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from skillogy.core.router import Router
from skillogy.infra.scanner import scan_skills

logger = logging.getLogger(__name__)

server = Server("skillogy")

_router_singleton: Router | None = None
_skills_cache: list | None = None


def _get_router() -> Router:
    global _router_singleton
    if _router_singleton is None:
        _router_singleton = Router()
    return _router_singleton


def _get_skills() -> list:
    global _skills_cache
    if _skills_cache is None:
        _skills_cache = scan_skills()
    return _skills_cache


@server.list_tools()
async def list_tools_handler() -> list[types.Tool]:
    return [
        types.Tool(
            name="find_skill",
            description="Find the most relevant Claude Code SKILL.md for a given query using the GraphRAG router.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of what the agent is trying to do.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of candidate skills to consider (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list_skills",
            description="List all scanned SKILL.md files, optionally filtered by name substring.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Case-insensitive substring to filter skill names (empty = return all).",
                        "default": "",
                    },
                },
            },
        ),
        types.Tool(
            name="get_skill",
            description="Get the full SKILL.md body for a specific skill by name. Use after find_skill to load skill content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact skill name as returned by find_skill or list_skills.",
                    },
                },
                "required": ["name"],
            },
        ),
    ]


@server.call_tool()
async def call_tool_handler(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name == "find_skill":
        return await _handle_find_skill(arguments)
    elif name == "list_skills":
        return await _handle_list_skills(arguments)
    elif name == "get_skill":
        return await _handle_get_skill(arguments)
    else:
        raise ValueError(f"Unknown tool: {name!r}")


async def _handle_find_skill(arguments: dict[str, Any]) -> list[types.TextContent]:
    import json

    query: str = arguments["query"]
    top_k: int = int(arguments.get("top_k", 5))

    try:
        router = _get_router()
        result = router.find_skill(query=query, top_k=top_k, judge=True, load_body=False)
        payload = {
            "skill_name": result.skill_name,
            "score": result.score,
            "alternatives": result.alternatives,
            "instructions": (
                f'Load the skill with: Skill({{ skill: "{result.skill_name}" }}) '
                f'or call get_skill tool with name="{result.skill_name}" for the full body.'
            ) if result.skill_name else "",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("find_skill failed: %s", exc)
        payload = {
            "error": str(exc),
            "skill_name": "",
            "score": 0.0,
            "alternatives": [],
            "instructions": "",
        }

    return [types.TextContent(type="text", text=json.dumps(payload))]


async def _handle_list_skills(arguments: dict[str, Any]) -> list[types.TextContent]:
    import json

    filter_str: str = arguments.get("filter", "") or ""
    f = filter_str.lower()

    skills = _get_skills()
    result = [
        {"name": s.name, "description": s.description}
        for s in skills
        if not f or f in s.name.lower()
    ]

    return [types.TextContent(type="text", text=json.dumps(result))]


async def _handle_get_skill(arguments: dict[str, Any]) -> list[types.TextContent]:
    import json
    from pathlib import Path

    skill_name: str = arguments["name"]
    skills = _get_skills()
    match = next((s for s in skills if s.name == skill_name), None)
    if match is None:
        return [types.TextContent(type="text", text=json.dumps({"error": f"Skill '{skill_name}' not found"}))]
    try:
        body = Path(match.source_path).read_text(encoding="utf-8")
    except OSError as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]
    return [types.TextContent(type="text", text=json.dumps({"name": skill_name, "body": body}))]


async def _run() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="skillogy",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
