from __future__ import annotations

import argparse
from pathlib import Path

from src.self_corr_repair import write_bucket_aware_next_scan_artifacts, write_self_corr_repair_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a value-bucketed SELF_CORRELATION repair scan from a completed run.")
    parser.add_argument("--workspace-root", default=".", help="Repository root.")
    parser.add_argument("--run-dir", required=True, help="Run directory containing scan_results_snapshot.json.")
    parser.add_argument("--bucket-aware-next", action="store_true", help="Build mixed repair/replacement next scan.")
    parser.add_argument("--target-count", type=int, default=20, help="Maximum candidates for --bucket-aware-next.")
    args = parser.parse_args()

    root = Path(args.workspace_root).resolve()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = root / run_dir

    if args.bucket_aware_next:
        result = write_bucket_aware_next_scan_artifacts(root, run_dir, target_count=args.target_count)
        print(f"review_path={result['review_path']}")
        print(f"scan_config_path={result['scan_config_path']}")
        print(f"candidate_count={result['candidate_count']}")
        return

    result = write_self_corr_repair_artifacts(root, run_dir)
    print(f"review_path={result['review_path']}")
    print(f"scan_config_path={result['scan_config_path']}")
    print(f"repair_candidate_count={result['repair_candidate_count']}")


if __name__ == "__main__":
    main()
