# Skillogy

> GraphRAG-based skill router for Claude Code. Routes every prompt through a
> Neo4j knowledge graph of your `SKILL.md` files so the right skill triggers
> based on intent — not on whether your wording happens to match a description.

## Install

```bash
/plugin marketplace add PurpleCHOIms/Skillogy
/plugin install skillogy@skillogy
```

Prerequisites: **Node.js ≥ 20** and **Docker** (for the Neo4j container).
Everything else is automatic — the SessionStart hook starts Neo4j and
indexes `~/.claude/skills` + project `.claude/skills` in the background.
Until indexing finishes, the hook gracefully passes through with a one-time
status message.

## Benchmark

29 testbed skills, 29 natural-language queries, same `claude --print`. Only
variable: whether Skillogy's `UserPromptSubmit` hook is active. Detection
criterion: Claude Code emits the correct `Skill({skill: "..."})` tool call
within 60 s.

| Model | Native | With Skillogy | Improvement |
|---|---|---|---|
| Claude Haiku 4.5 | 20.7 % | **62.1 %** | +41.4 pp |
| Claude Sonnet 4.6 | 48.3 % | **100.0 %** | +51.7 pp |
| Claude Opus 4.7  | 69.0 % | **96.6 %**  | +27.6 pp |

Hook p95 latency < 10 s. Numbers are from the original Python implementation
under `legacy-python/bench/`; the TypeScript port preserves the routing
pipeline byte-for-byte.

## License

MIT — see [LICENSE](LICENSE).
