from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from wqb_agent_lab.research.self_corr_policy import SELF_CORR_NEAR_REPAIR_MAX, self_corr_bucket as _policy_self_corr_bucket

MILD_SELF_CORR_REPAIR_MAX = SELF_CORR_NEAR_REPAIR_MAX

DEFAULT_SCAN_SETTINGS = {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 6,
    "neutralization": "MARKET",
    "truncation": 0.05,
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "ON",
    "maxTrade": "OFF",
    "maxPosition": "OFF",
    "language": "FASTEXPR",
    "visualization": False,
}


def build_self_corr_repair_plan(rows: Sequence[Mapping[str, Any]], *, max_variants_per_alpha: int = 2) -> dict[str, Any]:
    review: list[dict[str, Any]] = []
    excluded_extreme: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    bucket_counts = {"mild": 0, "moderate": 0, "extreme": 0}

    for row in rows:
        if "SELF_CORRELATION" not in _failed_check_names(row):
            continue
        self_corr = _self_corr_value(row)
        bucket = self_corr_bucket(self_corr)
        if bucket not in bucket_counts:
            continue
        bucket_counts[bucket] += 1
        item = _review_item(row, self_corr, bucket)
        review.append(item)
        if bucket == "extreme":
            excluded_extreme.append(item)
            continue
        if bucket != "mild":
            continue
        candidates.extend(_repair_candidates(row, bucket, max_variants_per_alpha=max_variants_per_alpha))

    return {
        "bucket_counts": bucket_counts,
        "review": review,
        "excluded_extreme": excluded_extreme,
        "scan_config": {
            "continue_on_pass": True,
            "max_concurrency": 3,
            "settings": DEFAULT_SCAN_SETTINGS,
            "candidates": _dedupe_candidates(candidates),
        },
    }


def build_bucket_aware_next_scan_plan(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_count: int = 20,
    max_variants_per_alpha: int = 2,
) -> dict[str, Any]:
    review: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    bucket_counts = {"mild": 0, "moderate": 0, "extreme": 0}
    lane_counts = {"repair_probe": 0, "replace_probe": 0}

    for row in rows:
        if "SELF_CORRELATION" not in _failed_check_names(row):
            continue
        self_corr = _self_corr_value(row)
        bucket = self_corr_bucket(self_corr)
        if bucket not in bucket_counts:
            continue
        bucket_counts[bucket] += 1
        review.append(_review_item(row, self_corr, bucket))
        if bucket == "mild":
            next_candidates = _repair_candidates(row, bucket, max_variants_per_alpha=max_variants_per_alpha)
            lane = "repair_probe"
        else:
            next_candidates = _replacement_candidates(row, bucket=bucket, max_variants_per_alpha=max_variants_per_alpha)
            lane = "replace_probe"
        for candidate in next_candidates:
            candidate["wqb_action_lane"] = lane
            candidate["recommended_action"] = "replace_overcrowded_signal" if lane == "replace_probe" else "self_corr_bucket_repair"
            lane_counts[lane] += 1
        candidates.extend(next_candidates)

    deduped = _select_bucket_aware_candidates(_dedupe_candidates(candidates), target_count=max(1, int(target_count)))
    lane_counts = _count_lanes(deduped)
    return {
        "bucket_counts": bucket_counts,
        "lane_counts": lane_counts,
        "review": review,
        "scan_config": {
            "continue_on_pass": True,
            "max_concurrency": 3,
            "settings": DEFAULT_SCAN_SETTINGS,
            "candidates": deduped,
        },
    }


def write_self_corr_repair_artifacts(root: Path, run_dir: Path) -> dict[str, str]:
    snapshot_path = run_dir / "scan_results_snapshot.json"
    rows = _read_json(snapshot_path, [])
    plan = build_self_corr_repair_plan(rows)

    run_tag = run_dir.name
    config_dir = root / ".local" / "research" / "scans" / "continuous-alpha" / f"self-corr-repair-{run_tag}"
    output_path = run_dir / "self_corr_repair_results.json"
    review_path = run_dir / "self_corr_bucket_review.json"
    scan_config_path = config_dir / "scan_config_round1.json"

    config = dict(plan["scan_config"])
    config["output"] = str(output_path)

    _write_json(review_path, {
        "run_tag": run_tag,
        "bucket_counts": plan["bucket_counts"],
        "review": plan["review"],
        "excluded_extreme": plan["excluded_extreme"],
        "repair_candidate_count": len(config["candidates"]),
        "scan_config": str(scan_config_path),
    })
    _write_json(scan_config_path, config)

    return {
        "review_path": str(review_path),
        "scan_config_path": str(scan_config_path),
        "repair_candidate_count": str(len(config["candidates"])),
    }


def write_bucket_aware_next_scan_artifacts(root: Path, run_dir: Path, *, target_count: int = 20) -> dict[str, str]:
    snapshot_path = run_dir / "scan_results_snapshot.json"
    rows = _read_json(snapshot_path, [])
    plan = build_bucket_aware_next_scan_plan(rows, target_count=target_count)

    run_tag = run_dir.name
    config_dir = root / ".local" / "research" / "scans" / "continuous-alpha" / f"bucket-aware-next-{run_tag}"
    output_path = run_dir / "bucket_aware_next_scan_results.json"
    review_path = run_dir / "bucket_aware_next_scan_review.json"
    scan_config_path = config_dir / "scan_config_round1.json"

    config = dict(plan["scan_config"])
    config["output"] = str(output_path)

    _write_json(review_path, {
        "run_tag": run_tag,
        "bucket_counts": plan["bucket_counts"],
        "lane_counts": plan["lane_counts"],
        "review": plan["review"],
        "candidate_count": len(config["candidates"]),
        "scan_config": str(scan_config_path),
    })
    _write_json(scan_config_path, config)

    return {
        "review_path": str(review_path),
        "scan_config_path": str(scan_config_path),
        "candidate_count": str(len(config["candidates"])),
    }


def self_corr_bucket(value: Any) -> str:
    return _policy_self_corr_bucket(value)


def _repair_candidates(row: Mapping[str, Any], bucket: str, *, max_variants_per_alpha: int) -> list[dict[str, Any]]:
    expression = _normalize_expression(str(row.get("expression") or ""))
    if not expression:
        return []
    mutations = _light_mutations(expression) if bucket == "mild" else _structural_mutations(expression)
    base_settings = dict(row.get("settings") or {})
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for axis, mutated in mutations:
        mutated = _normalize_expression(mutated)
        if not mutated or mutated == expression or mutated in seen:
            continue
        seen.add(mutated)
        candidates.append({
            "expression": mutated,
            "settings": _settings_for_axis(base_settings, axis),
            "note": f"self_corr_repair {row.get('alpha_id')} {bucket} {axis}",
            "base_alpha_id": row.get("alpha_id"),
            "base_expression": expression,
            "family": row.get("family") or row.get("behavior_family") or "unknown",
            "self_corr": _self_corr_value(row),
            "self_corr_bucket": bucket,
            "axis": axis,
        })
        if len(candidates) >= max_variants_per_alpha:
            break
    return candidates


def _replacement_candidates(row: Mapping[str, Any], *, bucket: str, max_variants_per_alpha: int) -> list[dict[str, Any]]:
    expression = _normalize_expression(str(row.get("expression") or ""))
    if not expression:
        return []
    mutations = _bridge_component_mutations(expression)
    mutations.extend(_replacement_chassis_mutations(expression))
    mutations.extend(_structural_mutations(expression))
    base_settings = dict(row.get("settings") or {})
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for axis, mutated in mutations:
        mutated = _normalize_expression(mutated)
        if not mutated or mutated == expression or mutated in seen:
            continue
        seen.add(mutated)
        candidates.append({
            "expression": mutated,
            "settings": _settings_for_axis(base_settings, axis),
            "note": f"self_corr_replace {row.get('alpha_id')} {bucket} {axis}",
            "base_alpha_id": row.get("alpha_id"),
            "base_expression": expression,
            "family": row.get("family") or row.get("behavior_family") or "unknown",
            "self_corr": _self_corr_value(row),
            "self_corr_bucket": bucket,
            "axis": axis,
        })
        if len(candidates) >= max_variants_per_alpha:
            break
    return candidates


def _light_mutations(expression: str) -> list[tuple[str, str]]:
    mutations = []
    mutations.extend(_group_shift_mutations(expression))
    mutations.extend(_weaken_reversal_mutations(expression))
    if expression.startswith("group_rank("):
        mutations.append(("neutralize_subindustry", f"group_neutralize({expression}, subindustry)"))
    return mutations


def _structural_mutations(expression: str) -> list[tuple[str, str]]:
    mutations = []
    mutations.extend(_group_shift_mutations(expression))
    mutations.extend(_replace_reversal_anchor_mutations(expression))
    if expression.startswith("group_rank("):
        mutations.append(("neutralize_industry", f"group_neutralize({expression}, industry)"))
    mutations.extend(_drop_common_crowd_leg_mutations(expression))
    return mutations


def _bridge_component_mutations(expression: str) -> list[tuple[str, str]]:
    mutations: list[tuple[str, str]] = []
    primary_rank = _first_weighted_rank_term(expression)

    for pattern in _crowded_price_leg_patterns():
        if re.search(pattern, expression):
            if primary_rank:
                mutations.append(
                    (
                        "bridge_replace_price_reversal_with_primary_confirmation",
                        re.sub(pattern, f" + ts_mean({primary_rank}, 20) / 30", expression, count=1),
                    )
                )
            weakened = _weaken_crowded_price_leg(expression)
            if weakened != expression:
                mutations.append(("bridge_weaken_price_reversal_leg", weakened))
            mutations.append(("bridge_drop_price_reversal_leg", re.sub(pattern, "", expression, count=1)))
            break

    for axis, pattern in (
        ("bridge_drop_price_volume_corr_leg", r"\s*-\s*ts_corr\(rank\(close\),\s*rank\(volume\),\s*\d+\)\s*/\s*\d+"),
        ("bridge_drop_volume_spike_leg", r"\s*-\s*rank\(volume\s*/\s*ts_mean\(volume,\s*\d+\)\)\s*/\s*\d+"),
        ("bridge_drop_volatility_penalty_leg", r"\s*-\s*rank\(ts_std_dev\(returns,\s*\d+\)\)\s*/\s*\d+"),
    ):
        if re.search(pattern, expression):
            mutations.append((axis, re.sub(pattern, "", expression, count=1)))
            break

    return mutations


def _crowded_price_leg_patterns() -> list[str]:
    return [
        r"\s*[+]\s*rank\(-ts_delta\((?:close|vwap),\s*\d+\)\)\s*/\s*\d+",
        r"\s*[+]\s*rank\(ts_mean\((?:close|vwap),\s*\d+\)\s*-\s*(?:close|vwap)\)\s*/\s*\d+",
        r"\s*[+-]\s*rank\(ts_delta\(returns,\s*\d+\)\)\s*/\s*\d+",
        r"\s*[+-]\s*rank\(-ts_mean\(returns,\s*\d+\)\)\s*/\s*\d+",
    ]


def _first_weighted_rank_term(expression: str) -> str:
    match = re.search(r"(rank\((?:[^()]|\([^()]*\))*\))\s*/\s*\d+", expression)
    return match.group(1) if match else ""


def _weaken_crowded_price_leg(expression: str) -> str:
    def delta_repl(match: re.Match[str]) -> str:
        source = match.group(1)
        window = match.group(2)
        divisor = int(match.group(3))
        return f"rank(-ts_delta({source}, {window})) / {max(divisor + 8, int(divisor * 1.5))}"

    weakened = re.sub(
        r"rank\(-ts_delta\((close|vwap),\s*(\d+)\)\)\s*/\s*(\d+)",
        delta_repl,
        expression,
        count=1,
    )
    if weakened != expression:
        return weakened

    def mean_reversion_repl(match: re.Match[str]) -> str:
        source = match.group(1)
        window = match.group(2)
        divisor = int(match.group(3))
        return f"rank(ts_mean({source}, {window}) - {source}) / {max(divisor + 8, int(divisor * 1.5))}"

    return re.sub(
        r"rank\(ts_mean\((close|vwap),\s*(\d+)\)\s*-\s*\1\)\s*/\s*(\d+)",
        mean_reversion_repl,
        expression,
        count=1,
    )


def _group_shift_mutations(expression: str) -> list[tuple[str, str]]:
    replacements = {
        "industry": "subindustry",
        "subindustry": "industry",
        "sector": "industry",
    }
    mutations = []
    for source, target in replacements.items():
        if re.search(rf"\b{source}\b", expression):
            mutations.append((f"group_shift_{target}", re.sub(rf"\b{source}\b", target, expression)))
            break
    return mutations


def _weaken_reversal_mutations(expression: str) -> list[tuple[str, str]]:
    return [
        ("weaken_close_reversal", re.sub(r"rank\(-ts_delta\(close,\s*(\d+)\)\)\s*/\s*(\d+)", _increase_divisor, expression)),
        ("weaken_vwap_reversal", re.sub(r"rank\(-ts_delta\(vwap,\s*(\d+)\)\)\s*/\s*(\d+)", _increase_divisor, expression)),
        ("weaken_return_reversal", re.sub(r"rank\(-returns\)\s*/\s*(\d+)", _increase_single_divisor, expression)),
    ]


def _replace_reversal_anchor_mutations(expression: str) -> list[tuple[str, str]]:
    return [
        ("replace_close_reversal_with_vol_adjusted", re.sub(r"rank\(-ts_delta\(close,\s*(\d+)\)\)", r"rank(-ts_delta(close, \1) / ts_std_dev(returns, 20))", expression)),
        ("replace_vwap_reversal_with_close_reversal", re.sub(r"rank\(-ts_delta\(vwap,\s*(\d+)\)\)", r"rank(-ts_delta(close, \1))", expression)),
    ]


def _drop_common_crowd_leg_mutations(expression: str) -> list[tuple[str, str]]:
    return [
        ("remove_price_volume_corr_leg", re.sub(r"\s*-\s*ts_corr\(rank\(close\),\s*rank\(volume\),\s*\d+\)\s*/\s*\d+", "", expression)),
        ("remove_volume_spike_leg", re.sub(r"\s*-\s*rank\(volume\s*/\s*ts_mean\(volume,\s*\d+\)\)\s*/\s*\d+", "", expression)),
        ("remove_volatility_penalty_leg", re.sub(r"\s*-\s*rank\(ts_std_dev\(returns,\s*\d+\)\)\s*/\s*\d+", "", expression)),
    ]


def _replacement_chassis_mutations(expression: str) -> list[tuple[str, str]]:
    without_price_anchor = re.sub(r"\s*[+\-]\s*rank\(-ts_delta\((close|vwap),\s*\d+\)\)\s*/\s*\d+", "", expression)
    return [
        ("replace_chassis_zscore_rank", f"rank(ts_zscore({without_price_anchor}, 60))"),
        ("replace_chassis_decay_rank", f"rank(ts_decay_linear({without_price_anchor}, 20))"),
    ]


def _settings_for_axis(base_settings: Mapping[str, Any], axis: str) -> dict[str, Any]:
    settings = dict(base_settings)
    if axis.startswith("group_shift") or axis.startswith("neutralize"):
        current = str(base_settings.get("neutralization") or "MARKET")
        settings["neutralization"] = "INDUSTRY" if current != "INDUSTRY" else "SECTOR"
    return settings


def _increase_divisor(match: re.Match[str]) -> str:
    window = match.group(1)
    divisor = int(match.group(2))
    return f"rank(-ts_delta(close, {window})) / {max(divisor + 8, int(divisor * 1.5))}"


def _increase_single_divisor(match: re.Match[str]) -> str:
    divisor = int(match.group(1))
    return f"rank(-returns) / {max(divisor + 8, int(divisor * 1.5))}"


def _review_item(row: Mapping[str, Any], self_corr: Any, bucket: str) -> dict[str, Any]:
    raw_metrics = row.get("metrics")
    metrics: Mapping[str, Any] = (
        raw_metrics if isinstance(raw_metrics, Mapping) else {}
    )
    return {
        "alpha_id": row.get("alpha_id"),
        "family": row.get("family") or row.get("behavior_family") or "unknown",
        "sharpe": metrics.get("sharpe"),
        "fitness": metrics.get("fitness"),
        "turnover": metrics.get("turnover"),
        "self_corr": self_corr,
        "self_corr_bucket": bucket,
        "recommended_action": "replace_overcrowded_signal" if bucket == "extreme" else "self_corr_light_repair" if bucket == "mild" else "self_corr_escape",
        "expression": row.get("expression"),
    }


def _dedupe_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        expression = str(item.get("expression") or "")
        settings = json.dumps(item.get("settings") or {}, sort_keys=True, ensure_ascii=False)
        key = (expression, settings)
        if not expression or key in seen:
            continue
        seen.add(key)
        deduped.append(dict(item))
    return deduped


def _select_bucket_aware_candidates(candidates: Sequence[Mapping[str, Any]], *, target_count: int) -> list[dict[str, Any]]:
    replacement = [dict(item) for item in candidates if str(item.get("wqb_action_lane") or "") == "replace_probe"]
    repair = [dict(item) for item in candidates if str(item.get("wqb_action_lane") or "") != "replace_probe"]
    selected: list[dict[str, Any]] = []
    for item in _first_per_base(replacement):
        if len(selected) >= target_count:
            break
        selected.append(item)
    while len(selected) < target_count and (repair or replacement):
        if repair:
            selected.append(repair.pop(0))
            if len(selected) >= target_count:
                break
        if replacement:
            selected.append(replacement.pop(0))
    return selected[:target_count]


def _first_per_base(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    remaining: list[dict[str, Any]] = []
    for item in candidates:
        base = str(item.get("base_alpha_id") or item.get("base_expression") or item.get("expression") or "")
        if base and base not in seen:
            selected.append(item)
            seen.add(base)
        else:
            remaining.append(item)
    candidates[:] = remaining
    return selected


def _count_lanes(candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"repair_probe": 0, "replace_probe": 0}
    for item in candidates:
        lane = str(item.get("wqb_action_lane") or "repair_probe")
        counts[lane] = counts.get(lane, 0) + 1
    return counts


def _failed_check_names(row: Mapping[str, Any]) -> set[str]:
    return {
        str(check.get("name") or "").upper()
        for check in row.get("checks") or []
        if isinstance(check, Mapping) and str(check.get("result") or "").upper() in {"FAIL", "ERROR"}
    }


def _self_corr_value(row: Mapping[str, Any]) -> Any:
    for check in row.get("checks") or []:
        if isinstance(check, Mapping) and str(check.get("name") or "").upper() == "SELF_CORRELATION":
            return check.get("value")
    return None


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_expression(expression: str) -> str:
    return re.sub(r"\s+", " ", expression.strip())


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return deepcopy(default)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
