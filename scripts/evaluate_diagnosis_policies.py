from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.diagnosis_policy import evaluate_diagnosis_policies


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate diagnosis policy effectiveness for a run.")
    parser.add_argument("--run-dir", required=True, help="Run directory containing scan_results_snapshot.json.")
    parser.add_argument("--input", default="scan_results_snapshot.json", help="Input JSON file inside run-dir.")
    parser.add_argument("--output", default="diagnosis_policy_evaluation.json", help="Output JSON file inside run-dir.")
    parser.add_argument("--summary", default="diagnosis_policy_evaluation.md", help="Output Markdown summary inside run-dir.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    rows = _read_rows(run_dir / args.input)
    report = evaluate_diagnosis_policies(rows)
    output_path = run_dir / args.output
    summary_path = run_dir / args.summary
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary_path.write_text(_summary_markdown(report), encoding="utf-8")
    print(f"diagnosis_policy_evaluation: {output_path}")
    print(f"diagnosis_policy_summary: {summary_path}")
    return 0


def _read_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("rows") or payload.get("results") or payload.get("data")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    raise ValueError(f"Unsupported diagnosis input: {path}")


def _summary_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Diagnosis Policy Evaluation",
        "",
        f"Generated at: `{report.get('generated_at')}`",
        f"Rows: `{report.get('total_rows')}`",
        f"Diagnoses: `{report.get('total_diagnoses')}`",
        f"Budget saved estimate: `{report.get('budget_saved_estimate')}`",
        "",
        "## Policies",
    ]
    for policy in report.get("policies", []):
        lines.extend(
            [
                "",
                f"### `{policy.get('diagnosis_type')}`",
                f"- Policy: `{policy.get('recommended_policy')}`",
                f"- Budget: `{policy.get('budget_policy')}`",
                f"- Observed: `{policy.get('observed_count')}`",
                f"- Repair rate: `{policy.get('repair_candidate_rate')}`",
                f"- Blocked rate: `{policy.get('blocked_rate')}`",
                f"- Success metric: `{policy.get('success_metric')}`",
                f"- Failure metric: `{policy.get('failure_metric')}`",
                f"- Next action: {policy.get('next_action')}",
            ]
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
