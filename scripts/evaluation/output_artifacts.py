"""Evaluate completed run artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from wqb_agent_lab.evaluation.output.evaluator import write_run_output_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate run output artifacts and write output evaluation reports.")
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()

    report_path, summary_path = write_run_output_evaluation(args.run_dir)
    print(f"output_evaluation_report: {report_path}")
    print(f"output_evaluation_summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
