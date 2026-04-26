# Skill Router

Hackathon project: a skill routing system for Claude Code skills.

## Project layout

```
Hackathon/
├── pyproject.toml
├── src/
│   └── skill_router/
│       ├── __init__.py
│       ├── __main__.py          # python -m skill_router index
│       ├── domain/
│       │   ├── types.py         # ParsedSkill, ExtractedSkill, Signal dataclasses
│       │   └── graph_schema.py  # Node label + relationship constants
│       ├── infra/
│       │   ├── db.py            # Neo4j driver singleton
│       │   ├── scanner.py       # SKILL.md discovery
│       │   └── llm.py           # LLM client (claude-agent-sdk or anthropic SDK)
│       ├── core/
│       │   ├── extractor.py     # ParsedSkill -> ExtractedSkill via LLM
│       │   ├── graph.py         # Neo4j graph build/enrich/export
│       │   └── router.py        # GraphRAG routing engine
│       └── adapters/
│           └── web_api.py       # FastAPI app
├── bench/
│   ├── __main__.py              # python -m bench eval-set
│   ├── eval_set.py              # Eval JSONL generation (MetaTool + personal skills)
│   └── data/                   # Cached/generated benchmark data
├── tests/
│   ├── unit/
│   │   ├── infra/               # test_llm.py, test_scanner.py
│   │   ├── core/                # test_extractor.py, test_graph.py, test_router.py
│   │   └── bench/               # test_eval_set.py
│   └── integration/             # testcontainers-based tests (NEO4J_INTEGRATION=1)
├── web/                         # Vite + React frontend
└── scripts/                     # Hook scripts (US-007)
```

## Neo4j (graph DB backend)

Start Neo4j locally:

    docker compose up -d neo4j

Browser: http://localhost:7474 (neo4j / skillrouter)

Index your local SKILL.md ecosystem into the graph:

    .venv/bin/python -m skill_router index

Run integration tests (requires Docker):

    NEO4J_INTEGRATION=1 .venv/bin/pytest -m integration -v

## Benchmark

Generate the eval JSONL dataset:

    .venv/bin/python -m bench eval-set --out bench/data/eval.jsonl

## Web UI

Browse and search all installed skills via a local web interface.

### Backend (FastAPI)

```bash
.venv/bin/uvicorn skill_router.adapters.web_api:app --port 8765
```

### Frontend (Vite + React)

```bash
# separate terminal
cd web && npm run dev
# opens http://localhost:5173
```

### API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/skills` | List all skills `[{name, description}]` |
| `GET /api/skills/{name}` | Full skill detail (body, source_path, raw_frontmatter) |
| `GET /api/graph` | Skill dependency graph from Neo4j |

## MCP server (cross-agent surface)

Other MCP-aware agents (Cursor, Codex, Gemini CLI, etc.) can use the router as a tool.
For Claude Code, register it via:

    claude mcp add skill-router /home/catow/GIT/Hackathon/.venv/bin/python -m skill_router.adapters.mcp_server

The server exposes:
- `find_skill(query, top_k=5)` — returns the most relevant SKILL.md body for a query
- `list_skills(filter="")` — lists all scanned skills (debugging / discovery)

Requires Neo4j running and an indexed graph.

## Install the Claude Code hook

Add this to `~/.claude/settings.json` to activate strict-trigger on every prompt:

    {
      "hooks": {
        "UserPromptSubmit": [
          {
            "matcher": "",
            "hooks": [
              {
                "type": "command",
                "command": "/home/catow/GIT/Hackathon/scripts/skill-router-hook.sh",
                "timeout": 10,
                "statusMessage": "Routing skill..."
              }
            ]
          }
        ]
      }
    }

(Adjust the path if you cloned elsewhere; honors `SKILL_ROUTER_ROOT` env var.)

Disable temporarily with `export SKILL_ROUTER_DISABLE=1`.
Tune match threshold with `export SKILL_ROUTER_MIN_SCORE=2.0`.
Limit companion suggestions from RELATES_TO edges with `export SKILL_ROUTER_RELATES_K=3` (default 3; set to 0 to disable).

Requires Neo4j running (`docker compose up -d neo4j`) and an indexed graph
(`uv run python -m skill_router index`).
