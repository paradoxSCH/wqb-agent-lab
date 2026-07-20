from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .ablation import evaluate_ablation, summarize_run_dir


REQUIRED_VARIANTS = ("baseline", "behavioral_proxy_only", "memory_only", "full_agent")


def build_ablation_suite(variant_paths: Mapping[str, Path | str]) -> dict[str, Any]:
    loaded: dict[str, list[Mapping[str, Any]]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for name, raw_path in variant_paths.items():
        path = Path(raw_path)
        summaries = _load_variant(path)
        loaded[name] = summaries
        metadata[name] = _variant_metadata(path, summaries)

    missing = [name for name in REQUIRED_VARIANTS if name not in loaded]
    fairness = _fairness(metadata, missing)
    report = evaluate_ablation(loaded)
    report["fairness"] = fairness
    return {
        "required_variants": list(REQUIRED_VARIANTS),
        "variants": {name: {"path": meta["path"], "runs": loaded[name]} for name, meta in metadata.items()},
        "fairness": fairness,
        "report": report,
    }


def select_ablation_candidates(runs_root: Path | str) -> dict[str, Path]:
    root = Path(runs_root)
    candidates: dict[str, list[Path]] = {name: [] for name in REQUIRED_VARIANTS}
    eligible_runs: list[Path] = []
    if not root.exists():
        return {}
    for run_dir in root.iterdir():
        if not run_dir.is_dir() or not (run_dir / "daily_budget_ledger.json").exists():
            continue
        eligible_runs.append(run_dir)
        classification = _classify_run(run_dir)
        candidates[classification].append(run_dir)
    selected: dict[str, Path] = {}
    for name, paths in candidates.items():
        if paths:
            selected[name] = sorted(paths, key=_run_sort_key, reverse=True)[0]
    if "baseline" not in selected:
        baseline_fallbacks = [
            run_dir
            for run_dir in eligible_runs
            if not (run_dir / "memory_sync_report.json").exists()
            and not (run_dir / "decision_attribution.json").exists()
            and "daily-budget" in run_dir.name.lower()
        ]
        if baseline_fallbacks:
            selected["baseline"] = sorted(baseline_fallbacks, key=_baseline_sort_key, reverse=True)[0]
    return selected


def write_ablation_suite(output_dir: Path | str, variant_paths: Mapping[str, Path | str]) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    suite = build_ablation_suite(variant_paths)
    report = suite["report"]
    (output / "ablation_suite.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (output / "ablation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (output / "summary.md").write_text(_summary_markdown(suite), encoding="utf-8")
    return suite


def _load_variant(path: Path) -> list[Mapping[str, Any]]:
    if path.is_dir():
        return [summarize_run_dir(path)]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        runs = payload.get("runs")
        if isinstance(runs, list):
            return [item for item in runs if isinstance(item, dict)]
        return [payload]
    return []


def _variant_metadata(path: Path, summaries: list[Mapping[str, Any]]) -> dict[str, Any]:
    ledger = _read_json(path / "daily_budget_ledger.json", {}) if path.is_dir() else {}
    summary = summaries[0] if summaries else {}
    return {
        "path": path.as_posix(),
        "date": ledger.get("date") if isinstance(ledger, dict) else None,
        "daily_budget": ledger.get("daily_budget") if isinstance(ledger, dict) else summary.get("simulations"),
        "run_tag": ledger.get("daily_run_tag", path.name) if isinstance(ledger, dict) else path.stem,
    }


def _fairness(metadata: Mapping[str, Mapping[str, Any]], missing: list[str]) -> dict[str, Any]:
    dates = {meta.get("date") for meta in metadata.values() if meta.get("date")}
    budgets = {
        value
        for meta in metadata.values()
        if isinstance((value := meta.get("daily_budget")), (int, float))
        and not isinstance(value, bool)
    }
    warnings: list[str] = []
    if missing:
        warnings.append(f"missing variants: {', '.join(missing)}")
    if len(dates) > 1:
        warnings.append("different date values across variants")
    if len(budgets) > 1:
        warnings.append("different daily_budget values across variants")
    comparison_type = "controlled" if not missing and len(dates) <= 1 and len(budgets) <= 1 else "observational"
    return {
        "comparison_type": comparison_type,
        "missing_variants": missing,
        "variant_dates": sorted(str(value) for value in dates),
        "variant_budgets": sorted(int(value) for value in budgets),
        "warnings": warnings,
    }


def _classify_run(run_dir: Path) -> str:
    has_memory = (run_dir / "memory_sync_report.json").exists()
    has_decision = (run_dir / "decision_attribution.json").exists()
    has_snapshot = (run_dir / "scan_results_snapshot.json").exists()
    has_behavioral = "behavioral" in run_dir.name.lower() or _ledger_mentions_behavioral(run_dir / "daily_budget_ledger.json")
    if has_decision or (has_memory and has_behavioral):
        return "full_agent"
    if has_memory:
        return "memory_only"
    if has_snapshot and any(marker in run_dir.name.lower() for marker in ("deepseek", "replay", "memory")):
        return "memory_only"
    if has_behavioral:
        return "behavioral_proxy_only"
    return "baseline"


def _ledger_mentions_behavioral(path: Path) -> bool:
    payload = _read_json(path, {})
    return "behavioral" in json.dumps(payload, ensure_ascii=False).lower()


def _run_sort_key(run_dir: Path) -> tuple[int, str, str]:
    ledger = _read_json(run_dir / "daily_budget_ledger.json", {})
    date = ledger.get("date") if isinstance(ledger, dict) else ""
    simulations = _simulations_for_sort(run_dir, ledger)
    return (simulations, str(date or ""), run_dir.name)


def _baseline_sort_key(run_dir: Path) -> tuple[int, int, str, str]:
    ledger = _read_json(run_dir / "daily_budget_ledger.json", {})
    date = ledger.get("date") if isinstance(ledger, dict) else ""
    simulations = _simulations_for_sort(run_dir, ledger)
    kimi_priority = 1 if "kimi-daily-budget" in run_dir.name.lower() else 0
    return (kimi_priority, simulations, str(date or ""), run_dir.name)


def _simulations_for_sort(run_dir: Path, ledger: Any) -> int:
    if isinstance(ledger, dict):
        spent = ledger.get("spent_simulations")
        if isinstance(spent, int):
            return spent
    snapshot = _read_json(run_dir / "scan_results_snapshot.json", [])
    if isinstance(snapshot, list):
        return len(snapshot)
    return 0


def _summary_markdown(suite: Mapping[str, Any]) -> str:
    raw_fairness = suite.get("fairness")
    fairness: dict[str, Any] = raw_fairness if isinstance(raw_fairness, dict) else {}
    raw_report = suite.get("report")
    report: dict[str, Any] = raw_report if isinstance(raw_report, dict) else {}
    lines = [
        "# Agent Ablation Suite",
        "",
        f"Verdict: `{report.get('verdict', 'unknown')}`",
        f"Comparison: `{fairness.get('comparison_type', 'unknown')}`",
        "",
        "## Variants",
    ]
    raw_variants = suite.get("variants")
    variants: dict[str, Any] = raw_variants if isinstance(raw_variants, dict) else {}
    for name, payload in variants.items():
        path = payload.get("path") if isinstance(payload, dict) else ""
        lines.append(f"- `{name}` {path}")
    raw_warnings = fairness.get("warnings")
    warnings = raw_warnings if isinstance(raw_warnings, list) else []
    if warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
