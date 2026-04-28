---
description: First-time Skillogy setup — ask user for scope, start services, then index
allowed-tools: Bash, AskUserQuestion
---

You are running **Skillogy first-time setup**. Follow these steps and report concise progress
to the user.

### Step 1 — Ask the user which skill scope to index

Use `AskUserQuestion` with these options (recommend `user,project` as default):

- **user**: Only my personal skills under `~/.claude/skills/` — fastest (~tens of skills)
- **project**: Only the current project's `.claude/skills/` — minimal
- **user,project**: Both my user skills and the current project — recommended
- **all**: Everything including plugin skills — slow first run (~thousands of skills)

Save the chosen scope as `SCOPE` for the next steps.

### Step 2 — Bring up infrastructure

The plugin ships pre-bundled `dist/`, so this only:
- Brings Neo4j up via `docker compose up -d neo4j` (if not already running)
- Records whether Docker is missing so the hook can surface a one-time hint
- Triggers background indexing (Step 3 also runs it explicitly with the chosen scope)

!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/skillogy-bootstrap.sh"`

### Step 3 — Trigger indexing in background with the chosen scope

Replace `<SCOPE>` below with the user's choice from Step 1, then run:

!`mkdir -p /tmp/skillogy && SKILLOGY_INDEX_SCOPES="<SCOPE>" nohup bash "${CLAUDE_PLUGIN_ROOT}/scripts/skillogy-cli.sh" index --incremental --workers 8 --scopes "$SKILLOGY_INDEX_SCOPES" > /tmp/skillogy/setup-index.log 2>&1 &`

### Step 4 — Report to the user

Tell the user:
- Backend (when running): http://localhost:8765
- Indexing log: `tail -f /tmp/skillogy/setup-index.log`
- Indexing runs in background. The hook starts working immediately for skills already in the graph
  and improves as more are indexed.
- To re-index later: `/skillogy:reindex`
