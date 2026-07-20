from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .policy_feedback import apply_policy_feedback


PRICE_VOLUME_PROXY_IDS = {"close", "returns", "volume", "vwap", "adv20"}

GENERIC_FIELD_NOISE = (
    "currency_code",
    "pricing currency",
    "actuals_value_currency",
    "minimum guidance value",
    "maximum guidance value",
)

MECHANISM_REQUIRED_MATCHES: Mapping[str, tuple[str, ...]] = {
    "quality_value_mispricing": ("quality", "cashflow", "cash flow", "accrual", "roe", "roa", "margin"),
    "limits_to_arbitrage_mispricing": ("short", "borrow", "put", "call", "option", "volatility", "liquidityrisk", "lend"),
    "media_sentiment_reversal": ("news", "sentiment", "media", "social", "buzz", "ravenpack"),
    "working_capital_cycle_misread": ("inventory", "receivable", "payable", "working capital", "cashflow", "current ratio"),
}


@dataclass(frozen=True)
class MechanismSeed:
    mechanism: str
    zh_name: str
    behavioral_logic: str
    market_condition: str
    expected_alpha_direction: str
    proxy_requirements: tuple[str, ...]
    kill_conditions: tuple[str, ...]
    quality_risks: tuple[str, ...]
    generation_policy: str
    field_families: tuple[str, ...]
    preferred_field_patterns: tuple[str, ...]
    required_secondary_proxies: tuple[str, ...]
    skeleton_template: str
    parameter_space: Mapping[str, Sequence[Any]]
    claim_template: str
    expected_holding_logic: str


MECHANISM_SEEDS: tuple[MechanismSeed, ...] = (
    MechanismSeed(
        mechanism="post_earnings_revision_conservatism",
        zh_name="盈利修正保守反应",
        behavioral_logic="投资者对盈利公告、盈利惊喜和分析师预期修正反应不足，价格调整滞后。",
        market_condition="盈利或预期信号已经变化，但价格和成交注意力尚未充分跟随。",
        expected_alpha_direction="偏向做多正向修正且价格未充分反应的股票，做空负向修正且价格仍锚定旧预期的股票。",
        proxy_requirements=("earnings_surprise", "analyst_revision", "price_underreaction"),
        kill_conditions=("LOW_FITNESS", "SELF_CORRELATION", "duplicate_revision_skeleton"),
        quality_risks=("退化成普通 earnings momentum", "分析师字段过度拥挤", "高 self-corr"),
        generation_policy="promote",
        field_families=("earnings_momentum", "analyst_revision", "surprise"),
        preferred_field_patterns=("earning", "earnings", "eps", "surprise", "revision", "rev", "estimate", "numest"),
        required_secondary_proxies=("price_underreaction", "volume_attention", "volatility_filter"),
        skeleton_template="group_rank(rank(ts_delta({primary_field}, {lookback})) + rank({secondary_proxy}) - rank({risk_filter}), {group})",
        parameter_space={"lookback": (20, 60, 120), "group": ("industry", "subindustry"), "risk_weight": (0.05, 0.1)},
        claim_template="盈利或分析师预期改善但价格未充分反应时，后续存在保守修正带来的补涨。",
        expected_holding_logic="中短周期 underreaction，优先 probe，后续只放大低相关 champion。",
    ),
    MechanismSeed(
        mechanism="anchoring_revision_gap",
        zh_name="锚定修正缺口",
        behavioral_logic="投资者锚定旧估值、旧盈利或旧价格参照点，对新修正信号调整不足。",
        market_condition="估值或盈利信号已改善，但价格仍靠近旧锚点。",
        expected_alpha_direction="偏向做多修正改善且估值仍锚定低位的标的。",
        proxy_requirements=("reference_point", "fundamental_revision", "valuation_anchor"),
        kill_conditions=("pure_price_reversal", "LOW_FITNESS", "no_fundamental_proxy"),
        quality_risks=("容易退化为纯价格反转", "参照点过度拥挤", "字段代理不足"),
        generation_policy="controlled",
        field_families=("valuation", "analyst_revision", "reference_point"),
        preferred_field_patterns=("value", "valuation", "pe", "peg", "estimate", "revision", "52", "high52", "low52"),
        required_secondary_proxies=("valuation_anchor", "price_reference_point", "risk_filter"),
        skeleton_template="group_rank(rank(ts_zscore({primary_field}, {lookback})) + rank({secondary_proxy}) - rank(ts_std_dev(returns, 20)), {group})",
        parameter_space={"lookback": (60, 120, 180), "group": ("industry", "subindustry"), "decay": (5, 9)},
        claim_template="基本面修正与旧估值锚点之间存在缺口时，市场会逐步修正错误定价。",
        expected_holding_logic="中周期锚定修正，禁止纯 price-volume 主代理。",
    ),
    MechanismSeed(
        mechanism="quality_value_mispricing",
        zh_name="质量价值错定价",
        behavioral_logic="市场短期低估现金流质量、盈利质量或价值修复的持续性。",
        market_condition="质量改善出现，但估值、价格或风险定价尚未修复。",
        expected_alpha_direction="偏向质量改善且估值未充分反映的股票。",
        proxy_requirements=("cashflow_quality", "earnings_quality", "valuation_compression"),
        kill_conditions=("LOW_FITNESS", "duplicate_quality_value_skeleton", "high_self_corr_cluster"),
        quality_risks=("质量和价值字段过度拥挤", "容易与已有 quality alpha 重复", "慢变量过拟合"),
        generation_policy="promote",
        field_families=("quality", "cashflow", "valuation", "profitability"),
        preferred_field_patterns=("quality", "cashflow", "cash flow", "accrual", "roe", "roa", "margin", "value", "valuation"),
        required_secondary_proxies=("valuation_anchor", "volatility_filter", "crowding_filter"),
        skeleton_template="group_rank(rank(ts_mean({primary_field}, {lookback})) + rank({secondary_proxy}) - rank({risk_filter}), {group})",
        parameter_space={"lookback": (60, 120, 180), "group": ("industry", "subindustry"), "risk_weight": (0.05, 0.1, 0.15)},
        claim_template="质量改善未被估值充分反映时，未来存在更高质量的价值修复 alpha。",
        expected_holding_logic="中周期质量价值重定价，优先保留低相关 champion。",
    ),
    MechanismSeed(
        mechanism="attention_amplified_anomaly",
        zh_name="注意力放大异常",
        behavioral_logic="有限注意力导致基本面异常在关注度变化时被重新定价。",
        market_condition="基本面异常存在，同时新闻、覆盖度或成交注意力发生边际变化。",
        expected_alpha_direction="做多被注意力重新发现的正向异常，规避纯成交量异常。",
        proxy_requirements=("fundamental_anomaly", "attention_change", "coverage_gap"),
        kill_conditions=("pure_volume_signal", "LOW_SHARPE", "attention_without_fundamental_proxy"),
        quality_risks=("退化成 volume anomaly", "注意力代理噪声大", "短期 crowding"),
        generation_policy="controlled",
        field_families=("attention", "coverage", "fundamental_model", "liquidity"),
        preferred_field_patterns=("attention", "coverage", "numest", "forecast", "news", "surprise", "liquidity", "volume"),
        required_secondary_proxies=("coverage_gap", "volume_attention", "volatility_filter"),
        skeleton_template="group_rank(rank({primary_field}) * (1 + rank({secondary_proxy}) / {attention_scale}) - rank({risk_filter}), {group})",
        parameter_space={"attention_scale": (20, 30, 40), "group": ("industry", "subindustry"), "lookback": (20, 60)},
        claim_template="基本面异常在注意力边际改善时更容易被市场重定价。",
        expected_holding_logic="短中周期 attention repricing，只允许 attention 作为增强腿。",
    ),
    MechanismSeed(
        mechanism="limits_to_arbitrage_mispricing",
        zh_name="套利限制错定价",
        behavioral_logic="卖空、借券、期权或流动性限制让错误定价不能被及时消除。",
        market_condition="存在 short constraint、option sentiment、borrow pressure 或流动性风险。",
        expected_alpha_direction="在套利限制缓解或极端约束反转时捕捉错误定价修复。",
        proxy_requirements=("short_constraint", "option_sentiment", "liquidity_risk"),
        kill_conditions=("LOW_SUB_UNIVERSE_SHARPE", "HIGH_TURNOVER", "uncontrolled_option_noise"),
        quality_risks=("期权字段噪声", "极端尾部风险", "高换手"),
        generation_policy="controlled",
        field_families=("short_interest", "options", "liquidity_risk", "volatility"),
        preferred_field_patterns=("short", "borrow", "put", "call", "option", "volatility", "liquidityrisk", "lend"),
        required_secondary_proxies=("volatility_filter", "liquidity_filter", "fundamental_anchor"),
        skeleton_template="group_rank(rank(ts_zscore({primary_field}, {lookback})) / {constraint_scale} + rank({secondary_proxy}) - rank({risk_filter}), {group})",
        parameter_space={"lookback": (60, 120, 180), "constraint_scale": (8, 12, 16), "group": ("industry", "subindustry")},
        claim_template="套利限制导致的错误定价在约束缓解或风险重新定价时产生可交易 alpha。",
        expected_holding_logic="受控探索，不允许无基本面锚的期权噪声直接放量。",
    ),
    MechanismSeed(
        mechanism="reference_point_disposition_drift",
        zh_name="参照点处置漂移",
        behavioral_logic="投资者围绕 52 周高低点、盈亏参照点和历史价格锚点产生处置效应。",
        market_condition="价格接近关键参照点，同时基本面或预期信号支持继续漂移或反转。",
        expected_alpha_direction="捕捉参照点附近的延迟漂移或反转，但必须有非价格代理确认。",
        proxy_requirements=("reference_point", "fundamental_confirmation", "risk_filter"),
        kill_conditions=("pure_price_reference", "SELF_CORRELATION", "LOW_FITNESS"),
        quality_risks=("容易变成普通 52w momentum", "高拥挤", "缺少基本面确认"),
        generation_policy="downweight",
        field_families=("reference_point", "momentum_model", "fundamental_confirmation"),
        preferred_field_patterns=("52", "high52", "low52", "momentum", "revision", "quality", "earning"),
        required_secondary_proxies=("fundamental_confirmation", "volatility_filter", "crowding_filter"),
        skeleton_template="group_rank(rank({primary_field}) + rank({secondary_proxy}) - rank({risk_filter}), {group})",
        parameter_space={"group": ("industry", "subindustry"), "risk_weight": (0.05, 0.1), "lookback": (20, 60)},
        claim_template="参照点附近的处置效应只有在基本面确认存在时才值得小预算 probe。",
        expected_holding_logic="小预算 probe，缺非价格代理时直接 block。",
    ),
    MechanismSeed(
        mechanism="media_sentiment_reversal",
        zh_name="媒体情绪反转",
        behavioral_logic="新闻、媒体或社交情绪极端会造成短期过度反应，随后出现修复。",
        market_condition="情绪极端且价格或风险已经过度反应，同时基本面未同步恶化。",
        expected_alpha_direction="做多负面情绪过度反应后的修复，做空正面情绪过热后的回落。",
        proxy_requirements=("news_sentiment", "social_sentiment", "fundamental_anchor"),
        kill_conditions=("sentiment_without_anchor", "HIGH_TURNOVER", "LOW_FITNESS"),
        quality_risks=("新闻字段短期噪声", "事件风险", "高换手"),
        generation_policy="controlled",
        field_families=("news", "sentiment", "social_media", "fundamental_anchor"),
        preferred_field_patterns=("news", "sentiment", "media", "social", "buzz", "ravenpack", "relativevalue", "quality"),
        required_secondary_proxies=("fundamental_anchor", "volatility_filter", "price_overreaction"),
        skeleton_template="group_rank(rank(-ts_zscore({primary_field}, {lookback})) + rank({secondary_proxy}) - rank({risk_filter}), {group})",
        parameter_space={"lookback": (60, 120, 180, 240), "group": ("industry", "subindustry"), "decay": (5, 9)},
        claim_template="情绪极端后的修复只有在基本面锚没有同步恶化时才具备提交质量。",
        expected_holding_logic="短中周期 sentiment reversal，必须带基本面 anchor。",
    ),
    MechanismSeed(
        mechanism="working_capital_cycle_misread",
        zh_name="营运资本周期误读",
        behavioral_logic="市场误读库存、应收、应付和现金流周转的周期性改善或恶化。",
        market_condition="营运资本指标变化领先于盈利或现金流确认。",
        expected_alpha_direction="做多营运资本改善且价格未反映的标的。",
        proxy_requirements=("inventory", "receivable", "cashflow_cycle"),
        kill_conditions=("LOW_FITNESS", "slow_variable_overfit", "duplicate_working_capital_skeleton"),
        quality_risks=("慢变量反应慢", "行业差异大", "财报滞后"),
        generation_policy="controlled",
        field_families=("inventory", "receivable", "working_capital", "cashflow"),
        preferred_field_patterns=("inventory", "receivable", "payable", "working capital", "cashflow", "current ratio", "sale"),
        required_secondary_proxies=("industry_neutralization", "valuation_anchor", "volatility_filter"),
        skeleton_template="group_rank(rank(ts_delta(ts_mean({primary_field}, {lookback}), {delta_window})) + rank({secondary_proxy}) - rank({risk_filter}), {group})",
        parameter_space={"lookback": (60, 120, 180), "delta_window": (10, 20, 40), "group": ("industry", "subindustry")},
        claim_template="营运资本周期改善被市场误读时，后续盈利质量确认带来重定价。",
        expected_holding_logic="中周期财报确认链路，必须行业中性化。",
    ),
)


def build_candidate_generation_artifacts(
    fields: Sequence[Mapping[str, Any]],
    *,
    policy_feedback: Mapping[str, Any] | None = None,
    policy_feedback_mode: str = "shadow",
) -> dict[str, Any]:
    inventory = _build_inventory()
    field_map = _build_field_map(fields)
    queue = _build_hypothesis_queue(field_map)
    queue = apply_policy_feedback(
        queue,
        field_map,
        policy_feedback,
        mode=policy_feedback_mode,
    )
    return {
        "behavioral_mechanism_inventory": inventory,
        "behavioral_proxy_field_map": field_map,
        "candidate_hypothesis_queue": queue,
    }


def write_candidate_generation_artifacts(
    fields: Sequence[Mapping[str, Any]],
    output_dir: Path | str,
    *,
    policy_feedback: Mapping[str, Any] | None = None,
    policy_feedback_mode: str = "shadow",
) -> dict[str, Path]:
    artifacts = build_candidate_generation_artifacts(
        fields,
        policy_feedback=policy_feedback,
        policy_feedback_mode=policy_feedback_mode,
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    file_names = {
        "behavioral_mechanism_inventory": "behavioral_mechanism_inventory.json",
        "behavioral_proxy_field_map": "behavioral_proxy_field_map.json",
        "candidate_hypothesis_queue": "candidate_hypothesis_queue.json",
    }
    for key, file_name in file_names.items():
        path = output / file_name
        path.write_text(json.dumps(artifacts[key], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written[key] = path
    return written


def _build_inventory() -> dict[str, Any]:
    return {
        "version": 1,
        "source": "behavioral_candidate_generation_spec",
        "objective_priority": ["submission_quality", "distinct_champion_quantity"],
        "mechanisms": [
            {
                "mechanism": seed.mechanism,
                "zh_name": seed.zh_name,
                "behavioral_logic": seed.behavioral_logic,
                "market_condition": seed.market_condition,
                "expected_alpha_direction": seed.expected_alpha_direction,
                "proxy_requirements": list(seed.proxy_requirements),
                "kill_conditions": list(seed.kill_conditions),
                "quality_risks": list(seed.quality_risks),
                "generation_policy": seed.generation_policy,
            }
            for seed in MECHANISM_SEEDS
        ],
    }


def _build_field_map(fields: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    mappings = []
    for seed in MECHANISM_SEEDS:
        matched = _match_fields(fields, seed)
        non_price_volume_fields = [field for field in matched if not _is_price_volume_field(field)]
        proxy_strength = _proxy_strength(non_price_volume_fields)
        mappings.append(
            {
                "mechanism": seed.mechanism,
                "field_families": list(seed.field_families),
                "preferred_field_patterns": list(seed.preferred_field_patterns),
                "required_secondary_proxies": list(seed.required_secondary_proxies),
                "sample_fields": [_field_sample(field) for field in non_price_volume_fields[:12]],
                "proxy_strength": proxy_strength,
                "proxy_failure_modes": _proxy_failure_modes(seed, proxy_strength),
            }
        )
    return {
        "version": 1,
        "source": "wqb_behavioral_candidate_generation_field_map",
        "quality_rule": "primary proxy must not be pure price-volume",
        "mappings": mappings,
    }


def _build_hypothesis_queue(field_map: Mapping[str, Any]) -> dict[str, Any]:
    mappings = {row["mechanism"]: row for row in field_map.get("mappings", []) if isinstance(row, dict)}
    hypotheses = []
    for seed in MECHANISM_SEEDS:
        mapping = mappings.get(seed.mechanism) or {}
        if mapping.get("proxy_strength") == "none":
            continue
        sample_fields = mapping.get("sample_fields") or []
        if not sample_fields:
            continue
        primary_proxy = str(sample_fields[0].get("id") or "")
        if primary_proxy.lower() in PRICE_VOLUME_PROXY_IDS:
            continue
        hypotheses.append(
            {
                "hypothesis_id": f"H_{seed.mechanism}_001",
                "mechanism": seed.mechanism,
                "claim": seed.claim_template,
                "primary_proxy": primary_proxy,
                "secondary_proxy": _pick_secondary_proxy(seed),
                "risk_filter": _pick_risk_filter(seed),
                "expected_holding_logic": seed.expected_holding_logic,
                "kill_conditions": list(seed.kill_conditions),
                "success_criteria": ["near_pass", "low_self_corr_cluster_champion", "submit_ready_after_live_recheck"],
                "skeleton_template": seed.skeleton_template,
                "parameter_space": {key: list(value) for key, value in seed.parameter_space.items()},
                "preflight_requirements": [
                    "mechanism_in_inventory",
                    "proxy_strength_not_none",
                    "non_price_volume_primary_proxy",
                    "has_secondary_proxy",
                    "has_kill_condition",
                    "not_duplicate_skeleton",
                ],
                "budget_reason": "probe high-quality behavioral thesis before scale",
                "wqb_action_lane": "probe",
            }
        )
    return {
        "version": 1,
        "source": "behavioral_candidate_generation_spec",
        "quality_priority": "submission_quality_first_quantity_second",
        "hypotheses": hypotheses,
    }


def _match_fields(fields: Sequence[Mapping[str, Any]], seed: MechanismSeed) -> list[Mapping[str, Any]]:
    matched: list[tuple[float, Mapping[str, Any]]] = []
    seen = set()
    for field in fields:
        field_id = str(field.get("id") or field.get("name") or "")
        if not field_id or field_id in seen:
            continue
        score = _field_match_score(field, seed)
        if score > 0:
            matched.append((score, field))
            seen.add(field_id)
    return [
        field
        for _, field in sorted(
            matched,
            key=lambda item: (
                _is_price_volume_field(item[1]),
                -item[0],
                -_number(item[1].get("coverage")),
                str(item[1].get("id") or ""),
            ),
        )
    ]


def _field_match_score(field: Mapping[str, Any], seed: MechanismSeed) -> float:
    text = _field_text(field)
    required = MECHANISM_REQUIRED_MATCHES.get(seed.mechanism)
    if required and not any(token in text for token in required):
        return 0.0

    score = 0.0
    field_id = str(field.get("id") or field.get("name") or "").lower()
    dataset = str(field.get("dataset_id") or field.get("dataset_name") or "").lower()
    for pattern in seed.preferred_field_patterns:
        token = pattern.lower()
        if token in text:
            score += 2.0 + min(len(token), 12) / 4
        if token in field_id:
            score += 1.5
        if token in dataset:
            score += 1.0
    for noise in GENERIC_FIELD_NOISE:
        if noise in text:
            score -= 5.0
    if "flag" in field_id:
        score -= 1.0
    if _is_price_volume_field(field):
        score -= 4.0
    return score


def _field_text(field: Mapping[str, Any]) -> str:
    return " ".join(
        str(field.get(key) or "")
        for key in ("id", "name", "description", "dataset_id", "dataset_name", "category_name", "subcategory_name")
    ).lower()


def _is_price_volume_field(field: Mapping[str, Any]) -> bool:
    field_id = str(field.get("id") or field.get("name") or "").lower()
    dataset = str(field.get("dataset_id") or field.get("dataset_name") or "").lower()
    if field_id in PRICE_VOLUME_PROXY_IDS:
        return True
    return dataset.startswith("pv") and not any(token in field_id for token in ("relation", "sentiment"))


def _field_sample(field: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(field.get("id") or field.get("name") or ""),
        "dataset_id": str(field.get("dataset_id") or ""),
        "description": str(field.get("description") or "")[:180],
        "coverage": _number(field.get("coverage")),
        "userCount": int(_number(field.get("userCount"))),
        "alphaCount": int(_number(field.get("alphaCount"))),
    }


def _proxy_strength(fields: Sequence[Mapping[str, Any]]) -> str:
    count = len(fields)
    coverage = sum(_number(field.get("coverage")) for field in fields) / count if count else 0.0
    if count >= 3 and coverage >= 0.75:
        return "strong"
    if count >= 2:
        return "medium"
    if count == 1:
        return "weak"
    return "none"


def _proxy_failure_modes(seed: MechanismSeed, proxy_strength: str) -> list[str]:
    if proxy_strength == "none":
        return ["no_wqb_field_proxy", "block_from_hypothesis_queue"]
    risks = list(seed.quality_risks[:2])
    risks.append("proxy_noise_or_duplicate_skeleton")
    return risks


def _pick_secondary_proxy(seed: MechanismSeed) -> str:
    return seed.required_secondary_proxies[0] if seed.required_secondary_proxies else "volatility_filter"


def _pick_risk_filter(seed: MechanismSeed) -> str:
    for proxy in seed.required_secondary_proxies:
        if "filter" in proxy or "risk" in proxy or "crowding" in proxy:
            return proxy
    return "volatility_filter"


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
