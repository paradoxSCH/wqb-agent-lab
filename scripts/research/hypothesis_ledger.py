"""Inspect and maintain the research hypothesis ledger."""

from __future__ import annotations

import argparse
import json

from src.alpha_memory.hypothesis import HypothesisDraft, validate_hypothesis


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an alpha research hypothesis draft.")
    parser.add_argument("--run", default="")
    parser.add_argument("--behavior-thesis", default="")
    parser.add_argument("--mechanism", default="")
    parser.add_argument("--proxy", action="append", default=[])
    parser.add_argument("--operator-skeleton", action="append", default=[])
    parser.add_argument("--kill-condition", action="append", default=[])
    parser.add_argument("--success-criterion", action="append", default=[])
    args = parser.parse_args()

    draft = HypothesisDraft(
        behavior_thesis=args.behavior_thesis,
        mechanism=args.mechanism,
        proxies=args.proxy,
        operator_skeletons=args.operator_skeleton,
        kill_conditions=args.kill_condition,
        success_criteria=args.success_criterion,
    )
    result = validate_hypothesis(draft)
    print(json.dumps({"valid": result.ok, "missing_fields": result.missing_fields}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
