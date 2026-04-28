import { describe, expect, it } from "vitest";

import { BudgetExceeded, raceBudget } from "../src/infra/budget.js";

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

describe("raceBudget", () => {
  it("resolves with the work value when it finishes inside the budget", async () => {
    const result = await raceBudget(
      (async () => {
        await sleep(20);
        return 42;
      })(),
      200,
    );
    expect(result).toBe(42);
  });

  it("rejects with BudgetExceeded when work overruns the budget", async () => {
    const start = Date.now();
    await expect(
      raceBudget(
        (async () => {
          await sleep(500);
          return "late";
        })(),
        100,
      ),
    ).rejects.toBeInstanceOf(BudgetExceeded);
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(300);
  });

  it("propagates the underlying rejection if work fails before the budget", async () => {
    await expect(
      raceBudget(Promise.reject(new Error("boom")), 200),
    ).rejects.toThrow("boom");
  });
});
