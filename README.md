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
uses an LLM to extract intent + signals from each user prompt and traverse the graph to find the
most relevant skill. The chosen skill name is injected into Claude Code as `additionalContext`
via a `UserPromptSubmit` hook, so Claude Code calls the right `Skill` tool autonomously.

```
user prompt  →  hook  →  LLM extract  →  Neo4j graph  →  LLM judge  →  skill hint  →  Claude Code
```

No API keys required. The hook reuses Claude Code's existing OAuth session via `claude-agent-sdk`.

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
(:Skill {name, description, scope, source_path})
(:Intent {label})        -- e.g. "build a rag system"
(:Signal {kind, value})  -- kind ∈ {keyword, file_ext, tool_name, error_pattern, pattern}

(s:Skill)-[:TRIGGERED_BY]->(n:Intent | Signal)   -- this signal activates the skill
(s:Skill)-[:EXCLUDED_BY]->(n:Signal)             -- this signal blocks the skill
(s:Skill)-[:RELATES_TO]->(t:Skill)               -- companion skills (soft boost)
```

### Routing pipeline

1. **Indexing (offline)** — every `SKILL.md` is parsed, then an LLM extracts `{intents, signals,
   exclusions, related_skills}` and writes nodes/edges to Neo4j.
2. **Extract (per query)** — LLM converts the raw prompt into intent labels + signal candidates.
3. **Graph traversal** — a single Cypher query scores skills by matched edges (Intent = +2,
   Signal = +1) while filtering out skills with active `EXCLUDED_BY` signals.
4. **LLM judge** — top-K is reranked with one final LLM call to disambiguate near-ties.
5. **RELATES_TO boost** — neighbors of the top-1 are surfaced as companion skills.
6. **Hook output** — `additionalContext` is emitted as `Skill({skill: "name"})`. Claude Code
   then calls the tool natively, with no further intervention.

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

### Wire the hook into Claude Code
The repo ships a project-level hook for the testbed. To enable it globally, add to
`~/.claude/settings.json`:

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

Restart Claude Code. From now on, every prompt is routed through Skillogy before it reaches the model.

### Try the web UI (optional)
```bash
make ui                             # backend :8765 + frontend :5173
```
Browse the graph, filter by project/scope, click a skill to see its trigger surface.

---

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `SKILLOGY_DISABLE` | Bypass the hook (passthrough) | unset |
| `SKILLOGY_MIN_SCORE` | Minimum router score to inject hint | `0.4` |
| `SKILLOGY_RELATES_K` | Companion skills surfaced via RELATES_TO | `3` |
| `SKILLOGY_LLM` | `sdk` \| `gemini` \| `api` | `sdk` (OAuth) |
| `SKILLOGY_EXTRA_ROOTS` | Colon-separated extra skill dirs | unset |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Neo4j connection | localhost defaults |

---

## What's next

- [ ] Package as a Claude Code plugin (currently installed manually as a hook)
- [ ] Vector embedding fallback for cold-start projects
- [ ] Built-in `RELATES_TO` learning from co-invocation traces
- [ ] MCP server expansion (currently exposes `find_skill` and `list_skills`)

---

## License

MIT — see [LICENSE](LICENSE).

---

## Project status

Hackathon prototype, actively maintained. Issues and PRs welcome.
