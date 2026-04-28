// Lightweight Neo4j health probe used by the UserPromptSubmit hook to
// distinguish three cold-start states with stable, user-friendly messages:
//   - Neo4j unreachable (docker not started, port busy, wrong URI)
//   - Neo4j reachable but graph empty (indexing still running)
//   - Neo4j reachable and populated (proceed with routing)

import type { Driver } from "neo4j-driver";

import { getDriver } from "./db.js";
import { raceBudget } from "./budget.js";

export type HealthStatus = "ready" | "indexing" | "down";

export interface HealthReport {
  status: HealthStatus;
  skillCount: number;
  errorMessage?: string;
}

const PROBE_CYPHER = "MATCH (s:Skill) RETURN count(s) AS n";

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
    return {
      status: "down",
      skillCount: 0,
      errorMessage: (exc as Error).message,
    };
  }
}
