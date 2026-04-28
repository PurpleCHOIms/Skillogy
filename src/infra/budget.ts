// Time-budget helper used by the UserPromptSubmit hook so a slow LLM call can
// never blow past Claude Code's 10s hook timeout. The hook budgets the full
// routing call; on budget exhaustion it falls back to keyword-only routing
// with a tighter cap, then to passthrough.

export class BudgetExceeded extends Error {
  constructor(public readonly budgetMs: number) {
    super(`budget ${budgetMs}ms exceeded`);
    this.name = "BudgetExceeded";
  }
}

export function raceBudget<T>(work: Promise<T>, budgetMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new BudgetExceeded(budgetMs)), budgetMs);
    if (typeof timer === "object" && timer && "unref" in timer) {
      (timer as { unref: () => void }).unref();
    }
    work.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}
