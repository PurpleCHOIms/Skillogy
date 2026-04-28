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

No API keys required. The hook reuses Claude Code's existing OAuth session via
`@anthropic-ai/claude-agent-sdk`.

---

## Install (Claude Code plugin — recommended)

```bash
# 1. add the marketplace
/plugin marketplace add PurpleCHOIms/Skillogy

# 2. install the plugin
/plugin install skillogy@skillogy
```

That's it. The plugin payload comes from **npm** — `marketplace.json` points
`source` at the `skillogy` npm package, so `/plugin install` pulls a pre-bundled
build (no `npm install` on your machine, no waiting for a build step). The
`SessionStart` hook then automatically:

1. Starts a local Neo4j container via `docker compose up -d neo4j`
2. Kicks off incremental indexing of your `~/.claude/skills` and project `.claude/skills` in the background

After that, every prompt is routed through Skillogy before it reaches the model.

### Prerequisites

- **Node.js >= 20** (used to run the bundled plugin code; no `npm install` needed)
- **Docker** (for Neo4j 5 Community — single container, no manual config)
- **Claude Code CLI** (logged in)

### First-run timing

Be patient on the very first session — these run in the background, and the hook
gracefully passes through with a one-time message until they finish:

| Step | Typical time |
|---|---|
| `/plugin install` (npm download of bundled package) | ~5–15 s |
| SessionStart bootstrap (no install/build needed — dist ships pre-bundled) | < 2 s |
| `docker compose up -d neo4j` (cold) | ~10–30 s |
| Initial skill indexing (varies with skill count) | ~1–3 min for ~600 skills |

Run `/skillogy:status` to see live progress. Once the graph has ≥ 1 skill, every
new prompt gets routed.

### Slash commands shipped with the plugin

| Command | Purpose |
|---|---|
| `/skillogy:setup` | First-time setup — pick scope, bring services up, kick off indexing |
| `/skillogy:reindex` | Re-index after adding new SKILL.md files (incremental or full) |
| `/skillogy:status` | Show Neo4j status + indexed skill count + recent hook fires |

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

(Benchmark numbers are from the original Python implementation — see `legacy-python/bench/`.
The TypeScript port preserves the routing semantics byte-for-byte; bench parity work is tracked
in the issue tracker.)

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
6. **Hook output** — `additionalContext` is emitted as a `Skill({ skill: "name" })` hint.
   Claude Code then calls the tool natively, with no further intervention.

---

## Development (without the plugin manager)

The repo ships pre-built bundles in `dist/`, so for dev you only need
`npm install` if you intend to edit `src/`:

```bash
git clone https://github.com/PurpleCHOIms/Skillogy.git
cd Skillogy
npm install               # only needed if you'll edit src/
cp .env.example .env
docker compose up -d neo4j
npm run build             # rebundles dist/ via esbuild
node dist/cli/index.js index --incremental --scopes user,project
```

To force the SessionStart bootstrap to rebuild from source instead of using the
shipped bundle, export `SKILLOGY_DEV=1` before starting Claude Code.

Then wire the hook manually into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash /absolute/path/to/Skillogy/scripts/skillogy-hook.sh"
          }
        ]
      }
    ]
  }
}
```

### Try the web UI (optional)

```bash
node dist/cli/index.js serve   # backend on :8765
cd web && npm install && npm run dev   # frontend on :5173
```

Browse the graph, filter by project/scope, click a skill to see its trigger surface.

### Run as MCP server

```bash
claude mcp add skillogy -- node /absolute/path/to/Skillogy/dist/adapters/mcp_server.js
```

Then any agent can call `find_skill` and `list_skills` tools.

---

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `SKILLOGY_DISABLE` | Bypass the hook (passthrough) | unset |
| `SKILLOGY_MIN_SCORE` | Minimum router score to inject hint | `0.4` |
| `SKILLOGY_RELATES_K` | Companion skills surfaced via RELATES_TO | `3` |
| `SKILLOGY_LLM` | `sdk` \| `gemini` \| `api` | `sdk` (OAuth) |
| `SKILLOGY_FORCE_API_KEY` | Skip SDK auto-detect, use API key path | unset |
| `SKILLOGY_EXTRA_ROOTS` | Colon-separated extra skill dirs | unset |
| `SKILLOGY_WEB_PORT` | Web API port | `8765` |
| `SKILLOGY_GEMINI_MODEL` | Gemini model id | `gemini-pro-latest` |
| `SKILLOGY_HOOK_BUDGET_MS` | Hard cap for primary LLM routing call | `8000` |
| `SKILLOGY_HOOK_FALLBACK_MS` | Cap for keyword-only fallback when budget is blown | `1500` |
| `SKILLOGY_HEALTH_PROBE_MS` | Cap for the upfront Neo4j health probe | `1500` |
| `SKILLOGY_DEV` | Force rebuild from source on bootstrap (devs editing `src/`) | unset |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j connection | `bolt://localhost:7687` / `neo4j` / `skillrouter` |

---

## Troubleshooting

The hook is designed to **never** disrupt your normal Claude Code workflow.
On any failure it passes through silently, and surfaces a one-time
`additionalContext` message so you know what to fix.

| Symptom | Cause | Fix |
|---|---|---|
| First prompt shows `[skillogy] Skillogy is starting up — Neo4j is not reachable yet.` | `docker compose up -d neo4j` is still cold-starting (~10–30 s) | Wait one prompt, or run `/skillogy:status` |
| First prompt shows `[skillogy] Skill graph is still being indexed in the background.` | Initial extraction still running | Wait, or `tail -f /tmp/skillogy/index.log` |
| First prompt shows `[skillogy] Docker not found, so Neo4j cannot auto-start.` | Docker not installed (or not on `PATH`) | Install Docker, or set `NEO4J_URI` to your existing Neo4j |
| Hook is silent forever even after waiting | Neo4j up but graph empty/stale; SDK auth not initialized | `/skillogy:reindex`, then re-prompt |
| Need an emergency bypass | Want to debug without Skillogy interference | `export SKILLOGY_DISABLE=1` and restart Claude Code |
| Want to verify the install hermetically | CI / sanity check | `bash scripts/test-install.sh` (see "Verifying the install" below) |

### Verifying the install

```bash
bash scripts/test-install.sh
```

Mirrors what an OSS user gets via `/plugin install`: runs `npm pack` to produce
the exact tarball that would land on the npm registry, extracts it into
`/tmp/skillogy-install-test`, runs `SessionStart` bootstrap with services
skipped, then fires the hook with sample stdin. Asserts the bootstrap completes
in < 15 s without invoking `npm install` and that the hook returns valid JSON
within the 10 s plugin timeout.

### Releasing (maintainers)

```bash
# 1. bump versions in package.json AND .claude-plugin/marketplace.json
npm version 0.2.1 --no-git-tag-version
# (manually edit marketplace.json source.version to match)

# 2. commit, tag, push
git commit -am "release: v0.2.1"
git tag v0.2.1
git push origin main v0.2.1
```

The `.github/workflows/publish.yml` job runs `npm publish --provenance` on the
tag push (after `prepublishOnly` runs lint + test + build). Requires
`NPM_TOKEN` repo secret.

---

## Migration from the Python implementation

This project was originally implemented in Python (see `legacy-python/`). The TypeScript
port keeps the routing pipeline, Cypher queries, and LLM prompts byte-for-byte identical;
the change is purely about packaging and runtime — TypeScript ships in the same Node.js
runtime as Claude Code, removing the `uv`/Python prerequisite for OSS users.

If you still want to run the original Python codebase: `cd legacy-python && uv sync && make
neo4j-up && make index`.

---

## What's next

- [x] Package as a Claude Code plugin
- [x] Port to TypeScript (single Node.js dependency)
- [ ] Embedded graph DB option (Kùzu) for users without Docker
- [ ] Vector embedding fallback for cold-start projects
- [ ] Built-in `RELATES_TO` learning from co-invocation traces

---

## License

MIT — see [LICENSE](LICENSE).

---

## Project status

Hackathon prototype, actively maintained. Issues and PRs welcome.
