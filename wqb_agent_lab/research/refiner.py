"""Alpha 变异与优化策略。"""

from __future__ import annotations

import random
import re
from typing import Iterator

from .alpha_generator import (
    ANALYST_FIELDS,
    DEFAULT_CONSTRAINTS,
    FUNDAMENTAL_FIELDS,
    GenerationConstraints,
    MODEL_FIELDS,
    deduplicate_expressions,
)


# 字段替换分组
FIELD_GROUPS = {
    "price": ["close", "open", "high", "low", "vwap"],
    "activity": ["volume", "adv20", "cap", "sharesout"],
    "return": ["returns"],
    "fundamental": FUNDAMENTAL_FIELDS,
    "analyst": ANALYST_FIELDS,
    "model": MODEL_FIELDS,
}

ALL_FIELDS = [f for group in FIELD_GROUPS.values() for f in group]

# 外层算子包装模板
OUTER_OPERATORS = [
    "rank({expr})",
    "group_rank({expr}, sector)",
    "group_rank({expr}, industry)",
    "group_zscore({expr}, sector)",
    "zscore({expr})",
    "ts_rank({expr}, 20)",
    "-({expr})",
]

# 回看窗口候选值
LOOKBACK_VALUES = [1, 3, 5, 10, 20, 60, 120]
LOOKBACK_OFFSETS = (-10, -5, -2, 2, 5, 10)


def mutate_field(expression: str, original_field: str, new_field: str) -> str:
    """将表达式中的字段替换为另一个字段。"""
    pattern = rf"\b{re.escape(original_field)}\b"
    return re.sub(pattern, new_field, expression)


def mutate_field_all_variants(expression: str) -> Iterator[str]:
    """遍历字段替换后的全部变体。"""
    for group_fields in FIELD_GROUPS.values():
        for field in group_fields:
            pattern = rf"\b{re.escape(field)}\b"
            if re.search(pattern, expression):
                for replacement in group_fields:
                    if replacement != field:
                        variant = re.sub(pattern, replacement, expression)
                        yield variant


def mutate_lookback(expression: str) -> Iterator[str]:
    """修改表达式中的数值窗口参数。"""
    matches = list(re.finditer(r"(?<=,\s)\d+(?=\))", expression))

    for match in matches:
        original = int(match.group())
        candidates = {value for value in LOOKBACK_VALUES if value != original}
        for offset in LOOKBACK_OFFSETS:
            shifted = original + offset
            if shifted > 0:
                candidates.add(shifted)

        for new_val in sorted(candidates):
            yield (
                expression[: match.start()]
                + str(new_val)
                + expression[match.end() :]
            )


def wrap_with_operator(expression: str) -> Iterator[str]:
    """用外层算子包装表达式。"""
    for template in OUTER_OPERATORS:
        yield template.format(expr=expression)


def mutate_window_pair(expression: str) -> Iterator[str]:
    """针对成对短长窗口结构生成扰动变体。"""
    matches = list(re.finditer(r"(?<=,\s)\d+(?=\))", expression))
    if len(matches) < 2:
        return

    first = int(matches[0].group())
    second = int(matches[1].group())
    candidates = [(3, 10), (5, 20), (10, 60), (20, 120)]
    for short, long in candidates:
        if short == first and long == second:
            continue
        updated = expression
        updated = updated[: matches[1].start()] + str(long) + updated[matches[1].end() :]
        updated = updated[: matches[0].start()] + str(short) + updated[matches[0].end() :]
        yield updated


def combine_alphas(alpha_a: str, alpha_b: str) -> Iterator[str]:
    """生成两个 Alpha 的组合变体。"""
    yield f"rank({alpha_a}) + rank({alpha_b})"
    yield f"rank({alpha_a}) - rank({alpha_b})"
    yield f"rank({alpha_a}) * rank({alpha_b})"
    yield f"group_rank({alpha_a}, sector) + rank({alpha_b})"
    yield f"({alpha_a}) + ({alpha_b})"


def generate_mutations(
    expression: str,
    max_per_type: int = 10,
    constraints: GenerationConstraints | None = None,
) -> list[str]:
    """生成指定表达式的一组多样化变体。"""
    constraints = constraints or DEFAULT_CONSTRAINTS
    mutations = set()

    field_variants = list(mutate_field_all_variants(expression))
    if field_variants:
        mutations.update(random.sample(field_variants, min(max_per_type, len(field_variants))))

    lookback_variants = list(mutate_lookback(expression))
    if lookback_variants:
        mutations.update(random.sample(lookback_variants, min(max_per_type, len(lookback_variants))))

    pair_variants = list(mutate_window_pair(expression))
    if pair_variants:
        mutations.update(random.sample(pair_variants, min(max_per_type, len(pair_variants))))

    mutations.update(wrap_with_operator(expression))
    mutations.discard(expression)

    return deduplicate_expressions(mutations, constraints)
