import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getLlmClient } from "../src/infra/llm.js";

const SAVED_ENV = { ...process.env };

beforeEach(() => {
  delete process.env.SKILLOGY_LLM;
  delete process.env.GOOGLE_API_KEY;
  delete process.env.ANTHROPIC_API_KEY;
  delete process.env.SKILLOGY_FORCE_API_KEY;
});

afterEach(() => {
  process.env = { ...SAVED_ENV };
});

describe("getLlmClient", () => {
  it("throws when nothing is available and SKILLOGY_FORCE_API_KEY blocks SDK fallback", async () => {
    process.env.SKILLOGY_FORCE_API_KEY = "1";
    await expect(getLlmClient()).rejects.toThrow(/No LLM auth available/);
  });

  it("respects SKILLOGY_LLM=api requiring ANTHROPIC_API_KEY", async () => {
    process.env.SKILLOGY_LLM = "api";
    await expect(getLlmClient()).rejects.toThrow(/ANTHROPIC_API_KEY/);
  });

  it("falls back to SDK when claude-agent-sdk is importable", async () => {
    // claude-agent-sdk is installed in node_modules; SDKClient should construct fine
    const client = await getLlmClient();
    expect(typeof client.complete).toBe("function");
  });
});
