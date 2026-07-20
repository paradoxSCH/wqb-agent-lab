"""Alpha 表达式生成引擎。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Iterable, Iterator, Sequence

from wqb_agent_lab.runtime.config import SimulationDefaults


PRICE_FIELDS = ["close", "open", "high", "low", "vwap"]
ACTIVITY_FIELDS = ["volume", "adv20", "cap", "sharesout"]
RETURN_FIELDS = ["returns"]
FUNDAMENTAL_FIELDS = [
    "assets",
    "liabilities",
    "equity",
    "revenue",
    "net_income",
    "ebitda",
    "debt",
    "cash",
    "capex",
]
ANALYST_FIELDS = ["target_price", "rating_mean", "num_estimates", "eps_estimate"]
MODEL_FIELDS = ["alpha_signal", "risk_signal", "sentiment_signal"]

DEFAULT_FIELDS = [*PRICE_FIELDS, *ACTIVITY_FIELDS[:2], *RETURN_FIELDS]
DEFAULT_LOOKBACKS = [1, 3, 5, 10, 20, 60]
DEFAULT_SHORT_LONG_PAIRS = [(5, 20), (10, 60), (20, 120)]

FIELDS_BY_CATEGORY = {
    "pv": [*PRICE_FIELDS, *ACTIVITY_FIELDS, *RETURN_FIELDS],
    "price_volume": [*PRICE_FIELDS, *ACTIVITY_FIELDS, *RETURN_FIELDS],
    "fundamental": FUNDAMENTAL_FIELDS,
    "analyst": ANALYST_FIELDS,
    "model": MODEL_FIELDS,
}

STANDARD_FIELD_PATTERNS = [
    "rank({field})",
    "rank(ts_delta({field}, 5))",
    "rank(ts_delta({field}, 20))",
    "-rank(ts_std_dev({field}, 20))",
    "ts_zscore({field}, 20)",
    "group_rank({field}, sector)",
    "group_rank(ts_delta({field}, 5), industry)",
]

TEMPLATES = [
    "rank(ts_delta({field}, {d}))",
    "-rank(ts_std_dev({field}, {d}))",
    "ts_zscore({field}, {d})",
    "rank({field} / ts_mean({field}, {d}))",
    "rank(ts_decay_linear({field}, {d}))",
    "rank(ts_rank({field}, {d}))",
    "group_rank(ts_delta({field}, {d}), sector)",
    "group_rank({field}, industry)",
]

CORRELATION_TEMPLATES = [
    "-ts_corr(rank({field_a}), rank({field_b}), {d})",
]

RATIO_TEMPLATES = [
    "rank(ts_mean({field}, {short}) / ts_mean({field}, {long}))",
    "rank(ts_mean({field}, {short}) - ts_mean({field}, {long}))",
]


@dataclass(slots=True)
class TemplateDefinition:
    """Alpha 模板定义。"""

    name: str
    pattern: str
    params: dict[str, Sequence[Any]]
    category: str = "generic"
    description: str = ""


@dataclass(slots=True)
class GenerationConstraints:
    """表达式生成阶段的质量约束。"""

    max_expression_length: int = 200
    min_field_occurrences: int = 1
    forbid_redundant_wraps: bool = True
    forbidden_fragments: tuple[str, ...] = (
        "rank(rank(",
        "zscore(zscore(",
        "group_rank(group_rank(",
    )


@dataclass(slots=True)
class FieldCandidate:
    """候选字段的标准化描述。"""

    field_id: str
    dataset_id: str
    category: str
    coverage: float
    alpha_count: int
    value_score: float
    opportunity_score: float
    raw: dict[str, Any] = field(default_factory=dict)


DEFAULT_CONSTRAINTS = GenerationConstraints()


DEFAULT_TEMPLATE_LIBRARY = [
    TemplateDefinition(
        name="simple_momentum",
        pattern="rank(ts_delta({field}, {d}))",
        params={"field": DEFAULT_FIELDS, "d": DEFAULT_LOOKBACKS},
        category="pv",
        description="最基础的时序动量模板。",
    ),
    TemplateDefinition(
        name="low_volatility",
        pattern="-rank(ts_std_dev({field}, {d}))",
        params={"field": ["returns", "close", "volume"], "d": [10, 20, 60]},
        category="pv",
        description="低波动偏好模板。",
    ),
    TemplateDefinition(
        name="mean_reversion",
        pattern="-({field} - ts_mean({field}, {d})) / ts_std_dev({field}, {d})",
        params={"field": ["close", "returns", "vwap"], "d": [5, 10, 20, 60]},
        category="pv",
        description="均值回归模板。",
    ),
    TemplateDefinition(
        name="fundamental_quality",
        pattern="rank(ts_delta({field}, {d}))",
        params={"field": ["revenue", "net_income", "ebitda", "cash"], "d": [20, 60, 120]},
        category="fundamental",
        description="基本面趋势模板。",
    ),
    TemplateDefinition(
        name="fundamental_balance_sheet",
        pattern="rank({field_a} / {field_b})",
        params={
            "field_a": ["assets", "cash", "equity"],
            "field_b": ["liabilities", "debt"],
        },
        category="fundamental",
        description="资产负债表相对强弱模板。",
    ),
    TemplateDefinition(
        name="analyst_revision",
        pattern="rank(ts_delta({field}, {d}))",
        params={"field": ANALYST_FIELDS, "d": [5, 20, 60]},
        category="analyst",
        description="分析师预期与评级修正模板。",
    ),
    TemplateDefinition(
        name="model_cross_section",
        pattern="group_rank({field}, {group})",
        params={"field": MODEL_FIELDS, "group": ["sector", "industry"]},
        category="model",
        description="模型信号组内排序模板。",
    ),
]


def normalize_expression(expression: str) -> str:
    """规范化表达式，便于去重与比较。"""
    compact = re.sub(r"\s+", "", expression)
    return compact.strip()


def extract_field_tokens(expression: str) -> set[str]:
    """提取表达式中的字段名候选。"""
    all_fields = {field for fields in FIELDS_BY_CATEGORY.values() for field in fields}
    all_fields.update({"sector", "industry", "subindustry", "market", "country"})
    tokens = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", expression))
    return {token for token in tokens if token in all_fields}


def has_balanced_parentheses(expression: str) -> bool:
    """检查括号是否平衡。"""
    depth = 0
    for char in expression:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth < 0:
            return False
    return depth == 0


def is_expression_valid(
    expression: str,
    constraints: GenerationConstraints | None = None,
) -> bool:
    """按 Phase 3 质量规则检查表达式。"""
    constraints = constraints or DEFAULT_CONSTRAINTS
    if not expression.strip():
        return False
    if len(expression) > constraints.max_expression_length:
        return False
    if not has_balanced_parentheses(expression):
        return False
    if constraints.forbid_redundant_wraps:
        normalized = normalize_expression(expression)
        for fragment in constraints.forbidden_fragments:
            if normalize_expression(fragment) in normalized:
                return False
    if len(extract_field_tokens(expression)) < constraints.min_field_occurrences:
        return False
    return True


def deduplicate_expressions(
    expressions: Iterable[str],
    constraints: GenerationConstraints | None = None,
) -> list[str]:
    """对表达式做规范化去重，并过滤低质量项。"""
    constraints = constraints or DEFAULT_CONSTRAINTS
    unique: list[str] = []
    seen: set[str] = set()
    for expression in expressions:
        if not is_expression_valid(expression, constraints):
            continue
        normalized = normalize_expression(expression)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(expression)
    return unique


def _expand_template(template: TemplateDefinition) -> Iterator[str]:
    """展开单个模板定义。"""
    keys = list(template.params.keys())
    values = [template.params[key] for key in keys]
    for combination in product(*values):
        params = dict(zip(keys, combination, strict=False))
        try:
            yield template.pattern.format(**params)
        except KeyError:
            continue


def generate_template_library(
    category: str | None = None,
    constraints: GenerationConstraints | None = None,
) -> list[str]:
    """从模板库批量生成表达式。"""
    templates = DEFAULT_TEMPLATE_LIBRARY
    if category:
        templates = [item for item in templates if item.category == category]
    expressions = []
    for template in templates:
        expressions.extend(_expand_template(template))
    return deduplicate_expressions(expressions, constraints)


def infer_field_category(field_record: dict[str, Any]) -> str:
    """根据字段元数据推断所属类别。"""
    raw_category = str(field_record.get("category") or "").strip().lower()
    dataset_id = str(field_record.get("datasetId") or field_record.get("dataset_id") or "").strip().lower()
    if raw_category in FIELDS_BY_CATEGORY:
        return raw_category
    if dataset_id.startswith("pv"):
        return "pv"
    if "fund" in raw_category or "fund" in dataset_id:
        return "fundamental"
    if "analyst" in raw_category or "estimate" in raw_category:
        return "analyst"
    if "model" in raw_category or "model" in dataset_id:
        return "model"
    return "unknown"


def rank_field_candidates(
    field_records: Iterable[dict[str, Any]],
    *,
    category: str | None = None,
    min_coverage: float = 0.0,
    max_alpha_count: int | None = None,
    top_n: int | None = None,
) -> list[FieldCandidate]:
    """按 coverage 与 alpha_count 为字段候选打分。"""
    candidates: list[FieldCandidate] = []
    for record in field_records:
        inferred_category = infer_field_category(record)
        if category and inferred_category != category:
            continue
        field_id = str(record.get("id") or record.get("field") or "").strip()
        if not field_id:
            continue
        coverage = float(record.get("coverage", 0) or 0)
        alpha_count = int(record.get("alphaCount", record.get("alpha_count", 0)) or 0)
        value_score = float(record.get("valueScore", record.get("value_score", 0)) or 0)
        if coverage < min_coverage:
            continue
        if max_alpha_count is not None and alpha_count > max_alpha_count:
            continue
        opportunity_score = coverage * (1 / (1 + alpha_count)) * (1 + max(0.0, value_score))
        candidates.append(
            FieldCandidate(
                field_id=field_id,
                dataset_id=str(record.get("datasetId") or record.get("dataset_id") or ""),
                category=inferred_category,
                coverage=coverage,
                alpha_count=alpha_count,
                value_score=value_score,
                opportunity_score=opportunity_score,
                raw=dict(record),
            )
        )
    candidates.sort(
        key=lambda item: (item.opportunity_score, item.coverage, -item.alpha_count, item.value_score),
        reverse=True,
    )
    if top_n is not None:
        return candidates[:top_n]
    return candidates


def generate_field_driven_alphas(
    field_records: Iterable[dict[str, Any]],
    *,
    category: str | None = None,
    min_coverage: float = 0.8,
    max_alpha_count: int = 50,
    top_n: int = 20,
    transforms: Sequence[str] | None = None,
    constraints: GenerationConstraints | None = None,
) -> list[str]:
    """基于字段候选打分结果生成表达式。"""
    transforms = transforms or STANDARD_FIELD_PATTERNS
    candidates = rank_field_candidates(
        field_records,
        category=category,
        min_coverage=min_coverage,
        max_alpha_count=max_alpha_count,
        top_n=top_n,
    )
    expressions: list[str] = []
    for candidate in candidates:
        for transform in transforms:
            expressions.append(transform.format(field=candidate.field_id))
    return deduplicate_expressions(expressions, constraints)


def generate_category_alphas(
    category: str,
    *,
    include_library: bool = True,
    lookbacks: Sequence[int] | None = None,
    constraints: GenerationConstraints | None = None,
) -> list[str]:
    """按数据类别生成 Alpha。"""
    category_key = category.lower()
    fields = FIELDS_BY_CATEGORY.get(category_key, [])
    lookbacks = list(lookbacks or DEFAULT_LOOKBACKS)
    generated: list[str] = []
    if include_library:
        generated.extend(generate_template_library(category=category_key, constraints=constraints))
    if fields:
        generated.extend(
            generate_from_templates(
                templates=TEMPLATES,
                fields=list(fields),
                lookbacks=lookbacks,
                constraints=constraints,
            )
        )
        generated.extend(
            generate_ratio_alphas(
                fields=list(fields),
                short_long_pairs=DEFAULT_SHORT_LONG_PAIRS,
                constraints=constraints,
            )
        )
    return deduplicate_expressions(generated, constraints)


def generate_from_templates(
    templates: list[str] | None = None,
    fields: list[str] | None = None,
    lookbacks: list[int] | None = None,
    constraints: GenerationConstraints | None = None,
) -> list[str]:
    """基于模板和参数网格生成 Alpha 表达式。"""
    templates = templates or TEMPLATES
    fields = fields or DEFAULT_FIELDS
    lookbacks = lookbacks or DEFAULT_LOOKBACKS
    expressions: list[str] = []
    for template, field_name, d in product(templates, fields, lookbacks):
        try:
            expressions.append(template.format(field=field_name, d=d))
        except KeyError:
            continue
    return deduplicate_expressions(expressions, constraints)


def generate_correlation_alphas(
    field_pairs: list[tuple[str, str]] | None = None,
    lookbacks: list[int] | None = None,
    constraints: GenerationConstraints | None = None,
) -> list[str]:
    """生成相关性类 Alpha 表达式。"""
    if field_pairs is None:
        field_pairs = [
            ("close", "volume"),
            ("returns", "volume"),
            ("high", "low"),
            ("close", "vwap"),
        ]
    lookbacks = lookbacks or DEFAULT_LOOKBACKS
    expressions: list[str] = []
    for template in CORRELATION_TEMPLATES:
        for (fa, fb), d in product(field_pairs, lookbacks):
            expressions.append(template.format(field_a=fa, field_b=fb, d=d))
    return deduplicate_expressions(expressions, constraints)


def generate_ratio_alphas(
    fields: list[str] | None = None,
    short_long_pairs: list[tuple[int, int]] | None = None,
    constraints: GenerationConstraints | None = None,
) -> list[str]:
    """生成短周期与长周期比值类 Alpha 表达式。"""
    fields = fields or DEFAULT_FIELDS
    short_long_pairs = short_long_pairs or DEFAULT_SHORT_LONG_PAIRS
    expressions: list[str] = []
    for template in RATIO_TEMPLATES:
        for field_name, (short, long) in product(fields, short_long_pairs):
            expressions.append(template.format(field=field_name, short=short, long=long))
    return deduplicate_expressions(expressions, constraints)


def build_alpha_object(
    expression: str,
    settings: SimulationDefaults | None = None,
) -> dict:
    """构造兼容 BRAIN 模拟接口的 Alpha 对象。"""
    settings = settings or SimulationDefaults()
    return {
        "type": "REGULAR",
        "settings": settings.to_dict(),
        "regular": expression,
    }
