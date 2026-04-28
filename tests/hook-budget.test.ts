import { describe, expect, it, vi } from "vitest";

import { routeWithBudgets, type RouterLike } from "../src/adapters/hook.js";
import type { RoutingResult } from "../src/domain/types.js";

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const fastResult = (name: string, score: number): RoutingResult => ({
  skillName: name,
  skillBody: "",
  reasoningPath: [],
  alternatives: [],
  score,
});

describe("routeWithBudgets", () => {
  it("returns the primary result when it completes inside the budget", async () => {
    const router: RouterLike = {
      findSkill: vi.fn(async () => fastResult("primary-skill", 0.9)),
    };
    const out = await routeWithBudgets(router, "build rag", 200, 100, () => {});
    expect(out).not.toBeNull();
    expect(out!.mode).toBe("primary");
    expect(out!.result.skillName).toBe("primary-skill");
    expect(router.findSkill).toHaveBeenCalledTimes(1);
  });

  it("falls back to keyword-only routing when primary exceeds the budget", async () => {
    let calls = 0;
    const router: RouterLike = {
      findSkill: vi.fn(async (opts) => {
        calls += 1;
        if (calls === 1) {
          // Primary: simulate slow LLM that blows the budget
          await sleep(500);
          return fastResult("never-returned", 0.1);
        }
        // Fallback: quick keyword routing
        expect(opts.extract).toBe(false);
        expect(opts.judge).toBe(false);
        expect(opts.topK).toBe(1);
        return fastResult("fallback-skill", 0.5);
      }),
    };

    const logged: string[] = [];
    const start = Date.now();
    const out = await routeWithBudgets(router, "build rag", 100, 200, (line) =>
      logged.push(line),
    );
    const elapsed = Date.now() - start;

    expect(out).not.toBeNull();
    expect(out!.mode).toBe("fallback");
    expect(out!.result.skillName).toBe("fallback-skill");
    expect(elapsed).toBeLessThan(800);
    expect(logged.some((l) => l.includes("budget exceeded"))).toBe(true);
    expect(router.findSkill).toHaveBeenCalledTimes(2);
  });

  it("returns null (passthrough signal) when both primary and fallback exceed their budgets", async () => {
    const router: RouterLike = {
      findSkill: vi.fn(async () => {
        await sleep(2000);
        return fastResult("nope", 0);
      }),
    };
    const start = Date.now();
    const out = await routeWithBudgets(router, "build rag", 50, 50, () => {});
    const elapsed = Date.now() - start;

    expect(out).toBeNull();
    // Total wall: ~budget + ~fallback; should be well under 1s with 50ms each
    expect(elapsed).toBeLessThan(800);
    expect(router.findSkill).toHaveBeenCalledTimes(2);
  });

  it("returns null when primary throws a non-budget error", async () => {
    const router: RouterLike = {
      findSkill: vi.fn(async () => {
        throw new Error("neo4j unreachable");
      }),
    };
    const logged: string[] = [];
    const out = await routeWithBudgets(router, "x", 200, 200, (line) => logged.push(line));
    expect(out).toBeNull();
    expect(logged.some((l) => l.includes("routing failed"))).toBe(true);
    // Should NOT have attempted fallback for non-budget errors
    expect(router.findSkill).toHaveBeenCalledTimes(1);
  });
});
