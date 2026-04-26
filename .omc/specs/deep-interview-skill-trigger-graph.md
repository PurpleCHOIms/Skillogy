# Deep Interview Spec — Skill Trigger Graph Service (24h Hackathon MVP)

## Metadata

- **Interview ID**: skill-trigger-graph-2026-04-26
- **Rounds**: 9 (7 ouroboros + 2 OMC deep-interview)
- **Final Ambiguity Score**: 15.2%
- **Threshold**: 20% — **PASSED**
- **Type**: greenfield (with brownfield-style data dependency on user's local SKILL.md ecosystem)
- **Generated**: 2026-04-26
- **Status**: PASSED
- **Hackathon**: cmux × AIM Intelligence (Business & Applications track, 24h)

## Clarity Breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal Clarity | 0.95 | 0.40 | 0.380 |
| Constraint Clarity | 0.78 | 0.30 | 0.234 |
| Success Criteria | 0.78 | 0.30 | 0.234 |
| **Total Clarity** | | | **0.848** |
| **Ambiguity** | | | **0.152 (15.2%)** |

---

## Goal

Build an **external service** that scans all locally-installed Claude Code SKILL.md files (`~/.claude/skills/`, project `.claude/skills/`, plugin SKILL.md), uses LLM extraction to construct a typed knowledge graph (Skill / Intent / Capability / Signal nodes connected by `solves` / `triggered_by` / `forbidden_by` / `requires` / `composes_with` edges), and exposes the graph through:

1. **MCP server** — agents query the graph and **the MCP returns the SKILL.md body itself**, so the agent immediately receives the right skill loaded into its context. The MCP doesn't just *recommend* a skill — it effectively **triggers** the skill by injecting its content. Cross-agent: Claude Code, Cursor, Codex, Gemini CLI, etc.
2. **Web UI dashboard** — visualizes the user's own SKILL.md ecosystem as the typed graph; clicking a Skill node opens the original SKILL.md body for inspection

The goal is to **measurably improve skill trigger success rate** vs the default flat-list approach used by Claude Code, demonstrating that relation-aware GraphRAG routing beats vector-only / flat metadata exposure.

### User Flow (the problem we solve)

**Default (problem):**
```
User input → Claude Code → tries to discover skill from flat metadata list
            → ~50% trigger rate → often no skill loaded → degraded answer
```

**With our service:**
```
User input → Claude Code's agent → calls our MCP `find_skill(query)`
            → our service: GraphRAG over SOG → picks most relevant Skill
            → MCP returns {skill_name, skill_body, reasoning_path}
            → agent receives SKILL.md content directly in context
            → executes per skill instructions (effectively "triggered")
```

The MCP tool is the **trigger mechanism** — by returning the SKILL.md body as the tool result, the right skill enters the agent's context regardless of whether Claude Code's native discovery would have found it.

---

## Constraints

- **24h hackathon timeline** (1 day total)
- **Two-tier integration** (per Round 10 update):
  - **Claude Code (primary)**: install a `UserPromptSubmit` hook so every user input is intercepted, routed through our service, and the matched SKILL.md body is injected into context as `additionalContext` / `system-reminder`. This is the **strict trigger** path — the agent receives the right skill content automatically on every turn.
  - **Other agents (cross-agent future)**: MCP server only — voluntary call from Cursor / Codex / Gemini CLI / etc.
- **"Organic" = read existing SKILL.md format only** — zero changes to skill authoring workflow required; users only install our service (hook + MCP)
- **Dual LLM auth** (per Round 10 update):
  - **Primary**: Claude Agent SDK (`claude-agent-sdk`) — inherits Claude Code authentication automatically; no separate API key needed for users on Claude Code subscription
  - **Fallback**: explicit `ANTHROPIC_API_KEY` env var — for users without Claude Code or for standalone CLI / MCP-only deployments
  - Auto-detect preference order: Claude Agent SDK if available, else env var, else fail with clear setup instruction
- **Pure GraphRAG routing** — no vector embedding similarity in candidate selection (relations are the differentiator)
- **Knowledge-map schema** (Round 13 update): graph carries TWO classes of relations
  - **Trigger surface** (primary, drives routing): `(:Skill)-[:TRIGGERED_BY]->(:Intent|:Signal)` and `(:Skill)-[:EXCLUDED_BY]->(:Signal)` — what user inputs activate / exclude this skill
  - **Knowledge map** (secondary, drives recommendations): `(:Skill)-[:RELATES_TO]->(:Skill)` — inter-skill connections forming an organic skill graph for "you might also need" companion routing
- **Tier 1 + Tier 2 extraction only** — `name` + `description` (frontmatter) + body LLM extraction; ignore non-standard frontmatter (Tier 3 cut, since only 12% use `allowed-tools`, 0% use `See also`)
- **API budget**: < $5 USD for indexing + benchmark (Haiku 4.5 primary)
- **Scope of scanned skills**: user's local environment (652+ SKILL.md found in `~/.claude/`)
- **Graph storage** (per Round 12 update): **Neo4j 5 Community Edition** via Docker (`docker compose up -d neo4j`). Cypher native query language. Replaces earlier networkx + JSON snapshot plan. `neo4j-python-driver` for Bolt access.
- **Python env**: managed via `uv` (`uv venv .venv && uv pip install -e ".[dev]"`)
- **Project layout**: src/ layout with concern separation — `src/skill_router/{domain,infra,core,adapters}/` + top-level `bench/` package + `web/` frontend (Vite/React)

---

## Non-Goals (explicitly OUT of v1)

- **Per-agent skill profile management** in Web UI (Profile-based subset assignment) — future
- **Live routing tester** in Web UI (type-a-query-see-result panel) — future
- **AIM Intelligence safety policy integration** (Guard / Stinger / Supervisor) — future
- **cmux-specific socket/CLI integration** — cmux per-agent scoping is inherited "for free" via worktree-based read paths; no special cmux adapter
- **Claude Code plugin distribution** — future packaging
- **Skill format extension or modification** — read-only consumer of existing format
- **Multi-user / org-scoped skill sharing** — single-user local scope only
- **Vector embedding-based retrieval** — explicitly excluded by user (relations-only routing)

---

## Acceptance Criteria

### Core Engine
- [ ] Scans `~/.claude/skills/`, project `.claude/skills/`, and plugin SKILL.md paths recursively (652+ files in this user's env)
- [ ] Parses each SKILL.md: extracts `name` and `description` from YAML frontmatter; reads full body as markdown
- [ ] Extracts typed nodes per skill via Haiku LLM call: Skill (1 per file, name as id) + Intent + Capability + Signal nodes
- [ ] Builds typed edges: `solves`, `triggered_by`, `forbidden_by`, `requires`, `composes_with`
- [ ] Persists graph to disk (JSON or sqlite); incremental re-index on file change
- [ ] Routing API: given a user query string, returns top-K (5-10) ranked Skill names + scores within < 2 seconds
- [ ] Routing algorithm: Pure GraphRAG — extract Intent/Signal from query (1 Haiku call), graph traversal 1–2 hop, edge-type weighted sum (`triggered_by` strong / `solves` medium / `composes_with` weak / `forbidden_by` = absolute exclude), Haiku LLM judge picks final winner from top candidates

### MCP Server (cross-agent voluntary surface)
- [ ] Primary MCP tool: `find_skill(query: str) → {skill_name, skill_body, reasoning_path, alternatives: [{name, score}]}`
  - **`skill_body` is the full SKILL.md content of the top-1 match** — this is what makes our MCP a *trigger* not a *recommender*. The agent receives executable skill instructions directly.
  - `reasoning_path` shows the graph traversal (which Intent / Signal nodes matched, which edges led to this Skill) for explainability
  - `alternatives` lists top 2–5 next candidates with scores for fallback / agent override
- [ ] Optional MCP tool: `list_skills(filter?: str) → [{name, description}]` for discovery / debugging
- [ ] Compliant with MCP spec — installable in Claude Code, Cursor, Gemini CLI etc. via standard `mcp` config (stdio transport)
- [ ] Returns within 2 seconds; handles concurrent requests
- [ ] Live demo: in any MCP-aware client, send a user query that historically fails to trigger any skill → our MCP returns the right SKILL.md body → agent immediately executes the skill correctly

### Claude Code Hook (strict trigger path)
- [ ] `UserPromptSubmit` hook installed on user's Claude Code (`~/.claude/settings.json` or project `.claude/settings.json`)
- [ ] Hook script (Python) entry: receives user prompt via stdin, calls core routing engine, emits matched SKILL.md body to stdout in `additionalContext` JSON shape
- [ ] Claude Code automatically injects the additionalContext as `system-reminder` on every turn → strict trigger achieved
- [ ] Hook completes within < 500 ms p95 (router cache + Claude Agent SDK reuse)
- [ ] Graceful degradation: if router returns no confident match (score below threshold), hook emits empty additionalContext (no injection — Claude Code falls back to native discovery)
- [ ] Configurable: user can disable via env var (`SKILL_ROUTER_DISABLE=1`) or settings flag

### Dual LLM Auth
- [ ] Auto-detect: prefer Claude Agent SDK if importable AND credentials available
- [ ] Fallback: explicit `ANTHROPIC_API_KEY` env var → use `anthropic` Python SDK directly
- [ ] No-credentials fail fast: print clear setup message ("Install claude-agent-sdk OR set ANTHROPIC_API_KEY")
- [ ] Same routing API surface regardless of auth path

### Web UI Dashboard (management surface)
- [ ] Skill catalog view: scrollable list of all scanned Skills with name + description; search/filter by name substring
- [ ] Graph visualization: interactive SOG renderer (cytoscape.js or similar). **Nodes = Skills (primary)** + Intent/Capability/Signal (secondary, color/size differentiated). Edges = the 6 relation types, color-coded
- [ ] **Node click → SKILL.md body display** in side panel or modal: shows raw markdown of the source file (with parsed frontmatter)
- [ ] Layout: list/catalog on left, graph viz center/main, detail panel on right (or modal)
- [ ] Graph reflects the actual extracted structure of the user's local SKILL.md ecosystem — i.e. it visualizes "your skills, as our graph DB sees them"

### Benchmark Report (separate proof artifact)
- [ ] CLI script that runs an evaluation set against three conditions: Baseline-Flat (all skills as flat list), Baseline-Vector (description embedding top-K), Ours-SOG (graph routing)
- [ ] Eval set: 200–500 from MetaTool ToolE sample + 50–100 synthesized from user's personal `~/.claude/skills/` (per `research/02-benchmark-plan.md`)
- [ ] Output: PNG/PDF chart showing Precision@1, Recall@5, average tokens per query for each condition
- [ ] **Acceptance threshold**: Ours-SOG must achieve Precision@1 at least **+10pp** above Baseline-Flat on the eval set
- [ ] Reproducible: prompt templates, model versions, seed all committed to git

### Cross-cutting
- [ ] Runs on user's local machine (Linux/WSL); zero external infrastructure required
- [ ] All code + spec + benchmark output committed to `/home/catow/GIT/Hackathon/`
- [ ] License + attribution clear (MetaTool MIT cited, Anthropic SKILL.md spec credited)

---

## Assumptions Exposed & Resolved

| Assumption | Challenge / Source | Resolution |
|---|---|---|
| Anthropic SKILL.md format compliance is uniform | Empirical scan of 652 local files | Only `name` (99.5%) and `description` (99.2%) reliable; build for noisy real-world data |
| Should integrate into Claude Code internals | User clarified scope of "organic" | OUT — external service that *reads* Claude Code's skill directories, no hook integration |
| Vector RAG is necessary for paraphrase matching | User: "릴레이션이 핵심" (relations are key) | Cut vector entirely, pure GraphRAG; Tier1+Tier2 LLM extraction handles paraphrase normalization |
| Web UI is a comparison/demo playground | User clarified twice | NO — Web UI = management dashboard (catalog + graph viz). Comparison is a separate benchmark report |
| Per-agent profile management is core differentiator | User did NOT select in Round 9 must-have vote | NOT in v1; cmux per-agent angle deferred to future |
| AIM Intelligence integration is sponsor must-do | Time analysis vs MVP scope | Out of v1; future post-hackathon |
| MCP server is "stretch" not core | User explicitly named MCP as agent's search entry point | ELEVATED to must-have in v1 |
| Graph viz is "wow factor only" | User: graph viz IS the product surface | Graph viz = primary product UI showing our structural value |
| All 600+ skills must work correctly | Scope check | Yes — eval set drawn from real local skills + MetaTool sample |

---

## Technical Context (greenfield)

### Repo State
- Working directory: `/home/catow/GIT/Hackathon`
- Existing artifacts: `research/01-problem-validation.md` (quantitative pain + KG vs vector benchmark numbers), `research/02-benchmark-plan.md` (MetaTool eval plan, $5 budget)
- No existing source code — full greenfield implementation

### Local Data Source (input)
- 652 SKILL.md files discovered under `~/.claude/`
- Distribution: plugin skills (vast majority), user skills, project-scoped (`.claude/skills/`) when applicable
- Format reality (per empirical analysis):
  - `name` 99.5%, `description` 99.2% — universal
  - `allowed-tools` 12%, `disable-model-invocation` 0.9%, "When to use" sections 6%, "See also" 0%
  - Body length median 15K chars (3× Anthropic's 500-line recommendation)
  - 27 non-standard frontmatter fields invented across ecosystems

### Implementation Recommendations (open to refinement in ralplan)
- **Backend / Core**: Python 3 (LLM ecosystem mature, pyyaml + markdown + networkx friendly)
- **LLM auth**: dual path — Claude Agent SDK (`claude-agent-sdk`) primary (inherits Claude Code auth) + `anthropic` SDK with `ANTHROPIC_API_KEY` fallback
- **LLM model**: Anthropic Claude Haiku 4.5 (`claude-haiku-4-5-20251001`, temp=0) for extraction + routing judge
- **Embedding**: NOT used (vector cut per Round 7)
- **Graph storage**: networkx in-memory + JSON snapshot (sqlite if needed for incremental); no graph DB install
- **MCP server**: official `mcp` Python SDK (stdio transport)
- **Hook**: bash entry → `python3 -m skill_router.hook` ; called via `UserPromptSubmit` event in `~/.claude/settings.json` ; warm Python process via subprocess optimization or persistent socket (target < 500 ms p95)
- **Web UI**: **Vite + React + shadcn + cytoscape.js** (Vite chosen over Next.js for hackathon speed); talks to backend via local HTTP (FastAPI)
- **Benchmark CLI**: Python script, matplotlib for PNG, JSON intermediate
- **Background indexing**: kick off SKILL.md scan + LLM extraction in background as soon as core is testable, so by Web UI dev hour 12 the graph DB is already populated

### Layout (updated post-restructure)
- Package layout switched to **src-layout**: `src/skill_router/{domain,infra,core,adapters}/`
- Benchmark split to top-level **`bench/`** package (separate from `src/skill_router`)
- `pyproject.toml` wheel packages: `["src/skill_router", "bench"]`
- Tests reorganized to `tests/unit/{infra,core,bench}/` and `tests/integration/`
- `skill_router.auth` renamed to `skill_router.infra.llm`; all imports use new absolute paths

### Time Allocation (rough — Round 10 update, 5 deliverables)
- Core engine + extractor: 6h
- Claude Code Hook + Agent SDK + dual auth wrap: 3h
- MCP server wrapping core: 1.5h
- Web UI (catalog + graph viz + SKILL.md drilldown, Vite): 5h
- Benchmark CLI + eval set + chart: 4h
- Integration + dogfood + buffer: 2h
- Pitch prep: 1.5h
- Sleep / contingency: 1h
- **Total: 24h** (tight — runs indexing in background during UI dev to parallelize)

---

## Ontology (Key Entities)

| Entity | Type | Fields | Relationships |
|---|---|---|---|
| **Skill** | core domain | name (id), description, body, source_path | solves Intent; triggered_by Signal; forbidden_by Signal; composes_with Skill |
| **Intent** | core domain | label, paraphrases (extracted) | solves ← Skill |
| **Capability** | supporting | label, type | (informational; surfaced in detail view) |
| **Signal** | core domain | type (keyword / pattern / file-ext / tool-name), value | triggered_by ← Skill; forbidden_by ← Skill |
| **AgentProfile** | future / non-MVP | name, allowed_skills | (post-v1) |
| **MCPServer** | artifact | tool name, response schema | (separate output) |
| **WebUIDashboard** | artifact | features (catalog, graph viz, SKILL.md drilldown) | (separate output) |
| **CoreEngine** | artifact | scanner, extractor, graph store, router | (separate output) |
| **BenchmarkReport** | artifact | metrics, chart | (separate output) |

---

## Ontology Convergence

| Round | Entity Count | New | Changed | Stable | Stability |
|---|---|---|---|---|---|
| 1 | 3 | Skill, ProblemClass, Intent | — | — | N/A |
| 2 | 4 | +Signal | — | 3 | 75% |
| 3 | 5 | +Capability | — | 4 | 80% |
| 4 | 6 | +AgentProfile (cmux angle) | — | 5 | 83% |
| 5 | 6 | — | — | 6 | 100% |
| 6 | 6 (Tier3 cut) | — | — | 6 | 100% |
| 7 | 5 (ProblemClass merged into Intent/Signal) | — | merge | 5 | 80% |
| 8 | 5 + 4 artifacts | +Core/MCP/UI/Benchmark | — | 5 core | 90% |
| 9 | 4 core + 4 artifacts | — | AgentProfile → non-MVP | 8 | **95%** |

→ **Core domain entities (Skill / Intent / Capability / Signal) stable since round 5** = converged. Round 9 finalized scope by demoting AgentProfile to future.

---

## Interview Transcript Summary

<details>
<summary>Q1–Q9 (9 rounds, mixed Ouroboros MCP + OMC Path B)</summary>

**Q1 — Failure mode dominance**: User picks **Miss** (skill exists but doesn't trigger) over Mismatch / Overload. → Sets routing-as-discovery, not as filter.

**Q2 — Root cause of Miss**: (a) attention dilution from large metadata + (b) lexical mismatch between user phrasing and skill description. (c) partial exposure ruled out as a downstream symptom of (a). → Schema must address both volume compression and semantic bridging.

**Q3 — Product shape**: External independent service. Not a Claude Code plugin or hook integration. Web UI (management dashboard) + Core engine + MCP server (cross-agent surface).

**Q4 — Claude Code integration mechanism**: User clarified "organic" = compatibility with existing SKILL.md format, NOT runtime hook injection. Service reads existing skill directories; no internal hooks.

**Q5 — Hero shot mechanism**: Originally chose Web UI comparison playground; later corrected — Web UI is management, benchmark is a SEPARATE artifact (chart). MCP server initially "stretch" → later elevated to must-have when user named it as agent's search entry point.

**Q6 — Schema complexity**: Middle (Skill / Intent / Capability / Signal) + Hybrid edges (explicit from frontmatter / body, semantic from LLM extraction). Tier 1 (`name + description`) + Tier 2 (body LLM) only. **Tier 3 (non-standard frontmatter) cut** based on user direction confirmed by 27-field empirical chaos.

**Q7 — Routing algorithm**: Pure GraphRAG — graph traversal 1–2 hop with edge-type weighting + Haiku LLM judge over top candidates. **Vector embedding cut entirely** ("relations are key").

**Q8 — Web UI metaphor**: User cleared up — UI is management dashboard, not comparison playground. Comparison is separate benchmark report.

**Q9 — Web UI must-have features**: Skill catalog (list + search) + Graph visualization. Per-agent profile management and live routing tester explicitly NOT in v1. Plus user's clarifying message: graph viz IS the product surface (visualizes "user's SKILL.md ecosystem as our graph DB"); clicking a Skill node opens raw SKILL.md body; agents query the graph through the MCP server.

</details>

---

## Open Implementation Questions (for ralplan stage, not requirements)

These are not ambiguity in the requirements — they are technical choices that the implementation planning stage (`ralplan` / `omc-plan`) should resolve:

1. **Frontend stack**: Next.js + shadcn vs Vite + React vs Streamlit. Next.js gives polish; Vite is fastest for 24h; Streamlit is fastest but less control over graph viz UX
2. **Graph layout algorithm**: cytoscape.js force-directed vs cose-bilkent vs d3-force. cytoscape.js has the richest plugin ecosystem
3. **Eval label generation**: human-curated 50-100 personal + LLM-generated 200 from MetaTool, vs all LLM-generated with spot-check
4. **Incremental indexing**: re-scan all 652 vs file watcher with delta updates
5. **MCP transport**: stdio vs HTTP/SSE — stdio is simpler for local single-machine demo
6. **Concurrent HTTP serving**: FastAPI vs Flask vs aiohttp — FastAPI best for MCP+HTTP combined

---

## Status

**Spec is ready.** Ambiguity 15.2% is comfortably below the 20% threshold. Open items above are implementation tactics, not requirements.

Recommended next step: **omc-plan with `--consensus --direct` flags** to engage Planner/Architect/Critic on the spec, producing an execution-ready plan, then handoff to `autopilot` Phase 2 for parallel implementation.

Alternative: jump directly to `autopilot` (skip ralplan) for fastest execution at the cost of consensus refinement.

Alternative: jump to `team` (N coordinated agents) for max parallelism on the 4 separable artifacts.
