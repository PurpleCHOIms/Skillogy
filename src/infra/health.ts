// Lightweight Neo4j health probe used by the UserPromptSubmit hook to
// distinguish four cold-start states with stable, user-friendly messages:
//   - Neo4j unreachable (docker not started, port busy, wrong URI)
//   - Neo4j reachable but auth failed (stale data volume vs new password)
//   - Neo4j reachable but graph empty (indexing still running)
//   - Neo4j reachable and populated (proceed with routing)

import type { Driver } from "neo4j-driver";

import { getDriver } from "./db.js";
import { raceBudget } from "./budget.js";

export type HealthStatus = "ready" | "indexing" | "down" | "auth_mismatch";

export interface HealthReport {
  status: HealthStatus;
  skillCount: number;
  errorMessage?: string;
}

const PROBE_CYPHER = "MATCH (s:Skill) RETURN count(s) AS n";

function looksLikeAuthFailure(err: Error): boolean {
  const msg = err.message ?? "";
  // Neo4j driver tags: Neo.ClientError.Security.Unauthorized
  // Generic phrasing covers older driver versions too.
  return (
    msg.includes("Unauthorized") ||
    msg.includes("authentication failure") ||
    msg.includes("authentication failed") ||
    msg.includes("AuthenticationRateLimit")
  );
}

export async function probeHealth(
  driver: Driver = getDriver(),
  timeoutMs = 1500,
): Promise<HealthReport> {
  try {
    const { records } = await raceBudget(
      driver.executeQuery(PROBE_CYPHER, {}),
      timeoutMs,
    );
    const raw = records[0]?.get("n");
    const count =
      typeof raw === "number"
        ? raw
        : Number((raw as { toNumber?: () => number })?.toNumber?.() ?? raw ?? 0);
    return {
      status: count > 0 ? "ready" : "indexing",
      skillCount: count,
    };
  } catch (exc) {
    const err = exc as Error;
    if (looksLikeAuthFailure(err)) {
      return { status: "auth_mismatch", skillCount: 0, errorMessage: err.message };
    }
    return {
      status: "down",
      skillCount: 0,
      errorMessage: err.message,
    };
  }
}
