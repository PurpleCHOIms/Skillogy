import { serve } from "@hono/node-server";
import { Hono } from "hono";
import { cors } from "hono/cors";
import neo4j from "neo4j-driver";

import { getDriver } from "../infra/db.js";
import { exportGraphJson } from "../core/graph.js";
import { Router } from "../core/router.js";

export function createApp(): Hono {
  const app = new Hono();
  app.use("/*", cors());

  app.get("/api/skills", async (c) => {
    const drv = getDriver();
    const { records } = await drv.executeQuery(
      "MATCH (s:Skill) RETURN s.name AS name, s.description AS description, s.scope AS scope, s.source_path AS source_path, s.body_length AS body_length ORDER BY s.name",
      undefined,
      { routing: neo4j.routing.READ },
    );
    return c.json(
      records.map((r) => ({
        name: String(r.get("name")),
        description: String(r.get("description") ?? ""),
        scope: String(r.get("scope") ?? "user"),
        source_path: String(r.get("source_path") ?? ""),
        body_length: Number(r.get("body_length") ?? 0),
      })),
    );
  });

  app.get("/api/skills/:name", async (c) => {
    const name = c.req.param("name");
    const drv = getDriver();
    const { records } = await drv.executeQuery(
      "MATCH (s:Skill {name: $name}) RETURN s.name AS name, s.description AS description, s.scope AS scope, s.source_path AS source_path, s.body_length AS body_length",
      { name },
      { routing: neo4j.routing.READ },
    );
    if (records.length === 0) return c.json({ error: "Not found" }, 404);
    const r = records[0]!;
    const sourcePath = String(r.get("source_path") ?? "");
    let body = "";
    if (sourcePath) {
      try {
        const fs = await import("node:fs");
        body = fs.readFileSync(sourcePath, "utf-8");
      } catch {
        // body stays empty
      }
    }
    return c.json({
      name: String(r.get("name")),
      description: String(r.get("description") ?? ""),
      scope: String(r.get("scope") ?? "user"),
      source_path: sourcePath,
      body_length: Number(r.get("body_length") ?? 0),
      body,
    });
  });

  app.get("/api/graph", async (c) => {
    const data = await exportGraphJson();
    return c.json(data);
  });

  app.get("/api/route", async (c) => {
    const q = c.req.query("q") ?? "";
    if (!q.trim()) return c.json({ error: "missing ?q=..." }, 400);
    const router = new Router();
    const result = await router.findSkill({ query: q, topK: 5, judge: true, extract: true });
    return c.json(result);
  });

  app.get("/health", (c) => c.text("ok"));
  return app;
}

export async function startServer(port = 8765): Promise<void> {
  const app = createApp();
  serve({ fetch: app.fetch, port });
  process.stderr.write(`[skillogy web] listening on http://127.0.0.1:${port}\n`);
}
