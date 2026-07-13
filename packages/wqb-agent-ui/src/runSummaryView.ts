export interface RunSummaryContract {
  run_id: string;
  mode: "dry_run" | "live";
  budget: {
    planned: number;
    used: number;
    remaining: number;
  };
  counters: Record<string, number>;
  artifacts: string[];
}

export interface RunSummaryViewModel {
  contract: "run_summary";
  runId: string;
  modeLabel: string;
  budgetPlanned: number;
  budgetUsed: number;
  budgetRemaining: number;
  candidates: number;
  simulations: number;
  submitReady: number;
  artifactLinks: string[];
}

export function toRunSummaryViewModel(summary: RunSummaryContract): RunSummaryViewModel {
  return {
    contract: "run_summary",
    runId: summary.run_id,
    modeLabel: summary.mode === "live" ? "Live" : "Dry Run",
    budgetPlanned: summary.budget.planned,
    budgetUsed: summary.budget.used,
    budgetRemaining: summary.budget.remaining,
    candidates: summary.counters.candidates ?? 0,
    simulations: summary.counters.simulations ?? 0,
    submitReady: summary.counters.submit_ready ?? 0,
    artifactLinks: [...summary.artifacts],
  };
}
