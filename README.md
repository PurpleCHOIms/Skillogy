# Skillogy

> **Why don't agents use skills properly?**
>
> You wrote a SKILL.md. The agent ignored it. You wrote a longer description. It still ignored it.
> The problem isn't your skill — it's that LLMs scan a flat list of skill descriptions and only
> trigger when the user prompt happens to phrase things the same way the description does.
>
> Skillogy fixes this with a **GraphRAG router** that sits in front of Claude Code (or any
> agent) and tells it which skill to load — based on the *meaning* of the request, not the surface words.

---

## What it does

Skillogy turns your scattered `SKILL.md` files into a **typed knowledge graph** in Neo4j, then
uses an LLM to extract intent + signals from each user prompt and traverse the graph to find
the most relevant skill. The chosen skill name is injected into Claude Code as
`additionalContext` via a `UserPromptSubmit` hook, so Claude Code calls the right `Skill`
tool autonomously.

```
user prompt  →  hook  →  LLM extract  →  Neo4j graph  →  LLM judge  →  skill hint  →  Claude Code
```

No API keys required by default. The hook reuses Claude Code's existing OAuth session via
`claude-agent-sdk`. Gemini and direct Anthropic API keys are also supported as opt-in
backends (better for high-throughput indexing).

---

## Benchmark

29 testbed skills. 29 natural-language queries. Same skill catalog, same Claude Code CLI
(`claude --print`). Only difference: whether our `UserPromptSubmit` hook is active.

| Model | Native (no hook) | With Skillogy hook | Improvement |
|---|---|---|---|
| **Claude Haiku 4.5** | 20.7 % | **62.1 %** | +41.4 pp |
| **Claude Sonnet 4.6** | 48.3 % | **100.0 %** | +51.7 pp |
| **Claude Opus 4.7** | 69.0 % | **96.6 %** | +27.6 pp |

`p95` latency added by the hook is < 10 s on average. Detection criterion: Claude Code emits
the correct `Skill({skill: "..."})` tool call within 60 s.

Reproduce: `make bench-claude-all` (writes `bench/results/{model}_{timestamp}_summary.json`).

---

## Architecture

### Graph schema (Neo4j)

```
(:Skill {name, description, scope, source_path, body_length})
(:Intent {label})        -- e.g. "build a rag system"
(:Signal {kind, value})  -- kind ∈ {keyword, file_ext, tool_name, error_pattern, pattern}

(s:Skill)-[:TRIGGERED_BY]->(n:Intent | Signal)   -- this signal activates the skill
(s:Skill)-[:EXCLUDED_BY]->(n:Signal)             -- this signal blocks the skill
(s:Skill)-[:RELATES_TO]->(t:Skill)               -- companion skills (soft boost)
```

### Routing pipeline

1. **Indexing (offline)** — every `SKILL.md` is parsed, then an LLM extracts `{intents,
   signals, exclusions, related_skills}` and writes nodes/edges to Neo4j. Runs in parallel
   (5 workers by default) since LLM calls are I/O-bound.
2. **Extract (per query)** — LLM converts the raw prompt into intent labels + signal
   candidates. Falls back to keyword splitting if the LLM call fails.
3. **Graph traversal** — a single Cypher query scores skills by matched edges
   (Intent = +2.0, Signal = +1.0) while filtering out skills with active `EXCLUDED_BY`
   signals.
4. **LLM judge** — top-K is reranked with one final LLM call to disambiguate near-ties.
5. **RELATES_TO boost** — neighbors of the top-1 are surfaced as companion skills.
6. **Hook output** — `additionalContext` is emitted as `Skill({skill: "name"})`. Claude
   Code then calls the tool natively, with no further intervention.

### Project layout

```
src/skillogy/
├── domain/        # ParsedSkill, Signal, TriggerSurface dataclasses
├── infra/         # scanner.py, llm.py (sdk/gemini/api), db.py (Neo4j driver)
├── core/          # extractor.py, graph.py (Neo4j builder), router.py
└── adapters/      # hook.py, mcp_server.py, web_api.py (FastAPI)
bench/             # eval-set generation, runner, claude_runner, charts
web/               # React + Cytoscape graph explorer (Vite)
scripts/           # skillogy-hook.sh entry point
```

---

## Quickstart

### Prerequisites
- Python 3.11+
- Docker (for Neo4j)
- Claude Code CLI (logged in)
- `uv` (or pip)

### Install
```bash
git clone https://github.com/PurpleCHOIms/Skillogy.git
cd Skillogy
make install                        # uv venv + dev deps
cp .env.example .env                # fill in NEO4J_PASSWORD if not default
```

### Index your skills
```bash
make neo4j-up                       # start Neo4j on :7474 / :7687
make index-testbed                  # or `make index` for ALL local SKILL.md
```

`make index` discovers skills from:
- `~/.claude/skills/` (user scope)
- `~/.claude/plugins/` (plugin scope)
- `<project>/.claude/skills/` for every project Claude Code knows about (project scope)
- Anything in `SKILLOGY_EXTRA_ROOTS` (colon-separated paths)

Run `make index-clear` to wipe the graph; `make confidence` for a 2-minute end-to-end
sanity check (env, tests, Neo4j, smoke index, router, hook, MCP).

### Wire the hook into Claude Code
The repo ships a project-level hook for the testbed. To enable it globally, run:

```bash
make hook-install-snippet           # prints the JSON to merge
```

Then merge the printed snippet into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/Skillogy/scripts/skillogy-hook.sh"
          }
        ]
      }
    ]
  }
}
```

Restart Claude Code. From now on, every prompt is routed through Skillogy before it
reaches the model. Hook activity is logged to `/tmp/skill-router-hook.log`.

### Try the web UI (optional)
```bash
make ui                             # backend :8765 + frontend :5173
make start                          # ALSO brings up Neo4j + UI in background
make stop                           # tears it all down
```
Browse the graph, filter by project/scope, click a skill to see its trigger surface.
Backed by a FastAPI service at `:8765` (`/api/skills`, `/api/graph`,
`/api/graph/stream` SSE).

---

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `SKILLOGY_DISABLE` | `1` bypasses the hook (passthrough only) | unset |
| `SKILLOGY_MIN_SCORE` | Minimum router score to inject hint | `0.4` |
| `SKILLOGY_RELATES_K` | Companion skills surfaced via RELATES_TO | `3` |
| `SKILLOGY_LLM` | Provider override: `sdk` \| `gemini` \| `api` | auto (`sdk`) |
| `SKILLOGY_GEMINI_MODEL` | Gemini model id when `SKILLOGY_LLM=gemini` | `gemini-pro-latest` |
| `SKILLOGY_FORCE_API_KEY` | `1` forces direct Anthropic API over the SDK | unset |
| `SKILLOGY_EXTRA_ROOTS` | Colon-separated extra skill dirs (treated as project scope) | unset |
| `GOOGLE_API_KEY` | Required when using Gemini backend | unset |
| `ANTHROPIC_API_KEY` | Required when using direct API backend | unset |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Neo4j connection | localhost / `skillrouter` |

Provider auto-selection order: `SKILLOGY_LLM` override → `GOOGLE_API_KEY` → Claude Agent
SDK → `ANTHROPIC_API_KEY`.

---

## Common Make targets

| Target | What it does |
|---|---|
| `make help` | List every target with descriptions |
| `make confidence` | End-to-end sanity run (~2 min) |
| `make index-testbed` | Index ONLY testbed skills (parallel, 8 workers) |
| `make index` | Index ALL local SKILL.md (~600+, takes minutes) |
| `make router-smoke` | Single routing query against the live graph |
| `make hook-smoke` / `hook-smoke-disabled` | Exercise the hook end-to-end |
| `make bench-claude-all` | Run the full Native vs. Hook benchmark across Haiku/Sonnet/Opus |
| `make charts` | Generate matplotlib PNGs from the latest summary |
| `make start` / `stop` / `restart` | Bring up Neo4j + backend + frontend together |

---

## What's next

- [ ] Package as a Claude Code plugin (currently installed manually as a hook)
- [ ] Vector embedding fallback for cold-start projects
- [ ] Built-in `RELATES_TO` learning from co-invocation traces
- [ ] Polish the experimental MCP adapter (`find_skill` / `list_skills` / `get_skill`) so it's usable cross-agent

---

## License

MIT — see [LICENSE](LICENSE).

---

## Project status

Hackathon prototype, actively maintained. Issues and PRs welcome.
