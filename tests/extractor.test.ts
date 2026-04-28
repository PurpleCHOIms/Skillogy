import { describe, expect, it } from "vitest";

import { extract } from "../src/core/extractor.js";
import type { LLMClient } from "../src/infra/llm.js";
import type { ParsedSkill } from "../src/domain/types.js";

function makeParsed(): ParsedSkill {
  return {
    name: "rag-builder",
    description: "Build RAG pipelines",
    body: "This skill helps you build RAG pipelines using chroma and langchain.",
    sourcePath: "/tmp/rag-builder/SKILL.md",
    rawFrontmatter: {},
    scope: "user",
    warnings: [],
  };
}

class FakeLLM implements LLMClient {
  constructor(private readonly response: string) {}
  async complete(): Promise<string> {
    return this.response;
  }
}

describe("extract", () => {
  it("parses valid LLM JSON into TriggerSurface", async () => {
    const llm = new FakeLLM(
      JSON.stringify({
        intents: ["build a rag pipeline", "Set up retrieval"],
        signals: [
          { kind: "keyword", value: "rag" },
          { kind: "tool_name", value: "chromadb" },
          { kind: "BOGUS", value: "x" },
        ],
        exclusions: [],
        related_skills: ["langchain-fundamentals", "RAG-BUILDER", "rag-builder"],
      }),
    );
    const surface = await extract(makeParsed(), llm);
    expect(surface.skillName).toBe("rag-builder");
    expect(surface.intents).toEqual(["build a rag pipeline", "set up retrieval"]);
    expect(surface.signals.map((s) => s.value)).toEqual(["rag", "chromadb"]);
    // Self-reference dropped + dedup
    expect(surface.relatedSkills).toEqual(["langchain-fundamentals"]);
  });

  it("strips markdown fences", async () => {
    const llm = new FakeLLM(
      "```json\n{\"intents\":[\"x\"],\"signals\":[],\"exclusions\":[],\"related_skills\":[]}\n```",
    );
    const surface = await extract(makeParsed(), llm);
    expect(surface.intents).toEqual(["x"]);
  });

  it("returns empty surface + warning on bad JSON", async () => {
    const llm = new FakeLLM("not json at all");
    const surface = await extract(makeParsed(), llm);
    expect(surface.intents).toEqual([]);
    expect(surface.extractionWarnings.length).toBeGreaterThan(0);
  });

  it("returns empty surface on LLM error", async () => {
    class ErrLLM implements LLMClient {
      async complete(): Promise<string> {
        throw new Error("network down");
      }
    }
    const surface = await extract(makeParsed(), new ErrLLM());
    expect(surface.intents).toEqual([]);
    expect(surface.extractionWarnings[0]).toContain("LLM call failed");
  });
});
