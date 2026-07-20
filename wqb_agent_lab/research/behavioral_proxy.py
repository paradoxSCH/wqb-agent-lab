from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class MechanismDefinition:
    mechanism: str
    label_en: str
    label_zh: str
    keywords: tuple[str, ...]
    low_signal_policy: str = "downweight"


MECHANISMS: tuple[MechanismDefinition, ...] = (
    MechanismDefinition(
        "analyst_expectation_revision",
        "Analyst expectation revision",
        "分析师预期修正",
        ("analyst", "estimate", "revision", "rev", "surprise", "surp", "eps", "forecast", "numest"),
        low_signal_policy="controlled",
    ),
    MechanismDefinition(
        "investor_attention_coverage",
        "Investor attention and coverage gap",
        "投资者注意力与覆盖缺口",
        ("attention", "coverage", "numest", "forecast count", "volume", "turnover", "liquidity", "news"),
        low_signal_policy="controlled",
    ),
    MechanismDefinition(
        "post_earnings_underreaction",
        "Post-earnings underreaction",
        "盈利公告后反应不足",
        ("earnings", "earning", "surprise", "eps", "abnormal return", "revision", "estimate"),
        low_signal_policy="controlled",
    ),
    MechanismDefinition(
        "media_sentiment_reversal",
        "Media and sentiment reversal",
        "媒体/情绪反转",
        ("media", "social", "sentiment", "news", "put", "call", "relativevalue", "relative value"),
    ),
    MechanismDefinition(
        "attention_amplified_anomaly",
        "Attention-amplified anomaly",
        "注意力放大的异常收益",
        ("attention", "volume", "turnover", "news", "coverage", "liquidity", "surprise"),
    ),
    MechanismDefinition(
        "quality_value_mispricing",
        "Quality and value mispricing",
        "质量/价值误定价",
        ("quality", "value", "roe", "roa", "cashflow", "cash flow", "margin", "profit", "accrual", "operating"),
        low_signal_policy="controlled",
    ),
    MechanismDefinition(
        "capital_allocation_misread",
        "Capital allocation misread",
        "资本配置误读",
        ("capex", "dividend", "payout", "buyback", "issuance", "asset growth", "investment", "leverage"),
        low_signal_policy="controlled",
    ),
    MechanismDefinition(
        "working_capital_cycle_misread",
        "Working-capital cycle misread",
        "营运资本周期误读",
        ("inventory", "receivable", "payable", "working capital", "current ratio", "prepaid"),
        low_signal_policy="controlled",
    ),
    MechanismDefinition(
        "limits_to_arbitrage_conditioned_mispricing",
        "Limits-to-arbitrage conditioned mispricing",
        "套利限制约束下的误定价",
        ("short", "borrow", "squeeze", "volatility", "option", "put", "call", "distress", "altman", "liquidityrisk"),
    ),
    MechanismDefinition(
        "reference_point_disposition_drift",
        "Reference-point and disposition drift",
        "参考点/处置效应漂移",
        ("52w", "52 week", "high52", "low52", "reference", "anchor", "gain", "loss"),
    ),
)


def build_behavioral_proxy_map(
    fields: Sequence[Mapping[str, Any]],
    *,
    result_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = list(result_rows or [])
    mechanisms = []
    for definition in MECHANISMS:
        matched = _matched_fields(fields, definition.keywords)
        feedback = _result_feedback(definition.mechanism, rows)
        proxy_strength = _proxy_strength(matched)
        result_strength = _result_strength(feedback)
        budget_policy = _budget_policy(definition, proxy_strength, result_strength, feedback)
        mechanisms.append(
            {
                "mechanism": definition.mechanism,
                "label_en": definition.label_en,
                "label_zh": definition.label_zh,
                "proxy_strength": proxy_strength,
                "result_strength": result_strength,
                "budget_policy": budget_policy,
                "field_evidence": _field_evidence(matched),
                "result_feedback": feedback,
                "expected_failure_modes": _expected_failure_modes(feedback, proxy_strength),
                "rationale_zh": _rationale_zh(definition, proxy_strength, result_strength, budget_policy, feedback),
            }
        )
    return {
        "version": 1,
        "source": "wqb_field_first_behavioral_proxy_map",
        "mechanism_count": len(mechanisms),
        "mechanisms": mechanisms,
    }


def _matched_fields(fields: Sequence[Mapping[str, Any]], keywords: Sequence[str]) -> list[Mapping[str, Any]]:
    matched = []
    seen = set()
    for field in fields:
        field_id = str(field.get("id") or field.get("name") or "")
        if not field_id or field_id in seen:
            continue
        text = _field_text(field)
        if any(keyword in text for keyword in keywords):
            matched.append(field)
            seen.add(field_id)
    return matched


def _field_text(field: Mapping[str, Any]) -> str:
    parts = [
        field.get("id"),
        field.get("name"),
        field.get("description"),
        field.get("dataset_name"),
        field.get("category_name"),
        field.get("subcategory_name"),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _field_evidence(fields: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "matched_field_count": len(fields),
        "average_coverage": round(_average(_number(field.get("coverage")) for field in fields), 4),
        "average_user_count": round(_average(_number(field.get("userCount")) for field in fields), 2),
        "average_alpha_count": round(_average(_number(field.get("alphaCount")) for field in fields), 2),
        "sample_fields": [
            {
                "id": str(field.get("id") or field.get("name") or ""),
                "description": str(field.get("description") or "")[:180],
                "coverage": _number(field.get("coverage")),
                "userCount": int(_number(field.get("userCount"))),
                "alphaCount": int(_number(field.get("alphaCount"))),
            }
            for field in fields[:12]
        ],
    }


def _proxy_strength(fields: Sequence[Mapping[str, Any]]) -> str:
    count = len(fields)
    coverage = _average(_number(field.get("coverage")) for field in fields)
    if count >= 2 and coverage >= 0.75:
        return "strong"
    if count >= 2:
        return "medium"
    if count == 1:
        return "weak"
    return "none"


def _result_feedback(mechanism: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    relevant = [row for row in rows if _row_family(row) == mechanism]
    failures = Counter(failure for row in relevant for failure in _failed_checks(row))
    sharpes = [
        value
        for row in relevant
        if (value := _metric(row, "sharpe")) is not None
    ]
    fitness = [
        value
        for row in relevant
        if (value := _metric(row, "fitness")) is not None
    ]
    near_pass_count = sum(1 for row in relevant if (_metric(row, "sharpe") or -999.0) >= 1.25 and (_metric(row, "fitness") or -999.0) >= 1.0)
    all_pass_count = sum(1 for row in relevant if _all_pass(row))
    return {
        "tested_count": len(relevant),
        "all_pass_count": all_pass_count,
        "near_pass_count": near_pass_count,
        "average_sharpe": round(_average(sharpes), 4),
        "max_sharpe": round(max(sharpes), 4) if sharpes else 0.0,
        "average_fitness": round(_average(fitness), 4),
        "max_fitness": round(max(fitness), 4) if fitness else 0.0,
        "failure_modes": [{"name": name, "count": count} for name, count in failures.most_common()],
    }


def _row_family(row: Mapping[str, Any]) -> str:
    note = str(row.get("note") or "")
    if ":" in note:
        return note.split(":", 1)[0].strip()
    return str(row.get("family") or "").strip()


def _failed_checks(row: Mapping[str, Any]) -> list[str]:
    return [
        str(check.get("name") or "")
        for check in row.get("checks") or []
        if check.get("result") == "FAIL" and check.get("name")
    ]


def _all_pass(row: Mapping[str, Any]) -> bool:
    checks = row.get("checks") or []
    return bool(checks) and all(check.get("result") == "PASS" for check in checks)


def _metric(row: Mapping[str, Any], name: str) -> float | None:
    metrics = row.get("metrics") or {}
    value = metrics.get(name) if isinstance(metrics, Mapping) else None
    number = _number(value)
    return number if math.isfinite(number) else None


def _result_strength(feedback: Mapping[str, Any]) -> str:
    if int(feedback.get("all_pass_count") or 0) > 0:
        return "promising"
    if int(feedback.get("near_pass_count") or 0) >= 2:
        return "promising"
    if int(feedback.get("near_pass_count") or 0) == 1:
        return "mixed"
    if int(feedback.get("tested_count") or 0) > 0:
        return "weak"
    return "untested"


def _budget_policy(
    definition: MechanismDefinition,
    proxy_strength: str,
    result_strength: str,
    feedback: Mapping[str, Any],
) -> str:
    if result_strength == "promising" and proxy_strength in {"strong", "medium"}:
        return "promote"
    if proxy_strength == "strong" and result_strength in {"untested", "mixed"}:
        return "controlled"
    if proxy_strength == "strong" and result_strength == "weak":
        return definition.low_signal_policy
    if proxy_strength == "medium" and result_strength in {"untested", "mixed"}:
        return "controlled"
    if proxy_strength == "none" and int(feedback.get("tested_count") or 0) == 0:
        return "block"
    return "downweight"


def _expected_failure_modes(feedback: Mapping[str, Any], proxy_strength: str) -> list[str]:
    failures = [str(row["name"]) for row in feedback.get("failure_modes") or [] if row.get("name")]
    if failures:
        return failures[:4]
    if proxy_strength in {"none", "weak"}:
        return ["LOW_SHARPE", "LOW_FITNESS"]
    return ["SELF_CORRELATION", "LOW_FITNESS"]


def _rationale_zh(
    definition: MechanismDefinition,
    proxy_strength: str,
    result_strength: str,
    budget_policy: str,
    feedback: Mapping[str, Any],
) -> str:
    if int(feedback.get("all_pass_count") or 0) > 0:
        signal = "已经出现全检查通过的候选"
    elif int(feedback.get("near_pass_count") or 0) > 0:
        signal = "已有接近通过的右尾候选"
    elif int(feedback.get("tested_count") or 0) > 0:
        signal = "当前模拟结果偏弱"
    else:
        signal = "尚无本轮模拟反馈"
    return (
        f"{definition.label_zh} 的字段代理强度为 {proxy_strength}，结果强度为 {result_strength}，"
        f"{signal}；预算策略建议为 {budget_policy}。"
    )


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _average(values: Sequence[float] | Any) -> float:
    vals = [float(value) for value in values if math.isfinite(float(value))]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


__all__ = ["build_behavioral_proxy_map"]
