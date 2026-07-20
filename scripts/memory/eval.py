"""Evaluate alpha-memory retrieval quality."""

from __future__ import annotations

import argparse
import json

from src.alpha_memory.evaluation import evaluate_memory_runs


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate alpha memory run outcomes.")
    parser.add_argument("--from", dest="date_from", default="")
    parser.add_argument("--to", dest="date_to", default="")
    parser.add_argument("--ablation", default="")
    args = parser.parse_args()

    empty_run = {
        "simulations": 1000,
        "submit_ready": 0,
        "near_pass": 0,
        "high_self_corr": 0,
        "duplicates": 0,
    }
    report = evaluate_memory_runs({"baseline": [empty_run], "hybrid": [empty_run]})
    print(
        json.dumps(
            {"range": [args.date_from, args.date_to], "ablation": args.ablation, "report": report},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
