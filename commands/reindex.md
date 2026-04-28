---
description: Re-index skills — asks for scope and full/incremental, then runs
allowed-tools: Bash, AskUserQuestion
---

Re-index Skillogy. Walk the user through two quick choices, then run.

### Step 1 — Ask for the scope

Use `AskUserQuestion`:
- **user** — personal skills only
- **project** — current project only
- **user,project** — both (recommended)
- **all** — everything (incl. plugin skills, slow)

Save as `SCOPE`.

### Step 2 — Ask for the mode

Use `AskUserQuestion`:
- **incremental** — only add skills not already in the graph (fast, recommended)
- **full** — wipe the graph and re-index everything in scope (slow, use after major changes)

Save as `MODE`.

### Step 3 — Run

If `MODE == incremental`:
!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/skillogy-cli.sh" index --incremental --workers 8 --scopes "<SCOPE>"`

If `MODE == full`:
!`bash "${CLAUDE_PLUGIN_ROOT}/scripts/skillogy-cli.sh" index --workers 8 --scopes "<SCOPE>"`

(Replace `<SCOPE>` with the user's choice from Step 1.)

### Step 4 — Report

Show the user the final skill count from the command output.
