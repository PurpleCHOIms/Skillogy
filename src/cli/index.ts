#!/usr/bin/env node
import { Command } from "commander";

import { closeDriver, getDriver } from "../infra/db.js";
import { buildGraph, enrichWithParsed, initSchema } from "../core/graph.js";
import { extract } from "../core/extractor.js";
import { getLlmClient } from "../infra/llm.js";
import { scanSkills } from "../infra/scanner.js";

interface IndexOptions {
  limit?: string;
  workers?: string;
  roots?: string[];
  incremental?: boolean;
  scopes?: string;
}

async function cmdIndex(opts: IndexOptions): Promise<void> {
  const roots = opts.roots && opts.roots.length > 0 ? opts.roots : undefined;
  let parsedSkills = scanSkills(roots);
  console.log(`Scanned ${parsedSkills.length} skills (raw)`);

  const scopesStr = opts.scopes ?? "user,project";
  if (scopesStr && scopesStr !== "all") {
    const wanted = new Set(
      scopesStr
        .split(",")
        .map((s) => s.trim().toLowerCase())
        .filter(Boolean),
    );
    const before = parsedSkills.length;
    parsedSkills = parsedSkills.filter((p) => wanted.has(p.scope));
    console.log(
      `  scope filter (${[...wanted].sort().join(",")}): kept ${parsedSkills.length}/${before}`,
    );
  }

  const limit = opts.limit ? parseInt(opts.limit, 10) : undefined;
  if (limit) {
    parsedSkills = parsedSkills.slice(0, limit);
    console.log(`  limit applied: ${parsedSkills.length}`);
  }

  if (opts.incremental) {
    const drv = getDriver();
    const { records } = await drv.executeQuery("MATCH (sk:Skill) RETURN sk.name AS n");
    const already = new Set(records.map((r) => String(r.get("n"))));
    const before = parsedSkills.length;
    parsedSkills = parsedSkills.filter((p) => !already.has(p.name));
    console.log(
      `  incremental: ${before - parsedSkills.length} already indexed, ${parsedSkills.length} new/changed`,
    );
    if (parsedSkills.length === 0) {
      console.log("Nothing to do.");
      await closeDriver();
      return;
    }
  }

  const llm = await getLlmClient();
  const workers = opts.workers ? parseInt(opts.workers, 10) : 5;
  const extracted: Array<Awaited<ReturnType<typeof extract>>> = [];
  let done = 0;

  // Fixed-size worker pool over a queue of skills
  const queue = parsedSkills.slice();
  const total = parsedSkills.length;

  async function worker(): Promise<void> {
    while (queue.length > 0) {
      const p = queue.shift();
      if (!p) return;
      try {
        const surface = await extract(p, llm);
        extracted.push(surface);
      } catch (exc) {
        console.log(`  skipped ${p.name}: ${(exc as Error).message}`);
      } finally {
        done += 1;
        if (done % 5 === 0 || done === total) {
          console.log(`  extracted ${done}/${total}`);
        }
      }
    }
  }

  await Promise.all(Array.from({ length: workers }, () => worker()));

  const drv = getDriver();
  try {
    await initSchema(drv);
    const summary = await buildGraph(extracted, {
      driver: drv,
      clearFirst: !opts.incremental,
    });
    const parsedLookup = new Map(parsedSkills.map((p) => [p.name, p]));
    const enriched = await enrichWithParsed(parsedLookup, drv);
    const parts = Object.entries(summary).map(([k, v]) => `${k}=${v}`);
    console.log(`Graph built: ${parts.join(", ")}`);
    console.log(`Enriched ${enriched} skill nodes with metadata.`);
  } finally {
    await closeDriver();
  }
}

const program = new Command();
program.name("skillogy").description("Skillogy GraphRAG skill router").version("0.2.0");

program
  .command("index")
  .description("Scan SKILL.md files and (re)build the Neo4j graph")
  .option("--limit <n>", "Limit how many skills are processed")
  .option("--workers <n>", "Parallel LLM extraction workers (default 5)", "5")
  .option("--roots <paths...>", "Scan only these root dirs (bypasses default_roots)")
  .option("--incremental", "Only index skills not already in the graph")
  .option(
    "--scopes <csv>",
    "Comma-separated scopes to include: user,project,plugin or all (default user,project)",
    "user,project",
  )
  .action(async (opts) => {
    try {
      await cmdIndex(opts);
    } catch (exc) {
      console.error(`skillogy index failed: ${(exc as Error).message}`);
      process.exit(1);
    }
  });

program
  .command("serve")
  .description("Start the web API server")
  .option("--port <n>", "Port (default 8765)", process.env.SKILLOGY_WEB_PORT ?? "8765")
  .action(async (opts: { port: string }) => {
    const { startServer } = await import("../adapters/web_api.js");
    await startServer(parseInt(opts.port, 10));
  });

program
  .command("mcp")
  .description("Start the MCP server (stdio)")
  .action(async () => {
    const { startMcp } = await import("../adapters/mcp_server.js");
    await startMcp();
  });

program.parseAsync(process.argv).catch((exc) => {
  console.error(`skillogy: ${(exc as Error).message}`);
  process.exit(1);
});
