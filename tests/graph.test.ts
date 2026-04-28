import { describe, expect, it } from "vitest";

import { buildGraph } from "../src/core/graph.js";
import type { TriggerSurface } from "../src/domain/types.js";

class FakeDriver {
  public queries: Array<{ query: string; params?: unknown }> = [];
  async executeQuery(query: string, params?: unknown) {
    this.queries.push({ query, params });
    return { records: [] };
  }
}

describe("buildGraph", () => {
  it("creates Skill, Intent, Signal nodes and TRIGGERED_BY/EXCLUDED_BY edges", async () => {
    const surface: TriggerSurface = {
      skillName: "rag-builder",
      intents: ["build a rag system"],
      signals: [{ kind: "keyword", value: "rag" }],
      exclusions: [{ kind: "keyword", value: "html-only" }],
      relatedSkills: ["langchain-fundamentals"],
      extractionCostUsd: 0,
      extractionWarnings: [],
    };
    const driver = new FakeDriver();
    const counts = await buildGraph([surface], { driver: driver as any });
    expect(counts.skills).toBe(1);
    expect(counts.intents).toBe(1);
    expect(counts.signals).toBe(1);
    expect(counts.exclusions).toBe(1);
    expect(counts.related_to).toBe(1);
  });

  it("clears graph first when clearFirst=true", async () => {
    const driver = new FakeDriver();
    await buildGraph([], { driver: driver as any, clearFirst: true });
    expect(driver.queries[0]?.query).toContain("DETACH DELETE");
  });

  it("skips RELATES_TO self-references", async () => {
    const surface: TriggerSurface = {
      skillName: "self",
      intents: [],
      signals: [],
      exclusions: [],
      relatedSkills: ["self"],
      extractionCostUsd: 0,
      extractionWarnings: [],
    };
    const driver = new FakeDriver();
    const counts = await buildGraph([surface], { driver: driver as any });
    expect(counts.related_to).toBe(0);
  });
});
