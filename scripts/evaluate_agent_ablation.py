from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.agent_evaluation import select_ablation_candidates, summarize_run_dir, write_ablation_suite, write_evaluation_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate WQB agent variants by outcome lift and complexity cost.")
    parser.add_argument(
        "--variant",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Variant input. PATH may be a run directory, a JSON run summary, or a JSON list of summaries.",
    )
    parser.add_argument("--output-dir", help="Directory for ablation_report.json and summary.md.")
    parser.add_argument("--auto-runs-root", help="Auto-select ablation variant candidates from a local runs root.")
    parser.add_argument("--suite-output-dir", help="Directory for ablation_suite.json, ablation_report.json, and summary.md.")
    parser.add_argument(
        "--allow-observational",
        action="store_true",
        help="Allow writing a suite when variants are historical/observational rather than controlled.",
    )
    args = parser.parse_args()

    if args.auto_runs_root:
        output_dir = args.suite_output_dir or args.output_dir
        if not output_dir:
            raise SystemExit("--suite-output-dir or --output-dir is required with --auto-runs-root.")
        candidates = select_ablation_candidates(args.auto_runs_root)
        suite = write_ablation_suite(output_dir, candidates)
        comparison_type = suite["fairness"]["comparison_type"]
        if comparison_type != "controlled" and not args.allow_observational:
            raise SystemExit("Auto-selected suite is observational; pass --allow-observational to write it intentionally.")
        report_path = Path(output_dir) / "ablation_report.json"
        suite_path = Path(output_dir) / "ablation_suite.json"
        print(f"verdict={suite['report']['verdict']} comparison_type={comparison_type} suite={suite_path} report={report_path}")
        return 0

    if not args.output_dir:
        raise SystemExit("--output-dir is required unless --suite-output-dir is used with --auto-runs-root.")
    variants = _load_variants(args.variant)
    report = write_evaluation_report(args.output_dir, variants)
    report_path = Path(args.output_dir) / "ablation_report.json"
    print(f"verdict={report['verdict']} report={report_path}")
    return 0


def _load_variants(raw_variants: Sequence[str]) -> dict[str, list[Mapping[str, Any]]]:
    if not raw_variants:
        raise SystemExit("At least one --variant NAME=PATH is required.")
    variants: dict[str, list[Mapping[str, Any]]] = {}
    for raw in raw_variants:
        if "=" not in raw:
            raise SystemExit(f"Invalid --variant value: {raw!r}. Expected NAME=PATH.")
        name, path_text = raw.split("=", 1)
        name = name.strip()
        if not name:
            raise SystemExit(f"Invalid --variant value: {raw!r}. Variant name is empty.")
        path = Path(path_text)
        variants[name] = _load_variant_path(path)
    return variants


def _load_variant_path(path: Path) -> list[Mapping[str, Any]]:
    if path.is_dir():
        return [summarize_run_dir(path)]
    if not path.exists():
        raise SystemExit(f"Variant path does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("runs"), list):
            return [item for item in payload["runs"] if isinstance(item, dict)]
        return [payload]
    raise SystemExit(f"Unsupported variant JSON shape: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
