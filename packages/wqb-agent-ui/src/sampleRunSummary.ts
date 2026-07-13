import type { RunSummaryContract } from "./runSummaryView";

export const sampleRunSummary: RunSummaryContract = {
  run_id: "local-readonly-sample",
  mode: "dry_run",
  budget: {
    planned: 1000,
    used: 0,
    remaining: 1000,
  },
  counters: {
    candidates: 12,
    simulations: 0,
    submit_ready: 0,
  },
  artifacts: [
    "data/runs/example/triage_summary.md",
    "data/runs/example/output_evaluation_report.json",
    "schemas/run_summary.schema.json",
  ],
};
