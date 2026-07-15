"""Unified BRAIN scan runner — config-driven, no more one-off scripts.

Usage:
    python run_scan.py --config scan_config.json

scan_config.json schema:
    {
        "output": "data/scan_results.json",
        "candidates": [
            {
                "expression": "group_rank(operating_profit_before_depr_amort / cap, subindustry)",
                "settings": {
                    "instrumentType": "EQUITY",
                    "region": "USA",
                    "universe": "TOP3000",
                    "delay": 1,
                    "decay": 3,
                    "neutralization": "MARKET",
                    "truncation": 0.05,
                    "pasteurization": "ON",
                    "unitHandling": "VERIFY",
                    "nanHandling": "ON",
                    "language": "FASTEXPR",
                    "visualization": false
                },
                "note": "EBITDA annual pure signal, decay=3"
            }
        ],
        "param_sweeps": {
            "base_expression": "group_rank(operating_profit_before_depr_amort / cap, subindustry)",
            "base_settings": { ... },
            "sweeps": [
                {"axis": "decay", "values": [3, 4, 5, 6]},
                {"axis": "neutralization", "values": ["MARKET", "SUBINDUSTRY"]}
            ]
        }
    }
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import time
from pathlib import Path

from src.side_effect_governance import require_side_effect_capability
from wqb_agent_lab.platform import WQBClient, evaluate_check_snapshot


def is_pass(metrics: dict, checks: list[dict] | None = None) -> bool:
    check_readiness = evaluate_check_snapshot(checks)
    return (
        float(metrics.get("sharpe", 0.0) or 0.0) >= 1.25
        and float(metrics.get("fitness", 0.0) or 0.0) >= 1.0
        and float(metrics.get("turnover", 1.0) or 1.0) <= 0.7
        and check_readiness.ready
    )


async def explicit_checks(client: WQBClient, alpha_id: str) -> list[dict]:
    checks = await asyncio.to_thread(client.get_alpha_checks, alpha_id)
    return [check.to_dict() for check in checks]


def summarize_simulation_payload(payload: dict) -> str:
    if any(payload.get(key) for key in ("diagnosis", "error", "message", "detail", "status_code")):
        return f"Simulation failed: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    if payload.get("status"):
        return f"Simulation returned status: {payload['status']}"
    return "Simulation polling ended without an alpha"


def _settings_key(settings: dict) -> str:
    return json.dumps(settings, sort_keys=True, ensure_ascii=False)


def _normalized_expression(expression: str) -> str:
    return re.sub(r"\s+", "", expression)


def expand_param_sweeps(param_sweeps: dict | None) -> list[dict]:
    """Expand param sweep definitions into concrete candidate specs."""
    if not param_sweeps:
        return []

    base_expr = param_sweeps.get("base_expression", "")
    base_settings = param_sweeps.get("base_settings", {})
    sweeps = param_sweeps.get("sweeps", [])

    # Build Cartesian product of all sweep axes
    from itertools import product

    axes = []
    axis_names = []
    for sweep in sweeps:
        axis = sweep.get("axis", "")
        values = sweep.get("values", [])
        if axis and values:
            axes.append([(axis, v) for v in values])
            axis_names.append(axis)

    if not axes:
        return [{
            "expression": base_expr,
            "settings": base_settings,
            "note": param_sweeps.get("note", "param sweep"),
        }]

    candidates = []
    for combo in product(*axes):
        settings = copy.deepcopy(base_settings)
        expr = base_expr
        parts = []
        for axis, value in combo:
            if axis == "expression":
                expr = value
            else:
                settings[axis] = value
            parts.append(f"{axis}={value}")
        note = param_sweeps.get("note", "param sweep") + " " + ", ".join(parts)
        candidates.append({"expression": expr, "settings": settings, "note": note})

    return candidates


async def run_scan(config_path: str, cli_continue_on_pass: bool = False, max_concurrency: int | None = None) -> None:
    require_side_effect_capability("simulation")
    with open(config_path, "r", encoding="utf-8") as f:
        scan_cfg = json.load(f)

    # Config file can set continue_on_pass; CLI flag overrides it
    continue_on_pass = scan_cfg.get("continue_on_pass", False) or cli_continue_on_pass

    output_path = Path(scan_cfg.get("output", ".local/data/scan_results.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing results
    rows: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    if output_path.exists():
        try:
            rows = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows = []

    for row in rows:
        expression = row.get("expression", "")
        settings = row.get("settings", {})
        seen_keys.add((expression, _settings_key(settings)))

    # Build candidate list
    base_settings = scan_cfg.get("settings", {})
    candidates: list[dict] = []
    for item in scan_cfg.get("candidates", []):
        merged = copy.deepcopy(base_settings)
        merged.update(item.get("settings", {}))
        candidates.append({
            "expression": item["expression"],
            "settings": merged,
            "note": item.get("note", ""),
        })

    param_sweeps = scan_cfg.get("param_sweeps")
    if param_sweeps:
        if isinstance(param_sweeps, dict):
            candidates.extend(expand_param_sweeps(param_sweeps))
        elif isinstance(param_sweeps, list):
            for ps in param_sweeps:
                candidates.extend(expand_param_sweeps(ps))

    if not candidates:
        print("No candidates defined in config.")
        return

    concurrency = max(1, int(max_concurrency or scan_cfg.get("max_concurrency") or os.getenv("WQB_SCAN_CONCURRENCY", "1")))
    concurrency = min(concurrency, 3)
    clients = [WQBClient.from_config() for _ in range(concurrency)]

    print(f"Unified scan — {len(candidates)} candidates -> {output_path}", flush=True)
    if concurrency > 1:
        print(f"Concurrent scan enabled — concurrency={concurrency}", flush=True)

    # Setup completion log (append-only, human readable)
    log_dir = Path(".local/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (output_path.stem + ".log")
    log_file = open(log_path, "a", encoding="utf-8")
    log_file.write(f"\n=== Scan started at {time.strftime('%Y-%m-%d %H:%M:%S')} — {len(candidates)} candidates ===\n")
    log_file.flush()

    write_lock = asyncio.Lock()
    stop_event = asyncio.Event()

    async def process_candidate(client: WQBClient, idx: int, item: dict) -> None:
        nonlocal rows
        if stop_event.is_set():
            return
        expression = item["expression"]
        settings = item["settings"]
        key = (expression, _settings_key(settings))
        if key in seen_keys:
            print(f"[{idx}/{len(candidates)}] SKIP (already tested)", flush=True)
            return

        print(f"[{idx}/{len(candidates)}] {expression}", flush=True)
        if item.get("note"):
            print(f"  note: {item['note']}", flush=True)
        started = time.time()

        try:
            sim_data = await asyncio.wait_for(
                asyncio.to_thread(
                    client.run_simulation,
                    {
                        "type": "REGULAR",
                        "settings": settings,
                        "regular": expression,
                    },
                ),
                timeout=600,
            )
            alpha_id = sim_data.get("alpha", "")
            if not alpha_id:
                err = summarize_simulation_payload(sim_data)
                print(f"  {err}\n", flush=True)
                async with write_lock:
                    rows.append({
                        "expression": expression,
                        "settings": settings,
                        "note": item.get("note", ""),
                        "error": err,
                        "diagnosis": sim_data,
                        "elapsed_seconds": round(time.time() - started, 1),
                    })
                    output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
                    log_file.write(f"[{idx}/{len(candidates)}] {item.get('note','')} | EXCEPTION: {err}\n")
                    log_file.flush()
                return

            alpha = await asyncio.to_thread(client.get_alpha, alpha_id)
            if alpha.http_status is not None and alpha.http_status >= 400:
                err = f"Alpha GET failed HTTP {alpha.http_status}"
                print(f"  {err}\n", flush=True)
                async with write_lock:
                    rows.append({
                        "alpha_id": alpha_id,
                        "expression": expression,
                        "settings": settings,
                        "note": item.get("note", ""),
                        "error": err,
                        "elapsed_seconds": round(time.time() - started, 1),
                    })
                    output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
                    log_file.write(f"[{idx}/{len(candidates)}] {item.get('note','')} | EXCEPTION: {err}\n")
                    log_file.flush()
                return
            if alpha.expression and _normalized_expression(alpha.expression) != _normalized_expression(expression):
                err = "Simulation result expression does not match the requested expression"
                async with write_lock:
                    rows.append({
                        "alpha_id": alpha_id,
                        "expression": expression,
                        "platform_expression": alpha.expression,
                        "settings": settings,
                        "note": item.get("note", ""),
                        "error": err,
                        "diagnosis": {"diagnosis_type": "simulation_result_expression_mismatch"},
                        "elapsed_seconds": round(time.time() - started, 1),
                    })
                    output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
                return

            metrics = {
                "sharpe": float(alpha.metrics.get("sharpe", 0.0) or 0.0),
                "fitness": float(alpha.metrics.get("fitness", 0.0) or 0.0),
                "turnover": float(alpha.metrics.get("turnover", 0.0) or 0.0),
                "returns": float(alpha.metrics.get("returns", 0.0) or 0.0),
                "drawdown": float(alpha.metrics.get("drawdown", 0.0) or 0.0),
                "margin": float(alpha.metrics.get("margin", 0.0) or 0.0),
            }
            checks = [check.to_dict() for check in alpha.checks]

            # Run explicit checks if base metrics look good
            if metrics["sharpe"] >= 1.25 and metrics["fitness"] >= 1.0 and metrics["turnover"] <= 0.7:
                final_checks = await explicit_checks(client, alpha_id)
                if final_checks:
                    checks = final_checks

            row = {
                "alpha_id": alpha_id,
                "expression": expression,
                "settings": settings,
                "note": item.get("note", ""),
                "metrics": metrics,
                "checks": checks,
                "elapsed_seconds": round(time.time() - started, 1),
            }
            async with write_lock:
                rows.append(row)
                seen_keys.add(key)
                output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

            fail_names = [c.get("name") for c in checks if c.get("result") in ("FAIL", "ERROR")]
            passed = is_pass(metrics, checks)
            print(
                f"  Sharpe={metrics['sharpe']:.2f}  Fitness={metrics['fitness']:.2f}  "
                f"Turnover={metrics['turnover']:.4f}  Returns={metrics['returns']:.4f}  "
                f"DD={metrics['drawdown']:.4f}  ({row['elapsed_seconds']:.0f}s)",
                flush=True,
            )
            if fail_names:
                print(f"  FAIL: {', '.join(fail_names)}", flush=True)
            elif passed:
                print("  All checks PASS  ->  PASS", flush=True)
            else:
                reasons = []
                if metrics["sharpe"] < 1.25:
                    reasons.append(f"Sharpe={metrics['sharpe']:.2f}<1.25")
                if metrics["fitness"] < 1.0:
                    reasons.append(f"Fitness={metrics['fitness']:.2f}<1.0")
                if metrics["turnover"] > 0.7:
                    reasons.append(f"Turnover={metrics['turnover']:.2f}>0.7")
                print(f"  All checks PASS  ->  NO PASS ({', '.join(reasons)})", flush=True)

            # Write to completion log
            note_str = item.get("note", "")
            log_line = f"[{idx}/{len(candidates)}] {note_str} | S={metrics['sharpe']:.2f} F={metrics['fitness']:.2f} T={metrics['turnover']:.4f} R={metrics['returns']:.4f} DD={metrics['drawdown']:.4f} | "
            if passed:
                log_line += f"PASS | alpha_id={alpha_id}"
            elif fail_names:
                log_line += f"FAIL({','.join(fail_names)})"
            else:
                log_line += f"NO_PASS({','.join(reasons)})"
            async with write_lock:
                log_file.write(log_line + "\n")
                log_file.flush()

            if passed:
                print(f"\n*** FOUND PASS *** {alpha_id}", flush=True)
                print(f"expression: {expression}", flush=True)
                print(f"settings: {json.dumps(settings, indent=2)}", flush=True)
                if not continue_on_pass:
                    print("\nStopping early (found PASS). Use --continue-on-pass or set continue_on_pass:true in config to keep going.\n", flush=True)
                    async with write_lock:
                        log_file.write(f"Stopping early after finding PASS: {alpha_id}\n")
                        log_file.flush()
                    stop_event.set()
                    return

            print()
        except Exception as exc:
            async with write_lock:
                rows.append({
                    "expression": expression,
                    "settings": settings,
                    "note": item.get("note", ""),
                    "error": str(exc),
                    "elapsed_seconds": round(time.time() - started, 1),
                })
                output_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
                log_file.write(f"[{idx}/{len(candidates)}] {item.get('note','')} | EXCEPTION: {exc}\n")
                log_file.flush()
            print(f"  EXCEPTION: {exc}\n", flush=True)

    if concurrency <= 1:
        for idx, item in enumerate(candidates, start=1):
            await process_candidate(clients[0], idx, item)
            if stop_event.is_set():
                break
    else:
        queue: asyncio.Queue[tuple[int, dict]] = asyncio.Queue()
        for idx, item in enumerate(candidates, start=1):
            queue.put_nowait((idx, item))

        async def worker(client: WQBClient) -> None:
            while not queue.empty() and not stop_event.is_set():
                idx, item = await queue.get()
                try:
                    await process_candidate(client, idx, item)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker(client)) for client in clients]
        await asyncio.gather(*workers)

    log_file.write(f"=== Scan complete at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    log_file.close()
    print("Scan complete.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified BRAIN scan runner")
    parser.add_argument("--config", required=True, help="Path to scan config JSON")
    parser.add_argument("--continue-on-pass", action="store_true", help="Continue scanning after finding a PASS (overrides config)")
    parser.add_argument("--max-concurrency", type=int, default=None, help="Maximum concurrent simulations, capped at 3")
    args = parser.parse_args()
    asyncio.run(run_scan(args.config, cli_continue_on_pass=args.continue_on_pass, max_concurrency=args.max_concurrency))
