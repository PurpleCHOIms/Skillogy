---
description: Show Skillogy status — Neo4j, indexed skill count, hook activity, web UI ports
allowed-tools: Bash
---

Run the following checks and present a concise status table to the user.

1. **Neo4j connectivity + skill count**:
!`if curl -s -o /dev/null -m 2 http://localhost:7474; then node -e "(async () => { const m = await import('${CLAUDE_PLUGIN_ROOT}/dist/infra/db.js'); const drv = m.getDriver(); const { records } = await drv.executeQuery('MATCH (sk:Skill) RETURN count(sk) AS n'); console.log('Neo4j: UP — ' + records[0].get('n').toString() + ' skills indexed'); await m.closeDriver(); })()" 2>/dev/null || echo "Neo4j: UP but query failed"; else echo "Neo4j: DOWN"; fi`

2. **Web ports**:
!`for p in 8765 5173; do if lsof -ti:$p >/dev/null 2>&1; then echo "Port $p: UP"; else echo "Port $p: DOWN"; fi; done`

3. **Recent hook activity** (last 5 fires):
!`tail -5 /tmp/skillogy/hook.log 2>/dev/null || echo "(no hook fires yet)"`

4. **Indexing log tail** (last 5 lines):
!`tail -5 /tmp/skillogy/index.log 2>/dev/null || tail -5 /tmp/skillogy/setup-index.log 2>/dev/null || echo "(no indexing log)"`

Summarize for the user. If anything is DOWN, suggest `/skillogy:setup`.
