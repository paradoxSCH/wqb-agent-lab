"""Build behavioral candidate-generation inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wqb_agent_lab.research.candidates import write_candidate_generation_artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description="Build behavioral candidate generation artifacts.")
    parser.add_argument("--fields", default=".local/data/all_wqb_fields.json", help="Path to WQB field JSON.")
    parser.add_argument(
        "--output-dir",
        default=".local/data/behavioral_candidate_generation",
        help="Directory for generated behavioral candidate artifacts.",
    )
    parser.add_argument(
        "--output-evaluation-report",
        type=Path,
        help="Optional previous run output_evaluation_report.json used as policy feedback.",
    )
    parser.add_argument(
        "--policy-feedback-mode",
        choices=("off", "shadow", "advisory", "control"),
        default="shadow",
        help="Apply feedback as annotations by default; control may alter action lanes.",
    )
    args = parser.parse_args()

    fields = _load_fields(Path(args.fields))
    policy_feedback = _load_policy_feedback(args.output_evaluation_report)
    written = write_candidate_generation_artifacts(
        fields,
        Path(args.output_dir),
        policy_feedback=policy_feedback,
        policy_feedback_mode=args.policy_feedback_mode,
    )

    for key, path in written.items():
        print(f"{key}: {path}")
    return 0


def _load_fields(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        fields = payload.get("fields") or payload.get("results") or payload.get("data")
        if isinstance(fields, list):
            return [row for row in fields if isinstance(row, dict)]
    raise ValueError(f"Unsupported field payload: {path}")


def _load_policy_feedback(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
