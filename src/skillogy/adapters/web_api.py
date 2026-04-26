"""FastAPI web API for skillogy — US-008/US-009."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Skill Router API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Module-level cache: populated lazily on first request
_skills_cache: list[Any] | None = None


def _get_skills() -> list[Any]:
    """Return cached skills list, scanning on first call."""
    global _skills_cache  # noqa: PLW0603
    if _skills_cache is None:
        _skills_cache = _load_skills()
    return _skills_cache


def _load_skills() -> list[Any]:
    """Scan skills and dedup by name (project > user > plugin priority)."""
    try:
        from skillogy.infra.scanner import scan_skills  # noqa: PLC0415

        skills = scan_skills()
        _scope_rank = {"project": 0, "user": 1, "plugin": 2}
        best: dict[str, Any] = {}
        for s in skills:
            scope = getattr(s, "scope", "user")
            rank = _scope_rank.get(scope, 99)
            existing = best.get(s.name)
            if existing is None:
                best[s.name] = s
            else:
                ex_rank = _scope_rank.get(getattr(existing, "scope", "user"), 99)
                if rank < ex_rank or (rank == ex_rank and len(str(s.source_path)) < len(str(existing.source_path))):
                    best[s.name] = s
        unique = list(best.values())
        logger.info("Loaded %d skills into cache (from %d scanned)", len(unique), len(skills))
        return unique
    except Exception as exc:  # noqa: BLE001
        logger.warning("skillogy.scanner unavailable (%s); serving empty list", exc)
        return []


def _extra_roots() -> list[str]:
    """Return resolved extra project roots from SKILLOGY_EXTRA_ROOTS env var."""
    import os  # noqa: PLC0415
    roots = []
    for raw in os.environ.get("SKILLOGY_EXTRA_ROOTS", "").split(":"):
        raw = raw.strip()
        if raw:
            p = Path(raw).expanduser()
            roots.append(str(p.resolve()))
    return roots


def _project_root_for(source_path: Any) -> str | None:
    """Return the project root dir for a project-scope skill.

    Handles both .claude/skills project layout and SKILLOGY_EXTRA_ROOTS paths.
    """
    path = Path(source_path)
    abs_str = str(path.expanduser().absolute())

    # Extra roots: the root itself is the project container
    for root in _extra_roots():
        if abs_str.startswith(root):
            return root

    # Standard .claude/skills layout — resolve symlinks to find true location
    resolved = str(path.resolve())
    idx = resolved.find("/.claude/skills")
    if idx == -1:
        return None
    root = resolved[:idx]
    if root == str(Path.home().resolve()):
        return None
    return root


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _project_name(root: str) -> str:
    """Human-friendly name for a project root path."""
    p = Path(root)
    # Extra roots often end in 'skills' — use parent dir name for readability
    if p.name in ("skills", "skill", ".claude"):
        return p.parent.name
    return p.name


@app.get("/api/projects")
def list_projects() -> list[dict]:
    """Return all auto-discovered projects that have project-scope skills."""
    projects: dict[str, dict] = {}
    for s in _get_skills():
        if getattr(s, "scope", "user") != "project":
            continue
        root = _project_root_for(s.source_path)
        if not root:
            continue
        if root not in projects:
            projects[root] = {
                "path": root,
                "name": _project_name(root),
                "skill_count": 0,
            }
        projects[root]["skill_count"] += 1
    return list(projects.values())


@app.get("/api/skills")
def list_skills(
    scope: str | None = Query(default=None),
    project: str | None = Query(default=None),
) -> list[dict]:
    """Return skills as [{name, description, scope, project_path}].

    Filters: ?scope=project|user|plugin|all  and/or  ?project=<project_root_path>
    """
    result = []
    for s in _get_skills():
        skill_scope = getattr(s, "scope", "user")
        if scope and scope != "all" and skill_scope != scope:
            continue
        proj_root = _project_root_for(s.source_path) if skill_scope == "project" else None
        if project and proj_root != project:
            continue
        result.append({
            "name": s.name,
            "description": s.description,
            "scope": skill_scope,
            "project_path": proj_root,
        })
    return result


@app.get("/api/scopes")
def get_scopes() -> dict:
    """Return counts by scope: {project: N, user: N, plugin: N, total: N}."""
    counts: dict[str, int] = {"project": 0, "user": 0, "plugin": 0, "total": 0}
    for s in _get_skills():
        skill_scope = getattr(s, "scope", "user")
        if skill_scope in counts:
            counts[skill_scope] += 1
        counts["total"] += 1
    return counts


@app.get("/api/skills/{name:path}")
def get_skill(name: str) -> dict:
    """Return full skill detail; 404 if not found. Returns first match by name."""
    skill = next((s for s in _get_skills() if s.name == name), None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    skill_scope = getattr(skill, "scope", "user")
    return {
        "name": skill.name,
        "description": skill.description,
        "body": skill.body,
        "source_path": str(skill.source_path),
        "scope": skill_scope,
        "project_path": _project_root_for(skill.source_path) if skill_scope == "project" else None,
        "raw_frontmatter": skill.raw_frontmatter,
    }


@app.get("/api/graph")
def get_graph() -> dict:
    """Return graph nodes and edges; falls back gracefully if DB unavailable."""
    try:
        from skillogy.core.graph import export_graph_json  # noqa: PLC0415

        return export_graph_json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query graph store: %s", exc)
        return {"nodes": [], "edges": [], "warning": "graph store not available"}


def _get_graph_data() -> dict:
    """Return graph data from Neo4j, falling back to empty on any exception."""
    try:
        from skillogy.core.graph import export_graph_json  # noqa: PLC0415

        return export_graph_json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Graph data unavailable: %s", exc)
        return {"nodes": [], "edges": []}


@app.get("/api/graph/stream")
async def stream_graph() -> StreamingResponse:
    """SSE endpoint that streams incremental graph updates as Neo4j is populated."""

    async def event_generator():
        seen_node_ids: set[str] = set()
        seen_edge_ids: set[str] = set()
        first = True

        while True:
            try:
                data = await asyncio.to_thread(_get_graph_data)
                nodes: list[dict] = data.get("nodes", [])
                edges: list[dict] = data.get("edges", [])

                if first:
                    # Send full snapshot on first connection
                    seen_node_ids = {n["id"] for n in nodes}
                    seen_edge_ids = {f"{e['src']}__{e['etype']}__{e['dst']}" for e in edges}
                    payload = json.dumps({"nodes": nodes, "edges": edges})
                    yield f"data: {payload}\n\n"
                    first = False
                else:
                    new_nodes = [n for n in nodes if n["id"] not in seen_node_ids]
                    new_edges = [
                        e for e in edges
                        if f"{e['src']}__{e['etype']}__{e['dst']}" not in seen_edge_ids
                    ]

                    if new_nodes or new_edges:
                        for n in new_nodes:
                            seen_node_ids.add(n["id"])
                        for e in new_edges:
                            seen_edge_ids.add(f"{e['src']}__{e['etype']}__{e['dst']}")
                        payload = json.dumps({"nodes": new_nodes, "edges": new_edges})
                        yield f"data: {payload}\n\n"
                    else:
                        yield ": heartbeat\n\n"

            except Exception as exc:  # noqa: BLE001
                logger.warning("SSE graph stream error: %s", exc)
                yield ": heartbeat\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
