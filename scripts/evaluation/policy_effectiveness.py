"""Evaluate research-policy effectiveness."""

from __future__ import annotations

import argparse
from pathlib import Path

from wqb_agent_lab.evaluation.policy_effectiveness import (
    write_policy_effectiveness_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate policy effectiveness from decision attribution.")
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()

    path = write_policy_effectiveness_report(args.run_dir)
    print(f"policy_effectiveness_report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
