"""Alpha 绩效指标提取、多维评分与迭代优化。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from wqb_agent_lab.research.alpha_generator import (
    GenerationConstraints,
    deduplicate_expressions,
    normalize_expression,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 基础数据模型
# ---------------------------------------------------------------------------


@dataclass
class AlphaMetrics:
    """从模拟结果中提取出的绩效指标。"""

    expression: str
    sharpe: float = 0.0
    fitness: float = 0.0
    turnover: float = 0.0
    returns: float = 0.0
    drawdown: float = 0.0
    margin: float = 0.0
    alpha_id: str = ""
    composite_score: float = 0.0

    @property
    def meets_submission_criteria(self) -> bool:
        """是否满足基础提交阈值。"""
        return self.sharpe >= 1.25 and self.fitness >= 1.0 and self.turnover <= 0.7


@dataclass
class FilterCriteria:
    """Alpha 过滤阈值。"""

    min_sharpe: float = 1.25
    min_fitness: float = 1.0
    max_turnover: float = 0.7
    min_returns: float = 0.0
    max_drawdown: float = 1.0


@dataclass
class ScoreWeights:
    """综合评分权重配置。"""

    sharpe: float = 0.35
    fitness: float = 0.25
    turnover: float = 0.15
    returns: float = 0.15
    drawdown: float = 0.10

    def as_dict(self) -> dict[str, float]:
        return {
            "sharpe": self.sharpe,
            "fitness": self.fitness,
            "turnover": self.turnover,
            "returns": self.returns,
            "drawdown": self.drawdown,
        }


@dataclass
class IterationConfig:
    """迭代优化流程配置。"""

    max_rounds: int = 3
    mutations_per_winner: int = 10
    filter_criteria: FilterCriteria = field(default_factory=FilterCriteria)
    score_weights: ScoreWeights = field(default_factory=ScoreWeights)
    diversity_min_distance: float = 0.3
    top_k_per_round: int = 20
    generation_constraints: GenerationConstraints | None = None


# ---------------------------------------------------------------------------
# 指标提取
# ---------------------------------------------------------------------------


def extract_metrics(expression: str, sim_result: dict[str, Any]) -> AlphaMetrics | None:
    """从模拟响应中提取核心指标。

    BRAIN Alpha 对象将绩效指标嵌套在 ``is``（in-sample）字典中。
    """
    if not sim_result.get("success") or not sim_result.get("data"):
        return None

    data = sim_result["data"]

    # 指标在 data["is"] 字典内；回退到 data 本身以保持向后兼容
    is_data = data.get("is") or data

    try:
        return AlphaMetrics(
            expression=expression,
            sharpe=float(is_data.get("sharpe", 0)),
            fitness=float(is_data.get("fitness", 0)),
            turnover=float(is_data.get("turnover", 0)),
            returns=float(is_data.get("returns", 0)),
            drawdown=float(is_data.get("drawdown", 0)),
            margin=float(is_data.get("margin", 0)),
            alpha_id=str(data.get("id", "")),
        )
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 多指标综合评分
# ---------------------------------------------------------------------------


def compute_composite_score(
    metrics: AlphaMetrics,
    weights: ScoreWeights | None = None,
) -> float:
    """
    计算综合评分。

    对每个指标做 0-1 归一化映射后加权求和：
    - Sharpe:   线性映射到 [0, 3] → [0, 1]
    - Fitness:  线性映射到 [0, 2] → [0, 1]
    - Turnover: 反向映射到 [0, 1] → [1, 0]
    - Returns:  线性映射到 [0, 0.1] → [0, 1]
    - Drawdown: 反向映射到 [0, 0.5] → [1, 0]
    """
    weights = weights or ScoreWeights()

    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    s_sharpe = _clamp(metrics.sharpe / 3.0, 0, 1)
    s_fitness = _clamp(metrics.fitness / 2.0, 0, 1)
    s_turnover = 1 - _clamp(metrics.turnover, 0, 1)
    s_returns = _clamp(metrics.returns / 0.1, 0, 1)
    s_drawdown = 1 - _clamp(metrics.drawdown / 0.5, 0, 1)

    score = (
        weights.sharpe * s_sharpe
        + weights.fitness * s_fitness
        + weights.turnover * s_turnover
        + weights.returns * s_returns
        + weights.drawdown * s_drawdown
    )
    return round(score, 6)


def assign_composite_scores(
    metrics_list: list[AlphaMetrics],
    weights: ScoreWeights | None = None,
) -> list[AlphaMetrics]:
    """为一组 AlphaMetrics 计算并写入综合评分。"""
    for m in metrics_list:
        m.composite_score = compute_composite_score(m, weights)
    return metrics_list


# ---------------------------------------------------------------------------
# 过滤与排序
# ---------------------------------------------------------------------------


def filter_alphas(
    metrics_list: list[AlphaMetrics],
    criteria: FilterCriteria | None = None,
) -> list[AlphaMetrics]:
    """按给定阈值过滤 Alpha。"""
    criteria = criteria or FilterCriteria()

    return [
        m
        for m in metrics_list
        if m.sharpe >= criteria.min_sharpe
        and m.fitness >= criteria.min_fitness
        and m.turnover <= criteria.max_turnover
        and m.returns >= criteria.min_returns
        and m.drawdown <= criteria.max_drawdown
    ]


def rank_alphas(
    metrics_list: list[AlphaMetrics],
    sort_by: str = "composite_score",
    descending: bool = True,
) -> list[AlphaMetrics]:
    """按指定指标对 Alpha 排序。"""
    return sorted(
        metrics_list,
        key=lambda m: getattr(m, sort_by, 0),
        reverse=descending,
    )


# ---------------------------------------------------------------------------
# 多样性约束
# ---------------------------------------------------------------------------


def _expression_distance(expr_a: str, expr_b: str) -> float:
    """
    计算两个表达式的 Jaccard token 距离（0=完全相同，1=完全不同）。
    """
    import re as _re

    tokens_a = set(_re.findall(r"[a-zA-Z_]\w*|\d+", normalize_expression(expr_a)))
    tokens_b = set(_re.findall(r"[a-zA-Z_]\w*|\d+", normalize_expression(expr_b)))
    if not tokens_a and not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return 1 - len(intersection) / len(union)


def ensure_diversity(
    metrics_list: list[AlphaMetrics],
    min_distance: float = 0.3,
) -> list[AlphaMetrics]:
    """
    贪心去除结构过近的候选，保证入选集两两距离 >= min_distance。

    输入需已按评分降序排列。
    """
    selected: list[AlphaMetrics] = []
    for candidate in metrics_list:
        is_diverse = all(
            _expression_distance(candidate.expression, s.expression) >= min_distance
            for s in selected
        )
        if is_diverse:
            selected.append(candidate)
    return selected


# ---------------------------------------------------------------------------
# 迭代优化闭环
# ---------------------------------------------------------------------------


def run_iteration(
    seed_expressions: list[str],
    simulate_fn,
    config: IterationConfig | None = None,
) -> dict[str, Any]:
    """
    执行「模拟 → 过滤 → 变异 → 再模拟」迭代流程。

    参数:
        seed_expressions: 初始候选表达式列表
        simulate_fn: 模拟回调 (list[str]) -> list[dict]，返回模拟结果列表
        config: 迭代配置

    返回:
        包含 rounds 详情和 best 候选的汇总字典
    """
    from wqb_agent_lab.research.refiner import generate_mutations

    config = config or IterationConfig()
    all_seen: set[str] = set()
    best_overall: list[AlphaMetrics] = []
    round_details: list[dict[str, Any]] = []

    current_expressions = deduplicate_expressions(
        seed_expressions, config.generation_constraints
    )

    for round_idx in range(1, config.max_rounds + 1):
        new_expressions = [e for e in current_expressions if normalize_expression(e) not in all_seen]
        if not new_expressions:
            logger.info("第 %d 轮：无新表达式可模拟，提前终止", round_idx)
            break

        all_seen.update(normalize_expression(e) for e in new_expressions)

        logger.info("第 %d 轮：模拟 %d 个候选表达式", round_idx, len(new_expressions))
        sim_results = simulate_fn(new_expressions)

        metrics_list: list[AlphaMetrics] = []
        for expr, result in zip(new_expressions, sim_results, strict=False):
            m = extract_metrics(expr, result)
            if m is not None:
                metrics_list.append(m)

        assign_composite_scores(metrics_list, config.score_weights)
        passed = filter_alphas(metrics_list, config.filter_criteria)
        ranked = rank_alphas(passed, sort_by="composite_score")
        diverse = ensure_diversity(ranked, min_distance=config.diversity_min_distance)
        winners = diverse[: config.top_k_per_round]

        round_details.append({
            "round": round_idx,
            "simulated": len(new_expressions),
            "extracted": len(metrics_list),
            "passed_filter": len(passed),
            "after_diversity": len(diverse),
            "winners": len(winners),
        })

        logger.info(
            "第 %d 轮结果：模拟 %d → 提取 %d → 过滤 %d → 去重 %d → 优胜 %d",
            round_idx,
            len(new_expressions),
            len(metrics_list),
            len(passed),
            len(diverse),
            len(winners),
        )

        best_overall.extend(winners)

        if round_idx < config.max_rounds and winners:
            next_candidates: list[str] = []
            for w in winners:
                mutations = generate_mutations(
                    w.expression,
                    max_per_type=config.mutations_per_winner,
                    constraints=config.generation_constraints,
                )
                next_candidates.extend(mutations)
            current_expressions = deduplicate_expressions(
                next_candidates, config.generation_constraints
            )
        else:
            current_expressions = []

    assign_composite_scores(best_overall, config.score_weights)
    best_overall = rank_alphas(best_overall, sort_by="composite_score")
    best_overall = ensure_diversity(best_overall, min_distance=config.diversity_min_distance)

    return {
        "rounds": round_details,
        "total_rounds": len(round_details),
        "best": best_overall,
        "best_count": len(best_overall),
    }
