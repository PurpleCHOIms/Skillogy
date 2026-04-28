import { describe, expect, it } from "vitest";
import { spawnSync } from "node:child_process";
import { join } from "node:path";

const HOOK = join(process.cwd(), "dist", "adapters", "hook.js");

function runHook(input: string, env: Record<string, string> = {}): { stdout: string; stderr: string; code: number | null } {
  const result = spawnSync("node", [HOOK], {
    input,
    encoding: "utf-8",
    env: { ...process.env, ...env },
    timeout: 10_000,
  });
  return { stdout: result.stdout, stderr: result.stderr, code: result.status };
}

describe("hook adapter (e2e via dist)", () => {
  it("returns passthrough JSON when SKILLOGY_DISABLE=1", () => {
    const result = runHook(JSON.stringify({ prompt: "anything" }), { SKILLOGY_DISABLE: "1" });
    expect(result.code).toBe(0);
    const parsed = JSON.parse(result.stdout);
    expect(parsed.hookSpecificOutput.hookEventName).toBe("UserPromptSubmit");
    expect(parsed.hookSpecificOutput.additionalContext).toBe("");
  });

  it("returns passthrough on empty prompt", () => {
    const result = runHook(JSON.stringify({ prompt: "   " }), { SKILLOGY_DISABLE: "1" });
    expect(result.code).toBe(0);
    const parsed = JSON.parse(result.stdout);
    expect(parsed.hookSpecificOutput.additionalContext).toBe("");
  });

  it("returns passthrough on malformed JSON", () => {
    const result = runHook("not json at all", { SKILLOGY_DISABLE: "1" });
    expect(result.code).toBe(0);
    const parsed = JSON.parse(result.stdout);
    expect(parsed.hookSpecificOutput.additionalContext).toBe("");
  });
});
