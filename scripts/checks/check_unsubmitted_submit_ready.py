"""Fetch UNSUBMITTED metric-ready alphas and run platform checks.

This script does not submit alphas. It only:
1. fetches UNSUBMITTED alphas from BRAIN,
2. keeps alphas meeting basic submission metrics,
3. inspects stored platform checks, and
4. optionally calls the live check API for metric-ready alphas.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from wqb_agent_lab.runtime.config import load_config
from src.evaluator import AlphaMetrics, FilterCriteria, compute_composite_score
from wqb_agent_lab.platform import WQBSession
from wqb_agent_lab.platform.session import (
    URL_ALPHAS_ALPHAID,
    URL_ALPHAS_ALPHAID_CHECK,
    URL_USERS_SELF_ALPHAS,
)


ALPHAS_URL = URL_ALPHAS_ALPHAID
CHECK_URL = URL_ALPHAS_ALPHAID_CHECK
USER_ALPHAS_URL = URL_USERS_SELF_ALPHAS
OUTPUT_PATH = Path(".local/data/unsubmitted_submit_ready_check_report.json")
PROGRESS_PATH = Path(".local/data/unsubmitted_submit_ready_check_progress.json")

PLATFORM_CHECK_NAMES = {
    "SELF_CORRELATION",
    "MATCHES_COMPETITION",
    "CONCENTRATED_WEIGHT",
}

REQUIRED_CHECK_NAMES = {
    "LOW_SHARPE",
    "LOW_FITNESS",
    "LOW_TURNOVER",
    "HIGH_TURNOVER",
    "CONCENTRATED_WEIGHT",
    "LOW_SUB_UNIVERSE_SHARPE",
    "SELF_CORRELATION",
    "MATCHES_COMPETITION",
}


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _json_or_none(resp: Any) -> dict[str, Any] | None:
    if not resp.text.strip():
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _make_http_session(wqb_session: WQBSession) -> WQBSession:
    return wqb_session


def fetch_unsubmitted(http: WQBSession) -> list[dict[str, Any]]:
    alphas: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    while True:
        resp = http.get(
            USER_ALPHAS_URL,
            params={"offset": offset, "limit": limit, "status": "UNSUBMITTED"},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Failed to fetch unsubmitted alphas: HTTP {resp.status_code} {resp.text[:300]}")
        payload = resp.json()
        results = payload.get("results", [])
        if not results:
            break
        alphas.extend(results)
        logger.info("Fetched UNSUBMITTED page offset=%d count=%d total=%d", offset, len(results), len(alphas))
        offset += len(results)
        if len(results) < limit:
            break
    return alphas


def alpha_to_metrics(alpha: dict[str, Any]) -> AlphaMetrics:
    regular = alpha.get("regular", {}) or {}
    is_data = alpha.get("is", {}) or {}
    return AlphaMetrics(
        expression=str(regular.get("code", "") or ""),
        sharpe=float(is_data.get("sharpe", 0.0) or 0.0),
        fitness=float(is_data.get("fitness", 0.0) or 0.0),
        turnover=float(is_data.get("turnover", 0.0) or 0.0),
        returns=float(is_data.get("returns", 0.0) or 0.0),
        drawdown=float(is_data.get("drawdown", 0.0) or 0.0),
        margin=float(is_data.get("margin", 0.0) or 0.0),
        alpha_id=str(alpha.get("id", "") or ""),
    )


def metric_ready_alphas(alphas: list[dict[str, Any]], criteria: FilterCriteria) -> list[dict[str, Any]]:
    ready: list[dict[str, Any]] = []
    for alpha in alphas:
        metrics = alpha_to_metrics(alpha)
        if (
            metrics.alpha_id
            and metrics.sharpe >= criteria.min_sharpe
            and metrics.fitness >= criteria.min_fitness
            and metrics.turnover <= criteria.max_turnover
            and metrics.returns >= criteria.min_returns
            and metrics.drawdown <= criteria.max_drawdown
        ):
            metrics.composite_score = compute_composite_score(metrics)
            ready.append({"alpha": alpha, "metrics": metrics})
    ready.sort(key=lambda item: item["metrics"].composite_score, reverse=True)
    return ready


def dedupe_ready_by_expression(ready: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    deduped: list[dict[str, Any]] = []
    seen_expressions: set[str] = set()
    skipped = 0
    for item in ready:
        expression = item["metrics"].expression.strip()
        if not expression:
            deduped.append(item)
            continue
        if expression in seen_expressions:
            skipped += 1
            continue
        seen_expressions.add(expression)
        deduped.append(item)
    return deduped, skipped


def normalized_checks(alpha_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    checks = ((alpha_payload or {}).get("is") or {}).get("checks") or []
    normalized: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        normalized.append(
            {
                "name": str(check.get("name", "") or "").upper(),
                "result": str(check.get("result", "UNKNOWN") or "UNKNOWN").upper(),
                "limit": check.get("limit"),
                "value": check.get("value"),
            }
        )
    return normalized


def has_platform_check(checks: list[dict[str, Any]]) -> bool:
    names = {str(check.get("name", "") or "").upper() for check in checks}
    return bool(names & PLATFORM_CHECK_NAMES)


def missing_required_checks(checks: list[dict[str, Any]]) -> list[str]:
    names = {str(check.get("name", "") or "").upper() for check in checks}
    return sorted(REQUIRED_CHECK_NAMES - names)


def check_blockers(checks: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for check in checks:
        name = str(check.get("name", "") or "").upper()
        result = str(check.get("result", "UNKNOWN") or "UNKNOWN").upper()
        if result in {"FAIL", "ERROR", "PENDING"}:
            blockers.append(name)
        elif result == "WARNING" and name == "UNITS":
            blockers.append(name)
    return sorted(set(blockers))


def get_alpha_detail(http: WQBSession, alpha_id: str) -> tuple[dict[str, Any] | None, str | None]:
    resp = http.get(ALPHAS_URL.format(alpha_id), timeout=30)
    if resp.status_code == 429:
        return None, "HTTP 429 THROTTLED while fetching alpha detail"
    if not resp.ok:
        return None, f"HTTP {resp.status_code} while fetching alpha detail: {resp.text[:300]}"
    payload = _json_or_none(resp)
    if payload is None:
        return None, f"Non-JSON alpha detail response: {resp.text[:200]}"
    return payload, None


def run_check(
    http: WQBSession,
    alpha_id: str,
    *,
    retries: int,
    retry_wait: float,
) -> tuple[dict[str, Any] | None, str | None, int | None]:
    attempts = max(1, retries)
    last_status_code: int | None = None
    last_error = "Check returned empty/non-JSON response"
    for attempt in range(1, attempts + 1):
        resp = http.get(CHECK_URL.format(alpha_id), timeout=30)
        last_status_code = resp.status_code
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            return None, f"HTTP 429 THROTTLED. Retry-After={retry_after}", resp.status_code
        if not resp.ok:
            return None, f"HTTP {resp.status_code} while running check: {resp.text[:300]}", resp.status_code

        payload = _json_or_none(resp)
        if payload is not None:
            return payload, None, resp.status_code

        if attempt < attempts:
            time.sleep(retry_wait)

    return None, last_error, last_status_code


def record_for_item(
    *,
    alpha_id: str,
    metrics: AlphaMetrics,
    expression: str,
    checks: list[dict[str, Any]],
    had_platform_check: bool,
    action: str,
    check_status_code: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    blockers = check_blockers(checks)
    missing_checks = missing_required_checks(checks)
    return {
        "alpha_id": alpha_id,
        "expression": expression,
        "sharpe": round(metrics.sharpe, 4),
        "fitness": round(metrics.fitness, 4),
        "turnover": round(metrics.turnover, 4),
        "returns": round(metrics.returns, 4),
        "drawdown": round(metrics.drawdown, 4),
        "margin": round(metrics.margin, 6),
        "composite_score": round(metrics.composite_score, 6),
        "had_platform_check": had_platform_check,
        "action": action,
        "check_status_code": check_status_code,
        "has_required_checks": not missing_checks,
        "missing_required_checks": missing_checks,
        "all_pass": bool(checks) and not missing_checks and not blockers,
        "blockers": blockers,
        "checks": checks,
        "error": error,
    }


def save_report(path: Path, results: list[dict[str, Any]], total_unsubmitted: int, total_metric_ready: int) -> None:
    output = {
        "generated_at": datetime.now().isoformat(),
        "total_unsubmitted": total_unsubmitted,
        "total_metric_ready": total_metric_ready,
        "total_recorded": len(results),
        "live_check_attempted_count": sum(1 for row in results if row.get("action") in {"checked_now", "check_error"}),
        "checked_now_count": sum(1 for row in results if row.get("action") == "checked_now"),
        "check_error_count": sum(1 for row in results if row.get("action") == "check_error"),
        "stored_check_only_count": sum(1 for row in results if row.get("action") == "already_checked"),
        "already_checked_count": sum(1 for row in results if row.get("action") == "already_checked"),
        "all_pass_count": sum(1 for row in results if row.get("all_pass")),
        "needs_submit_review": [row for row in results if row.get("all_pass")],
        "results": sorted(results, key=lambda row: row.get("composite_score", 0), reverse=True),
    }
    _write_json(path, output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check UNSUBMITTED alphas that satisfy basic submission metrics.")
    parser.add_argument("--min-sharpe", type=float, default=1.25)
    parser.add_argument("--min-fitness", type=float, default=1.0)
    parser.add_argument("--max-turnover", type=float, default=0.7)
    parser.add_argument("--min-returns", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=float, default=1.0)
    parser.add_argument("--interval", type=float, default=90.0, help="Seconds between check API calls.")
    parser.add_argument("--limit", type=int, default=0, help="Limit metric-ready alphas processed; 0 means all.")
    parser.add_argument("--alpha-ids", nargs="*", default=None, help="Only process these alpha ids after metric filtering.")
    parser.add_argument("--max-checks", type=int, default=0, help="Limit newly triggered checks; 0 means all missing checks.")
    parser.add_argument("--check-retries", type=int, default=3, help="Attempts for an empty/non-JSON check response before recording check_error.")
    parser.add_argument("--check-retry-wait", type=float, default=20.0, help="Seconds to wait between empty/non-JSON check retries.")
    parser.add_argument("--recheck-pending", action="store_true", help="Re-run check for existing SELF_CORRELATION PENDING rows.")
    parser.add_argument("--refresh-existing-checks", action="store_true", help="Re-run check for metric-ready alphas even when platform checks already exist.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help="Report JSON output path.")
    parser.add_argument("--progress", default=str(PROGRESS_PATH), help="Progress JSON output path.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and inspect only; do not call the check API.")
    parser.add_argument("--resume", action="store_true", help="Reuse progress and skip alpha ids already recorded there.")
    parser.add_argument("--no-dedupe-expression", action="store_true", help="Do not merge exact duplicate expressions before processing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    progress_path = Path(args.progress)
    cfg = load_config()
    if not cfg.email or not cfg.password:
        logger.error("请在 .env 中配置 WQB_EMAIL 和 WQB_PASSWORD")
        return 1

    wqb_session = WQBSession(
        (cfg.email, cfg.password),
        auth_max_tries=10,
        auth_delay_unexpected=15.0,
    )
    wqb_session.get_authentication()
    logger.info("WQB 认证成功: %s", cfg.email)
    http = _make_http_session(wqb_session)

    criteria = FilterCriteria(
        min_sharpe=args.min_sharpe,
        min_fitness=args.min_fitness,
        max_turnover=args.max_turnover,
        min_returns=args.min_returns,
        max_drawdown=args.max_drawdown,
    )

    alphas = fetch_unsubmitted(http)
    ready = metric_ready_alphas(alphas, criteria)
    if args.alpha_ids:
        wanted_ids = {str(alpha_id).strip() for alpha_id in args.alpha_ids if str(alpha_id).strip()}
        ready = [item for item in ready if item["metrics"].alpha_id in wanted_ids]
    skipped_duplicate_expressions = 0
    if not args.no_dedupe_expression:
        ready, skipped_duplicate_expressions = dedupe_ready_by_expression(ready)
    total_metric_ready = len(ready)
    if args.limit > 0:
        ready = ready[: args.limit]
    logger.info(
        "UNSUBMITTED: %d; metric-ready total: %d; processing: %d; duplicate expressions merged: %d",
        len(alphas),
        total_metric_ready,
        len(ready),
        skipped_duplicate_expressions,
    )

    progress = _read_json(progress_path, {"results": []}) if args.resume else {"results": []}
    results_by_id = {
        str(row.get("alpha_id")): row
        for row in progress.get("results", [])
        if isinstance(row, dict) and row.get("alpha_id")
    }

    new_check_count = 0
    for index, item in enumerate(ready, start=1):
        alpha = item["alpha"]
        metrics = item["metrics"]
        alpha_id = metrics.alpha_id
        expression = metrics.expression

        if args.resume and alpha_id in results_by_id:
            continue

        detail_payload = alpha
        detail_checks = normalized_checks(detail_payload)
        if not has_platform_check(detail_checks):
            fetched_payload, detail_error = get_alpha_detail(http, alpha_id)
            if detail_error:
                record = record_for_item(
                    alpha_id=alpha_id,
                    metrics=metrics,
                    expression=expression,
                    checks=detail_checks,
                    had_platform_check=False,
                    action="detail_error",
                    error=detail_error,
                )
                results_by_id[alpha_id] = record
                save_report(output_path, list(results_by_id.values()), len(alphas), total_metric_ready)
                _write_json(progress_path, {"generated_at": datetime.now().isoformat(), "results": list(results_by_id.values())})
                logger.warning("[%d/%d] %s detail error: %s", index, len(ready), alpha_id, detail_error[:120])
                continue
            detail_payload = fetched_payload or alpha
            detail_checks = normalized_checks(detail_payload)

        had_check = has_platform_check(detail_checks)
        should_check = args.refresh_existing_checks or not had_check
        if args.recheck_pending and "SELF_CORRELATION" in check_blockers(detail_checks):
            should_check = True

        if args.dry_run or not should_check:
            action = "dry_run_missing_check" if args.dry_run and should_check else "already_checked"
            record = record_for_item(
                alpha_id=alpha_id,
                metrics=metrics,
                expression=expression,
                checks=detail_checks,
                had_platform_check=had_check,
                action=action,
            )
            results_by_id[alpha_id] = record
            logger.info(
                "[%d/%d] %s %s S=%.2f F=%.2f blockers=%s",
                index,
                len(ready),
                alpha_id,
                action,
                metrics.sharpe,
                metrics.fitness,
                ",".join(record["blockers"]) if record["blockers"] else "OK",
            )
        else:
            if args.max_checks > 0 and new_check_count >= args.max_checks:
                record = record_for_item(
                    alpha_id=alpha_id,
                    metrics=metrics,
                    expression=expression,
                    checks=detail_checks,
                    had_platform_check=had_check,
                    action="max_checks_deferred",
                )
                results_by_id[alpha_id] = record
                continue

            check_payload, check_error, status_code = run_check(
                http,
                alpha_id,
                retries=args.check_retries,
                retry_wait=args.check_retry_wait,
            )
            if check_error and status_code == 429:
                retry_after = 60
                if "Retry-After=" in check_error:
                    value = check_error.rsplit("Retry-After=", 1)[-1].strip()
                    if value.isdigit():
                        retry_after = int(value)
                logger.warning("[%d/%d] %s throttled; waiting %s seconds", index, len(ready), alpha_id, retry_after)
                time.sleep(retry_after)
                check_payload, check_error, status_code = run_check(
                    http,
                    alpha_id,
                    retries=args.check_retries,
                    retry_wait=args.check_retry_wait,
                )

            checks = normalized_checks(check_payload) if check_payload else detail_checks
            record = record_for_item(
                alpha_id=alpha_id,
                metrics=metrics,
                expression=expression,
                checks=checks,
                had_platform_check=had_check,
                action="checked_now" if not check_error else "check_error",
                check_status_code=status_code,
                error=check_error,
            )
            results_by_id[alpha_id] = record
            new_check_count += int(not check_error)
            logger.info(
                "[%d/%d] %s checked S=%.2f F=%.2f result=%s blockers=%s",
                index,
                len(ready),
                alpha_id,
                metrics.sharpe,
                metrics.fitness,
                "PASS" if record["all_pass"] else "BLOCKED",
                ",".join(record["blockers"]) if record["blockers"] else "OK",
            )

        save_report(output_path, list(results_by_id.values()), len(alphas), total_metric_ready)
        _write_json(progress_path, {"generated_at": datetime.now().isoformat(), "results": list(results_by_id.values())})

        if should_check and not args.dry_run and index < len(ready):
            time.sleep(args.interval)

    save_report(output_path, list(results_by_id.values()), len(alphas), total_metric_ready)
    logger.info("完成。报告: %s", output_path)
    logger.info("需要提交复核: %d", sum(1 for row in results_by_id.values() if row.get("all_pass")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
