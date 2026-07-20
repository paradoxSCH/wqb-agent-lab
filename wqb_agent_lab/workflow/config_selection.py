from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .artifacts import read_json, relative_path


def pick_scan_config(
    root: Path,
    *,
    configs_root: Path,
    runs_root: Path,
    run_date: date,
) -> str | None:
    """Choose a stale or productive scan config without mutating workflow state."""
    configs = sorted((root / configs_root).glob("*/scan_config_round*.json"))
    config_scores: dict[str, tuple[float, int]] = {}
    for config_path in configs:
        relative = relative_path(config_path, root)
        last_used: date | None = None
        total_yield = 0

        for ledger_path in (root / runs_root).glob("*/daily_budget_ledger.json"):
            ledger = read_json(ledger_path, {})
            if not isinstance(ledger, dict):
                continue
            if relative not in (ledger.get("queued_scan_configs") or []):
                continue
            try:
                observed_date = datetime.strptime(
                    str(ledger.get("date") or ""), "%Y-%m-%d"
                ).date()
            except ValueError:
                continue
            if last_used is None or observed_date > last_used:
                last_used = observed_date
            closed_loop = ledger.get("closed_loop")
            counts = closed_loop.get("counts") if isinstance(closed_loop, dict) else None
            if isinstance(counts, dict):
                total_yield += int(counts.get("submit_ready") or 0)

        if last_used is None:
            score = 1000.0
        else:
            days_since = (run_date - last_used).days
            score = -500.0 if days_since <= 1 else days_since * 50.0 + total_yield * 20.0
        try:
            file_size = config_path.stat().st_size
        except OSError:
            file_size = 0
        config_scores[relative] = (score, file_size)

    if not config_scores:
        return None
    best = max(config_scores, key=lambda key: config_scores[key])
    return best if config_scores[best][0] >= 0 else None
