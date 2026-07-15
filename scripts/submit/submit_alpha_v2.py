"""直接用 requests 提交 Alpha（绕过 wqb async）。"""
import json
import sys
import time
from pathlib import Path

import requests

from src.config import load_config
from wqb_agent_lab.platform import WQBSession
from wqb_agent_lab.platform.session import (
    URL_ALPHAS_ALPHAID,
    URL_ALPHAS_ALPHAID_CHECK,
    URL_ALPHAS_ALPHAID_SUBMIT,
)
from src.side_effect_governance import SideEffectCapabilityDisabled, require_side_effect_capability


def _json_or_none(resp: requests.Response):
    if not resp.text.strip():
        return None
    try:
        return resp.json()
    except ValueError:
        try:
            return json.loads(resp.text, strict=False)
        except ValueError:
            return None


def _request_with_retries(http: requests.Session, method: str, url: str, *, max_tries: int = 12, **kwargs):
    for attempt in range(1, max_tries + 1):
        try:
            resp = http.request(method, url, **kwargs)
        except requests.RequestException as exc:
            if attempt == max_tries:
                raise
            wait_seconds = 30.0
            print(f"请求异常: {exc}; 等待 {wait_seconds:.1f}s 后重试 ({attempt}/{max_tries})")
            time.sleep(wait_seconds)
            continue
        if resp.status_code != 429:
            return resp
        retry_after = resp.headers.get("Retry-After", "30")
        try:
            wait_seconds = max(float(retry_after), 1.0)
        except ValueError:
            wait_seconds = 30.0
        if attempt == max_tries:
            return resp
        print(f"429 THROTTLED，等待 {wait_seconds:.1f}s 后重试 ({attempt}/{max_tries})")
        time.sleep(wait_seconds)
    return resp


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _find_alpha_row(payload, alpha_id: str):
    if isinstance(payload, list):
        for item in payload:
            row = _find_alpha_row(item, alpha_id)
            if row:
                return row
    if isinstance(payload, dict):
        if payload.get("alpha_id") == alpha_id or payload.get("optimized_alpha_id") == alpha_id:
            return payload
        for value in payload.values():
            row = _find_alpha_row(value, alpha_id)
            if row:
                return row
    return None


def _check_failures(checks):
    failures = []
    for check in checks:
        name = str(check.get("name", "UNKNOWN") or "UNKNOWN")
        result = str(check.get("result", "UNKNOWN") or "UNKNOWN").upper()
        if result in {"PASS", "UNKNOWN"}:
            continue
        if name.upper() == "UNITS" and result == "WARNING":
            continue
        failures.append(name)
    return failures


def _pending_checks(checks):
    return [c.get("name", "UNKNOWN") for c in checks if c.get("result") == "PENDING"]


def _fetch_alpha_detail(http: requests.Session, alpha_id: str):
    detail_resp = _request_with_retries(http, "GET", URL_ALPHAS_ALPHAID.format(alpha_id), timeout=30)
    if not detail_resp.ok:
        print(f"Alpha detail 失败: {detail_resp.status_code} {detail_resp.text[:200]}")
        return None
    data = _json_or_none(detail_resp)
    if data is None:
        print(f"Alpha detail 返回非 JSON: {detail_resp.text[:100]}")
        return None
    return data


def _wait_for_resolved_checks(http: requests.Session, alpha_id: str, data: dict, *, max_polls: int = 12):
    checks = data.get("is", {}).get("checks", [])
    for poll in range(max_polls + 1):
        pending = _pending_checks(checks)
        if not pending:
            return data, checks
        if poll == max_polls:
            return data, checks
        wait_seconds = 30.0
        print(f"Check 仍在 PENDING: {', '.join(pending)}; 等待 {wait_seconds:.1f}s 后刷新 ({poll + 1}/{max_polls})")
        time.sleep(wait_seconds)
        refreshed = _fetch_alpha_detail(http, alpha_id)
        if refreshed is None:
            return data, checks
        data = refreshed
        checks = data.get("is", {}).get("checks", [])
    return data, checks


def _scoreboard_entry(scoreboard: dict, key: str) -> dict:
    entry = scoreboard.setdefault(key, {})
    entry["submitted_count"] = int(entry.get("submitted_count", 0) or 0) + 1
    return entry


def _record_submission(alpha_id: str) -> None:
    workflow_root = Path(".local/data/workflow/continuous-alpha")
    if not workflow_root.exists():
        return
    for run_dir in workflow_root.iterdir():
        if not run_dir.is_dir():
            continue
        matched = None
        matched_source = None
        for path in sorted(run_dir.glob("*.json")):
            if path.name in {"simulation_cache.json", "dataset_scoreboard.json", "field_scoreboard.json", "chassis_scoreboard.json"}:
                continue
            payload = _read_json(path, None)
            row = _find_alpha_row(payload, alpha_id)
            if row:
                matched = row
                matched_source = path
                break
        if not matched:
            continue

        dataset = str(matched.get("dataset", "unknown") or "unknown")
        chassis = str(matched.get("chassis", "") or matched.get("skeleton", "unknown") or "unknown")
        fields = matched.get("fields", []) or []

        dataset_scoreboard = _read_json(run_dir / "dataset_scoreboard.json", {})
        chassis_scoreboard = _read_json(run_dir / "chassis_scoreboard.json", {})
        field_scoreboard = _read_json(run_dir / "field_scoreboard.json", {})
        _scoreboard_entry(dataset_scoreboard, dataset)
        _scoreboard_entry(chassis_scoreboard, chassis)
        for field_id in fields:
            _scoreboard_entry(field_scoreboard, str(field_id))
        _write_json(run_dir / "dataset_scoreboard.json", dataset_scoreboard)
        _write_json(run_dir / "chassis_scoreboard.json", chassis_scoreboard)
        _write_json(run_dir / "field_scoreboard.json", field_scoreboard)

        events_path = run_dir / "submission_events.json"
        events = _read_json(events_path, [])
        if not any(item.get("alpha_id") == alpha_id for item in events if isinstance(item, dict)):
            events.append(
                {
                    "alpha_id": alpha_id,
                    "dataset": dataset,
                    "chassis": chassis,
                    "fields": fields,
                    "source": matched_source.as_posix() if matched_source else "",
                    "status": "submitted",
                }
            )
            _write_json(events_path, events)
        return


def main(alpha_id: str):
    try:
        require_side_effect_capability("submission")
    except SideEffectCapabilityDisabled as exc:
        print(json.dumps(exc.decision.to_dict(), ensure_ascii=False))
        return 2
    cfg = load_config()
    if not cfg.email or not cfg.password:
        print("错误: 请在 .env 中配置 WQB_EMAIL 和 WQB_PASSWORD")
        return 1

    # WQB 认证
    wqb_session = WQBSession(
        (cfg.email, cfg.password),
        auth_max_tries=10,
        auth_delay_unexpected=15.0,
    )
    wqb_session.get_authentication()
    print(f"认证成功: {cfg.email}")

    http = wqb_session

    # 1. Check
    check_url = URL_ALPHAS_ALPHAID_CHECK.format(alpha_id)
    print(f"\nCheck: {check_url}")
    resp = _request_with_retries(http, "GET", check_url, timeout=30)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 429:
        print("429 THROTTLED，无法 check")
        return 1
    if not resp.ok:
        print(f"Check 失败: {resp.text[:200]}")
        return 1
    data = _json_or_none(resp)
    if data is None:
        print("Check 返回空或非 JSON，改用 alpha detail 校验")
        data = _fetch_alpha_detail(http, alpha_id)
        if data is None:
            return 1

    data, checks = _wait_for_resolved_checks(http, alpha_id, data)
    failures = _check_failures(checks)
    if failures:
        print(f"Check 未通过: {', '.join(failures)}")
        return 1
    print("Check: [OK] 可提交")

    # 2. Submit
    submit_url = URL_ALPHAS_ALPHAID_SUBMIT.format(alpha_id)
    print(f"\nSubmit: {submit_url}")
    resp = _request_with_retries(http, "POST", submit_url, timeout=30)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 429:
        print("429 THROTTLED，无法 submit")
        return 1
    if resp.ok:
        print("Submit: [OK] 成功")
        _record_submission(alpha_id)
        try:
            print(f"响应: {resp.json()}")
        except Exception:
            print(f"响应文本: {resp.text}")
        return 0
    else:
        print(f"Submit 失败: {resp.text[:300]}")
        return 1


if __name__ == "__main__":
    alpha_id = sys.argv[1] if len(sys.argv) > 1 else "O0b0k7b1"
    sys.exit(main(alpha_id))
