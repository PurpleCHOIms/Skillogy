#!/usr/bin/env node
/**
 * UserPromptSubmit hook entry — invoked by Claude Code per turn.
 *
 * Reads JSON from stdin per Claude Code hook protocol; emits hookSpecificOutput
 * with additionalContext containing a one-line skill hint Claude Code will load
 * via its native Skill tool.
 *
 * Honors:
 *   SKILLOGY_DISABLE=1   → passthrough (no injection)
 *   SKILLOGY_MIN_SCORE   → minimum router score (default 0.4)
 */

import { pathToFileURL } from "node:url";

import { closeDriver, getDriver } from "../infra/db.js";
import { Router } from "../core/router.js";
import { BudgetExceeded, raceBudget } from "../infra/budget.js";
import { probeHealth, type HealthReport } from "../infra/health.js";
import { patchState, readState, type UserState } from "../infra/userState.js";
import type { RoutingResult } from "../domain/types.js";

interface HookInput {
  session_id?: string;
  transcript_path?: string;
  cwd?: string;
  permission_mode?: string;
  hook_event_name?: string;
  prompt?: string;
}

export interface RouterLike {
  findSkill(opts: {
    query: string;
    topK?: number;
    judge?: boolean;
    extract?: boolean;
    loadBody?: boolean;
  }): Promise<RoutingResult>;
}

export interface RoutingOutcome {
  result: RoutingResult;
  mode: "primary" | "fallback";
}

export interface ColdStartDecision {
  // additionalContext text to emit (empty string = silent passthrough)
  contextText: string;
  stateUpdate: Partial<UserState>;
  // True when Skillogy is healthy enough to attempt routing.
  proceedWithRouting: boolean;
}

/**
 * Decide how the hook should respond when health is checked. Pure function so
 * tests can exhaustively cover the cold-start state machine.
 *
 * Rules:
 *   - status "ready" → proceed; clear any prior notification flags so the
 *     next degradation can re-notify.
 *   - status "indexing" (Neo4j up, graph empty) → emit a one-time hint, then
 *     stay silent until the graph is populated.
 *   - status "down" + dockerMissing → emit one-time docker hint.
 *   - status "down" otherwise → emit one-time "Skillogy is starting" hint.
 *   - In all "down/indexing" branches, downstream routing is skipped.
 */
export function decideColdStartUx(
  report: HealthReport,
  state: UserState,
  indexLogPath = "/tmp/skillogy/index.log",
): ColdStartDecision {
  if (report.status === "ready") {
    const clears: Partial<UserState> = {};
    if (state.notifiedNeo4jDown) clears.notifiedNeo4jDown = false;
    if (state.notifiedIndexingPending) clears.notifiedIndexingPending = false;
    if (state.notifiedDockerMissing) clears.notifiedDockerMissing = false;
    return { contextText: "", stateUpdate: clears, proceedWithRouting: true };
  }

  if (report.status === "indexing") {
    if (state.notifiedIndexingPending) {
      return { contextText: "", stateUpdate: {}, proceedWithRouting: false };
    }
    const text = [
      "[skillogy] Skill graph is still being indexed in the background.",
      `Routing improvements will activate as skills land. Tail \`${indexLogPath}\` for progress, or run /skillogy:status.`,
      "",
    ].join("\n");
    return {
      contextText: text,
      stateUpdate: { notifiedIndexingPending: true },
      proceedWithRouting: false,
    };
  }

  // status === "down"
  if (state.dockerMissing) {
    if (state.notifiedDockerMissing) {
      return { contextText: "", stateUpdate: {}, proceedWithRouting: false };
    }
    const text = [
      "[skillogy] Docker not found, so Neo4j cannot auto-start.",
      "Install Docker (https://docs.docker.com/get-docker/) OR set NEO4J_URI to your existing Neo4j instance.",
      "Until then Skillogy stays in passthrough mode (your prompts are unaffected).",
      "",
    ].join("\n");
    return {
      contextText: text,
      stateUpdate: { notifiedDockerMissing: true },
      proceedWithRouting: false,
    };
  }

  if (state.notifiedNeo4jDown) {
    return { contextText: "", stateUpdate: {}, proceedWithRouting: false };
  }
  const text = [
    "[skillogy] Skillogy is starting up — Neo4j is not reachable yet.",
    "It usually takes ~10-30s the first time. The next prompt will use it once ready. Run /skillogy:status to check.",
    "",
  ].join("\n");
  return {
    contextText: text,
    stateUpdate: { notifiedNeo4jDown: true },
    proceedWithRouting: false,
  };
}

/**
 * Run router.findSkill with a hard time budget. On budget exhaustion, fall
 * back to a tighter keyword-only call. Returns null if both attempts fail or
 * throw — caller should emit passthrough in that case.
 *
 * Exported so tests can drive the budgeting logic with stub routers.
 */
export async function routeWithBudgets(
  router: RouterLike,
  query: string,
  budgetMs: number,
  fallbackMs: number,
  log: (line: string) => void = (line) => process.stderr.write(line),
): Promise<RoutingOutcome | null> {
  try {
    const result = await raceBudget(
      router.findSkill({ query, topK: 3, judge: true, extract: true, loadBody: false }),
      budgetMs,
    );
    return { result, mode: "primary" };
  } catch (exc) {
    if (!(exc instanceof BudgetExceeded)) {
      log(`[skillogy hook] routing failed: ${(exc as Error).message}\n`);
      return null;
    }
    log(
      `[skillogy hook] LLM budget exceeded (${budgetMs}ms), fell back to keyword routing\n`,
    );
    try {
      const result = await raceBudget(
        router.findSkill({
          query,
          topK: 1,
          judge: false,
          extract: false,
          loadBody: false,
        }),
        fallbackMs,
      );
      return { result, mode: "fallback" };
    } catch {
      return null;
    }
  }
}

function emitContext(additionalContext: string): void {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "UserPromptSubmit",
        additionalContext,
      },
    }),
  );
}

function emitPassthrough(): void {
  emitContext("");
}

async function readStdin(): Promise<string> {
  return await new Promise((resolve) => {
    let data = "";
    process.stdin.setEncoding("utf-8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", () => resolve(data));
    // If no stdin attached after a tick, resolve empty
    setTimeout(() => resolve(data), 5_000);
  });
}

async function main(): Promise<number> {
  if (process.env.SKILLOGY_DISABLE === "1") {
    emitPassthrough();
    return 0;
  }

  const raw = await readStdin();
  let payload: HookInput;
  try {
    payload = JSON.parse(raw) as HookInput;
  } catch {
    emitPassthrough();
    return 0;
  }

  const prompt = (payload.prompt ?? "").trim();
  if (!prompt) {
    emitPassthrough();
    return 0;
  }

  const minScore = parseFloat(process.env.SKILLOGY_MIN_SCORE ?? "0.4");
  const budgetMs = parseInt(process.env.SKILLOGY_HOOK_BUDGET_MS ?? "8000", 10);
  const fallbackMs = parseInt(process.env.SKILLOGY_HOOK_FALLBACK_MS ?? "1500", 10);
  const probeMs = parseInt(process.env.SKILLOGY_HEALTH_PROBE_MS ?? "1500", 10);

  // Cold-start UX: probe Neo4j + skill count first so we can emit a one-time
  // diagnostic when Skillogy is not actually ready instead of silently failing.
  const driver = getDriver();
  const report = await probeHealth(driver, probeMs);
  const decision = decideColdStartUx(report, readState());
  if (Object.keys(decision.stateUpdate).length > 0) {
    try {
      patchState(decision.stateUpdate);
    } catch {
      // state file is best-effort; never fail the hook on persistence issues
    }
  }
  if (!decision.proceedWithRouting) {
    emitContext(decision.contextText);
    await closeDriver().catch(() => {});
    return 0;
  }

  const router = new Router(driver);
  const outcome = await routeWithBudgets(router, prompt, budgetMs, fallbackMs);
  if (!outcome) {
    emitPassthrough();
    await closeDriver().catch(() => {});
    return 0;
  }
  const result = outcome.result;

  if (!result.skillName || result.score < minScore) {
    emitPassthrough();
    await closeDriver().catch(() => {});
    return 0;
  }

  const relatedNames = result.alternatives
    .filter((a) => a.via === "relates_to" && typeof a.name === "string")
    .slice(0, 2)
    .map((a) => a.name);

  const lines = [
    `[skillogy] Relevant skill detected: \`${result.skillName}\` (score=${result.score.toFixed(2)})`,
    `Load it with: Skill({ skill: "${result.skillName}" })`,
  ];
  if (relatedNames.length > 0) {
    lines.push(`Related skills: ${relatedNames.map((r) => "`" + r + "`").join(", ")}`);
  }
  const additionalContext = lines.join("\n") + "\n";

  emitContext(additionalContext);
  await closeDriver().catch(() => {});
  return 0;
}

// Run main() only when invoked directly (`node dist/adapters/hook.js`); when
// imported by tests we don't want a stray stdin-consuming process.exit() to
// fire and confuse the test runner.
const invokedAsScript =
  process.argv[1] !== undefined &&
  import.meta.url === pathToFileURL(process.argv[1]).href;

if (invokedAsScript) {
  main()
    .then((code) => process.exit(code))
    .catch((exc) => {
      process.stderr.write(`[skillogy hook] fatal: ${(exc as Error).message}\n`);
      emitPassthrough();
      process.exit(0);
    });
}
