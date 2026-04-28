---
description: Show Skillogy status — Neo4j, indexed skill count, hook activity, web UI ports
allowed-tools: Bash
---

Run the following checks and present a concise status table to the user.

1. **Neo4j connectivity + skill count** (via Neo4j HTTP transaction API — no dist/ import needed):
!`if curl -s -o /dev/null -m 2 http://localhost:7474; then COUNT=$(curl -s -u "${NEO4J_USER:-neo4j}:${NEO4J_PASSWORD:-skillrouter}" -H "Content-Type: application/json" -d '{"statements":[{"statement":"MATCH (s:Skill) RETURN count(s) AS n"}]}' http://localhost:7474/db/neo4j/tx/commit 2>/dev/null | sed -n 's/.*"row":\[\([0-9][0-9]*\)\].*/\1/p'); if [ -n "$COUNT" ]; then echo "Neo4j: UP — $COUNT skills indexed"; else echo "Neo4j: UP but query failed (auth?)"; fi; else echo "Neo4j: DOWN"; fi`

2. **Web ports**:
!`for p in 8765 5173; do if lsof -ti:$p >/dev/null 2>&1; then echo "Port $p: UP"; else echo "Port $p: DOWN"; fi; done`

3. **Recent hook activity** (last 5 fires):
!`tail -5 /tmp/skillogy/hook.log 2>/dev/null || echo "(no hook fires yet)"`

4. **Indexing log tail** (last 5 lines):
!`tail -5 /tmp/skillogy/index.log 2>/dev/null || tail -5 /tmp/skillogy/setup-index.log 2>/dev/null || echo "(no indexing log)"`

Summarize for the user. If anything is DOWN, suggest `/skillogy:setup`.
