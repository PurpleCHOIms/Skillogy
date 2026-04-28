import { describe, expect, it } from "vitest";

import { Router } from "../src/core/router.js";
import type { LLMClient } from "../src/infra/llm.js";

interface FakeRecord {
  get: (key: string) => unknown;
}

function makeRecords(rows: Array<Record<string, unknown>>): FakeRecord[] {
  return rows.map((row) => ({
    get: (key: string) => row[key],
  }));
}

class FakeDriver {
  public lastQuery = "";
  public lastParams: unknown = undefined;
  constructor(
    private readonly responses: Array<{ records: FakeRecord[] }>,
  ) {}
  async executeQuery(query: string, params?: unknown) {
    this.lastQuery = query;
    this.lastParams = params;
    const next = this.responses.shift();
    return next ?? { records: [] };
  }
}

class FakeLLM implements LLMClient {
  constructor(private readonly responses: string[]) {}
  async complete(): Promise<string> {
    return this.responses.shift() ?? "";
  }
}

describe("Router.findSkill", () => {
  it("returns empty result when no candidates match", async () => {
    const driver = new FakeDriver([
      { records: [] }, // collect_score
    ]);
    const llm = new FakeLLM([
      JSON.stringify({ intents: ["build x"], signals: [] }),
    ]);
    const router = new Router(driver as any, llm);
    const result = await router.findSkill({ query: "do something obscure", loadBody: false });
    expect(result.skillName).toBe("");
    expect(result.score).toBe(0);
  });

  it("picks the top-scoring candidate when judge=false", async () => {
    const driver = new FakeDriver([
      {
        records: makeRecords([
          { name: "alpha", description: "a", source_path: "", scope: "user", score: 5.0, hits: [] },
          { name: "beta", description: "b", source_path: "", scope: "user", score: 3.0, hits: [] },
        ]),
      },
      { records: [] }, // related neighbors
    ]);
    const llm = new FakeLLM([
      JSON.stringify({ intents: ["x"], signals: [{ kind: "keyword", value: "y" }] }),
    ]);
    const router = new Router(driver as any, llm);
    const result = await router.findSkill({
      query: "...",
      judge: false,
      loadBody: false,
      topK: 5,
    });
    expect(result.skillName).toBe("alpha");
    expect(result.score).toBe(5.0);
    expect(result.alternatives.map((a) => a.name)).toEqual(["beta"]);
  });

  it("appends related_to alternatives with via marker", async () => {
    const driver = new FakeDriver([
      {
        records: makeRecords([
          { name: "alpha", description: "a", source_path: "", scope: "user", score: 5.0, hits: [] },
        ]),
      },
      {
        records: makeRecords([
          { name: "gamma", description: "g", scope: "user" },
        ]),
      },
    ]);
    const llm = new FakeLLM([
      JSON.stringify({ intents: ["x"], signals: [] }),
    ]);
    const router = new Router(driver as any, llm);
    const result = await router.findSkill({
      query: "...",
      judge: false,
      loadBody: false,
    });
    expect(result.skillName).toBe("alpha");
    const related = result.alternatives.filter((a) => a.via === "relates_to");
    expect(related.map((r) => r.name)).toEqual(["gamma"]);
  });

  it("uses LLM judge when judge=true and rows>1", async () => {
    const driver = new FakeDriver([
      {
        records: makeRecords([
          { name: "alpha", description: "a", source_path: "", scope: "user", score: 5.0, hits: [] },
          { name: "beta", description: "b", source_path: "", scope: "user", score: 4.5, hits: [] },
        ]),
      },
      { records: [] },
    ]);
    const llm = new FakeLLM([
      JSON.stringify({ intents: ["x"], signals: [] }),
      JSON.stringify({ winner: "beta", reason: "better fit" }),
    ]);
    const router = new Router(driver as any, llm);
    const result = await router.findSkill({
      query: "...",
      judge: true,
      loadBody: false,
    });
    expect(result.skillName).toBe("beta");
  });
});
