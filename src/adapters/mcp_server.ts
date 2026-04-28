#!/usr/bin/env node
import neo4j from "neo4j-driver";
import { z } from "zod";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { getDriver } from "../infra/db.js";
import { Router } from "../core/router.js";

export async function startMcp(): Promise<void> {
  const server = new McpServer({ name: "skillogy", version: "0.2.0" });

  server.registerTool(
    "find_skill",
    {
      description:
        "Route a natural-language query to the most relevant Claude Code skill via the Skillogy GraphRAG router.",
      inputSchema: {
        query: z.string().describe("The user query to route"),
        topK: z.number().optional().default(5),
        loadBody: z.boolean().optional().default(true),
      },
    },
    async ({ query, topK, loadBody }) => {
      const router = new Router();
      const result = await router.findSkill({
        query,
        topK: topK ?? 5,
        loadBody: loadBody ?? true,
      });
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      };
    },
  );

  server.registerTool(
    "list_skills",
    {
      description: "List indexed skills, optionally filtered by a name substring.",
      inputSchema: {
        filter: z.string().optional().describe("Substring filter on skill name"),
      },
    },
    async ({ filter }) => {
      const drv = getDriver();
      const { records } = await drv.executeQuery(
        "MATCH (s:Skill) RETURN s.name AS name, s.description AS description, s.scope AS scope ORDER BY s.name",
        undefined,
        { routing: neo4j.routing.READ },
      );
      let rows = records.map((r) => ({
        name: String(r.get("name")),
        description: String(r.get("description") ?? ""),
        scope: String(r.get("scope") ?? "user"),
      }));
      if (filter && filter.trim()) {
        const f = filter.toLowerCase();
        rows = rows.filter((row) => row.name.toLowerCase().includes(f));
      }
      return {
        content: [{ type: "text", text: JSON.stringify(rows, null, 2) }],
      };
    },
  );

  const transport = new StdioServerTransport();
  await server.connect(transport);
  process.stderr.write("[skillogy mcp] running on stdio\n");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  startMcp().catch((exc) => {
    process.stderr.write(`[skillogy mcp] fatal: ${(exc as Error).message}\n`);
    process.exit(1);
  });
}
