"""Principle-driven alpha family generator.

The generator mines local submitted/high-performing alpha archives to extract
working signal principles, synthesizes deterministic seed families from the
current field pool, and optionally asks an LLM to mutate those seeds into more
diverse candidates.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.llm_provider import LLMProvider, LLMProviderError, LLMRequest


MAX_TOKENS = 4000
TEMPERATURE = 0.75

FASTEXPR_OPERATORS_CORE = """
group_rank(expr, group)
rank(expr)
ts_delta(field, days)
ts_mean(field, days)
ts_zscore(field, days)
ts_corr(a, b, days)
ts_rank(field, days)
ts_decay_linear(field, days)
ts_std_dev(field, days)
if_else(condition, a, b)
/  +  -  *
"""

PASS_SHARPE = 1.25
PASS_FITNESS = 1.00
PASS_TURNOVER = 0.70

METRIC_KEYS = ("sharpe", "fitness", "turnover", "returns", "drawdown")
DEFAULT_REFERENCE_PATHS = (
    Path("submitted_alphas/index.json"),
    Path(".local/data/submitted_alphas.json"),
    Path("submitted_alphas/round2_safe_candidates.json"),
    Path("submitted_alphas/round2_top_candidates.json"),
)
PRICE_REVERSAL_EXPR = "rank(-returns) - rank(close - ts_mean(close, 5))"
LOW_SELF_CORR = "low"
MEDIUM_SELF_CORR = "medium"
HIGH_SELF_CORR = "high"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return deepcopy(default)


def _slug(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-")


def _normalize_expression(expression: str) -> str:
    return re.sub(r"\s+", " ", expression.strip())


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _score_row(row: dict[str, Any]) -> tuple[float, float, float, float]:
    metrics = row.get("metrics") or {}
    return (
        _safe_float(metrics.get("fitness")),
        _safe_float(metrics.get("sharpe")),
        _safe_float(metrics.get("returns")),
        -_safe_float(metrics.get("drawdown")),
    )


def _is_expression_valid(expression: str) -> bool:
    if not expression or len(expression) > 400:
        return False
    depth = 0
    for ch in expression:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth < 0:
            return False
    return depth == 0


def _extract_field_ids(quadrant_rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("field_id", "")).strip() for row in quadrant_rows if row.get("field_id")}


def _validate_expression_uses_known_fields(expression: str, known_fields: set[str]) -> bool:
    tokens = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", expression))
    operators = {
        "group_rank",
        "rank",
        "ts_delta",
        "ts_mean",
        "ts_zscore",
        "ts_corr",
        "ts_rank",
        "ts_decay_linear",
        "ts_std_dev",
        "if_else",
        "sector",
        "industry",
        "subindustry",
        "market",
        "close",
        "cap",
        "returns",
        "volume",
    }
    return not (tokens - known_fields - operators)


def _dedupe_reference_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_expr: dict[str, dict[str, Any]] = {}
    for row in rows:
        expression = _normalize_expression(str(row.get("expression", "")))
        if not expression:
            continue
        existing = best_by_expr.get(expression)
        candidate = {**row, "expression": expression}
        if existing is None or _score_row(candidate) > _score_row(existing):
            best_by_expr[expression] = candidate
    return sorted(best_by_expr.values(), key=_score_row, reverse=True)


def _extract_group(expression: str) -> str:
    matches = re.findall(r",\s*(subindustry|industry|sector)\s*\)", expression)
    return matches[-1] if matches else "subindustry"


def _extract_divisor(expression: str, pattern: str, default: int) -> int:
    match = re.search(pattern, expression)
    if not match:
        return default
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return default


def _infer_field_role(field_id: str, description: str) -> str:
    haystack = f"{field_id} {description}".lower()
    if any(token in haystack for token in ("surprise", "revision", "estimate", "delta", "change", "momentum", "growth")):
        return "revision"
    if any(token in haystack for token in ("sentiment", "rating", "score", "rank", "ratio", "spread", "vol", "volatility", "implied")):
        return "score"
    if any(token in haystack for token in ("price", "per_share", "eps", "target")):
        return "per_share"
    return "level"


def _normalizer_for_field(field_id: str, description: str) -> str | None:
    role = _infer_field_role(field_id, description)
    if role == "per_share":
        return "close"
    if role == "score":
        return None
    return "cap"


def _field_term(field_id: str, description: str) -> str:
    normalizer = _normalizer_for_field(field_id, description)
    if normalizer is None:
        return field_id
    return f"{field_id} / {normalizer}"


def _reference_row(raw: dict[str, Any], source: str) -> dict[str, Any] | None:
    expression = str(raw.get("expression") or (raw.get("regular") or {}).get("code", "")).strip()
    if not expression:
        return None
    settings = raw.get("settings") or {}
    metrics = raw.get("metrics") or {key: raw.get(key) for key in METRIC_KEYS}
    return {
        "alpha_id": raw.get("alpha_id") or raw.get("id") or "",
        "expression": expression,
        "settings": settings,
        "metrics": {key: _safe_float(metrics.get(key)) for key in METRIC_KEYS},
        "status": raw.get("status", ""),
        "source": source,
    }


def _load_reference_alphas(workspace_root: Path, run_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative_path in DEFAULT_REFERENCE_PATHS:
        path = workspace_root / relative_path
        payload = _read_json(path, [])
        if isinstance(payload, dict) and "alphas" in payload:
            payload = payload.get("alphas", [])
        elif isinstance(payload, dict) and "candidates" in payload:
            payload = payload.get("candidates", [])
        if not isinstance(payload, list):
            continue
        for item in payload:
            normalized = _reference_row(item, relative_path.as_posix())
            if normalized:
                rows.append(normalized)

    best_parameters = _read_json(run_dir / "best_parameters.json", {})
    for item in best_parameters.get("best_by_skeleton", []):
        normalized = _reference_row(item, "best_parameters.json")
        if normalized:
            rows.append(normalized)

    return _dedupe_reference_rows(rows)


def _learn_principles(reference_rows: list[dict[str, Any]]) -> dict[str, Any]:
    anchor_rows = [row for row in reference_rows if "group_rank(" in row["expression"] and "/ cap" in row["expression"]]
    reversal_rows = [row for row in reference_rows if "rank(-returns)" in row["expression"]]
    short_reversal_rows = [row for row in reference_rows if "ts_delta(close, 3)" in row["expression"]]
    blended_inside_rows = [
        row
        for row in reversal_rows
        if row["expression"].startswith("group_rank(") and "rank(-returns)" in row["expression"].split(")", 1)[0]
    ]

    grouped_counter = Counter(_extract_group(row["expression"]) for row in reference_rows)
    inside_group_counter = Counter(_extract_group(row["expression"]) for row in blended_inside_rows)
    anchor_group_counter = Counter(_extract_group(row["expression"]) for row in anchor_rows)

    rank_returns_divisors = Counter(
        _extract_divisor(row["expression"], r"rank\(-returns\)\s*/\s*(\d+)", 10)
        for row in reversal_rows
        if "rank(-returns) /" in row["expression"]
    )
    anchor_divisors = Counter(
        _extract_divisor(row["expression"], r"/\s*cap\s*/\s*(\d+)", 10)
        for row in blended_inside_rows
        if "/ cap /" in row["expression"]
    )
    short_reversal_divisors = Counter(
        _extract_divisor(row["expression"], r"ts_delta\(close,\s*3\)\s*/\s*(\d+)", 10)
        for row in short_reversal_rows
    )

    top_examples = sorted(reference_rows, key=_score_row, reverse=True)[:8]
    return {
        "top_examples": top_examples,
        "preferred_group": grouped_counter.most_common(1)[0][0] if grouped_counter else "subindustry",
        "anchor_group": anchor_group_counter.most_common(1)[0][0] if anchor_group_counter else "subindustry",
        "inside_blend_group": inside_group_counter.most_common(1)[0][0] if inside_group_counter else "industry",
        "rank_returns_divisor": rank_returns_divisors.most_common(1)[0][0] if rank_returns_divisors else 10,
        "anchor_divisor": anchor_divisors.most_common(1)[0][0] if anchor_divisors else 10,
        "short_reversal_divisor": short_reversal_divisors.most_common(1)[0][0] if short_reversal_divisors else 10,
    }


def _infer_archetype(expression: str, signal_idea: str, fields: list[str]) -> str:
    normalized = _normalize_expression(expression)
    signal_slug = _slug(signal_idea)
    if PRICE_REVERSAL_EXPR in normalized:
        return "price-reversion-combo"
    if "-ts_delta(close, 3)" in normalized:
        return "short-reversal-anchor"
    if normalized.startswith("group_rank(") and "rank(-returns)" in normalized:
        if len(fields) > 1:
            return "dual-anchor-reversal"
        return "reversal-anchor-blend"
    if len(fields) > 1:
        return "dual-anchor"
    if signal_slug == "normalized-anchor" or normalized.startswith("group_rank("):
        return "normalized-anchor"
    return signal_slug or "composite"


def _self_corr_risk_for_archetype(archetype: str) -> str:
    if archetype in {"reversal-anchor-blend", "price-reversion-combo", "short-reversal-anchor", "dual-anchor-reversal"}:
        return HIGH_SELF_CORR
    if archetype in {"dual-anchor", "composite"}:
        return MEDIUM_SELF_CORR
    return LOW_SELF_CORR


def _build_chassis(expression: str, fields: list[str]) -> str:
    normalized = _normalize_expression(expression)
    for field_id in sorted(fields, key=len, reverse=True):
        normalized = re.sub(rf"\b{re.escape(field_id)}\b", "FIELD", normalized)
    normalized = re.sub(r"\b(cap|close|returns|volume)\b", "SERIES", normalized)
    normalized = re.sub(r"\b(subindustry|industry|sector|market)\b", "GROUP", normalized)
    normalized = re.sub(r"\b\d+\b", "N", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _archetype_budget(max_families: int, archetype: str, risk: str) -> int:
    if risk == HIGH_SELF_CORR:
        return 1
    if archetype == "normalized-anchor":
        return max(2, min(3, max(1, max_families // 3)))
    return 1


def _build_family(
    *,
    index: int,
    dataset: str,
    family: str,
    skeleton: str,
    signal_idea: str,
    fields: list[str],
    expression: str,
    reason: str,
) -> dict[str, Any]:
    archetype = _infer_archetype(expression, signal_idea, fields)
    return {
        "family_id": f"g{index}",
        "dataset": dataset,
        "family": family,
        "skeleton": skeleton,
        "signal_idea": signal_idea,
        "fields": fields,
        "expression": expression,
        "reason": reason,
        "archetype": archetype,
        "self_corr_risk": _self_corr_risk_for_archetype(archetype),
        "chassis": _build_chassis(expression, fields),
    }


class LLMTemplateGenerator:
    """Generates alpha families from learned principles and optional LLM refinement."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider
        self.last_diagnostic: dict[str, Any] | None = None

    def generate(
        self,
        workspace_root: Path,
        run_dir: Path,
        selected_dataset: str,
        selected_fields: list[dict[str, Any]],
        max_families: int = 12,
    ) -> list[dict[str, Any]]:
        self.last_diagnostic = None
        reference_rows = _load_reference_alphas(workspace_root, run_dir)
        principles = _learn_principles(reference_rows)
        blocked_skeletons = {
            str(item.get("skeleton", "")).strip()
            for item in _read_json(run_dir / "alpha_skeleton_blocklist.json", [])
        }
        submitted_expressions = {
            _normalize_expression(row["expression"])
            for row in reference_rows
            if row.get("expression")
        }
        blocked_chassis = {
            str(item.get("chassis", "")).strip()
            for item in _read_json(run_dir / "chassis_blocklist.json", [])
            if item.get("chassis")
        }

        seed_families = self._build_seed_families(
            selected_dataset=selected_dataset,
            selected_fields=selected_fields,
            principles=principles,
            blocked_skeletons=blocked_skeletons,
            submitted_expressions=submitted_expressions,
            max_families=max_families,
        )

        prompt = self._build_prompt(
            workspace_root=workspace_root,
            run_dir=run_dir,
            selected_dataset=selected_dataset,
            selected_fields=selected_fields,
            principles=principles,
            seed_families=seed_families,
            blocked_skeletons=blocked_skeletons,
            blocked_chassis=blocked_chassis,
            submitted_expressions=submitted_expressions,
        )

        llm_families: list[dict[str, Any]] = []
        if self.provider is not None:
            try:
                content = self._call_llm(prompt)
                llm_families = self._parse_llm_json(content)
            except LLMProviderError as exc:
                self.last_diagnostic = exc.to_dict()

        quadrant_rows = _read_json(workspace_root / ".local" / "data" / "field_quadrant_analysis.json", [])
        known_fields = _extract_field_ids(quadrant_rows)
        known_fields.update({"close", "cap", "returns", "volume"})
        return self._validate_and_merge_families(
            candidate_families=llm_families,
            fallback_families=seed_families,
            selected_dataset=selected_dataset,
            selected_fields=selected_fields,
            known_fields=known_fields,
            blocked_skeletons=blocked_skeletons,
            blocked_chassis=blocked_chassis,
            submitted_expressions=submitted_expressions,
            max_families=max_families,
        )

    def _build_seed_families(
        self,
        *,
        selected_dataset: str,
        selected_fields: list[dict[str, Any]],
        principles: dict[str, Any],
        blocked_skeletons: set[str],
        submitted_expressions: set[str],
        max_families: int,
    ) -> list[dict[str, Any]]:
        families: list[dict[str, Any]] = []
        seen_skeletons: set[str] = set()
        seen_expressions: set[str] = set(submitted_expressions)

        top_fields = selected_fields[: min(6, len(selected_fields))]
        inside_group = str(principles.get("inside_blend_group", "industry"))
        anchor_group = str(principles.get("anchor_group", "subindustry"))
        rev_div = int(principles.get("rank_returns_divisor", 10) or 10)
        anchor_div = int(principles.get("anchor_divisor", 10) or 10)
        short_rev_div = int(principles.get("short_reversal_divisor", 10) or 10)

        def add_family(family: dict[str, Any]) -> None:
            skeleton = str(family.get("skeleton", "")).strip()
            expression = _normalize_expression(str(family.get("expression", "")))
            if not skeleton or not expression:
                return
            if skeleton in blocked_skeletons or skeleton in seen_skeletons:
                return
            if expression in seen_expressions:
                return
            family["expression"] = expression
            families.append(family)
            seen_skeletons.add(skeleton)
            seen_expressions.add(expression)

        def add_built_family(family: dict[str, Any] | None) -> None:
            if family is not None:
                add_family(family)

        def build_anchor_family(row: dict[str, Any]) -> dict[str, Any] | None:
            field_id = str(row.get("field_id", "")).strip()
            description = str(row.get("description", "")).strip()
            if not field_id:
                return None
            field_term = _field_term(field_id, description)
            base_slug = _slug(field_id)
            return _build_family(
                index=len(families) + 1,
                dataset=selected_dataset,
                family=f"{field_id} normalized anchor",
                skeleton=f"{base_slug}-anchor-{anchor_group}",
                signal_idea="Normalized anchor",
                fields=[field_id],
                expression=f"group_rank({field_term}, {anchor_group})",
                reason="Uses the strongest submitted baseline principle: normalized cross-sectional anchor before adding faster components.",
            )

        def build_reversal_blend_family(row: dict[str, Any]) -> dict[str, Any] | None:
            field_id = str(row.get("field_id", "")).strip()
            description = str(row.get("description", "")).strip()
            if not field_id:
                return None
            field_term = _field_term(field_id, description)
            base_slug = _slug(field_id)
            return _build_family(
                index=len(families) + 1,
                dataset=selected_dataset,
                family=f"{field_id} reversal blend",
                skeleton=f"{base_slug}-returns-blend-{inside_group}",
                signal_idea="Reversal blend",
                fields=[field_id],
                expression=f"group_rank(rank(-returns) / {rev_div} + {field_term} / {anchor_div}, {inside_group})",
                reason="Instantiates the strongest archived archetype: mild short-term reversal blended with a normalized fundamental anchor inside group_rank.",
            )

        def build_price_reversion_family(row: dict[str, Any]) -> dict[str, Any] | None:
            field_id = str(row.get("field_id", "")).strip()
            description = str(row.get("description", "")).strip()
            if not field_id:
                return None
            role = _infer_field_role(field_id, description)
            if role not in {"level", "revision", "per_share"}:
                return None
            field_term = _field_term(field_id, description)
            base_slug = _slug(field_id)
            return _build_family(
                index=len(families) + 1,
                dataset=selected_dataset,
                family=f"{field_id} price mean reversion combo",
                skeleton=f"{base_slug}-price-reversion-combo",
                signal_idea="Price reversion combo",
                fields=[field_id],
                expression=f"{PRICE_REVERSAL_EXPR} + group_rank({field_term}, {anchor_group})",
                reason="Combines the account's strongest pure reversal component with a current-dataset anchor to avoid naked reversal-only reuse.",
            )

        def build_short_reversal_family(row: dict[str, Any]) -> dict[str, Any] | None:
            field_id = str(row.get("field_id", "")).strip()
            description = str(row.get("description", "")).strip()
            if not field_id:
                return None
            role = _infer_field_role(field_id, description)
            if role not in {"revision", "level"}:
                return None
            transformed_term = f"ts_delta({field_id}, 20)"
            if _normalizer_for_field(field_id, description) == "close":
                transformed_term = f"{transformed_term} / close"
            base_slug = _slug(field_id)
            return _build_family(
                index=len(families) + 1,
                dataset=selected_dataset,
                family=f"{field_id} revision short reversal",
                skeleton=f"{base_slug}-delta-short-reversal",
                signal_idea="Revision and short reversal",
                fields=[field_id],
                expression=f"group_rank(-ts_delta(close, 3) / {short_rev_div} + {transformed_term}, {anchor_group})",
                reason="Uses the archived short-horizon reversal motif, but swaps in a dataset-specific revision transform instead of recycling an existing submitted field.",
            )

        for row in top_fields:
            add_built_family(build_anchor_family(row))

        for row in top_fields[:2]:
            add_built_family(build_reversal_blend_family(row))

        for row in top_fields[:2]:
            add_built_family(build_short_reversal_family(row))

        for row in top_fields[:1]:
            add_built_family(build_price_reversion_family(row))

        if len(top_fields) >= 2 and len(families) < max_families:
            first = top_fields[0]
            second = top_fields[1]
            first_field = str(first.get("field_id", "")).strip()
            second_field = str(second.get("field_id", "")).strip()
            if first_field and second_field:
                first_term = _field_term(first_field, str(first.get("description", "")))
                second_term = _field_term(second_field, str(second.get("description", "")))
                pair_slug = f"{_slug(first_field)}-{_slug(second_field)}"
                add_family(
                    _build_family(
                        index=len(families) + 1,
                        dataset=selected_dataset,
                        family=f"{first_field} and {second_field} dual anchor",
                        skeleton=f"{pair_slug}-dual-anchor",
                        signal_idea="Dual anchor",
                        fields=[first_field, second_field],
                        expression=f"group_rank({first_term} + {second_term}, {anchor_group})",
                        reason="Multi-anchor signals already survive in the submitted library; this pairs the two best current fields before any gating or parameter sweep.",
                    )
                )
                add_family(
                    _build_family(
                        index=len(families) + 1,
                        dataset=selected_dataset,
                        family=f"{first_field} and {second_field} blended reversal",
                        skeleton=f"{pair_slug}-blended-reversal",
                        signal_idea="Composite blend",
                        fields=[first_field, second_field],
                        expression=f"group_rank(rank(-returns) / {rev_div} + ({first_term} + {second_term}) / {anchor_div}, {inside_group})",
                        reason="Extends the best-performing reversal-plus-anchor archetype by letting two dataset-native anchors share the same reversal chassis.",
                    )
                )

        return families[:max_families]

    def _validate_and_merge_families(
        self,
        *,
        candidate_families: list[dict[str, Any]],
        fallback_families: list[dict[str, Any]],
        selected_dataset: str,
        selected_fields: list[dict[str, Any]],
        known_fields: set[str],
        blocked_skeletons: set[str],
        blocked_chassis: set[str],
        submitted_expressions: set[str],
        max_families: int,
    ) -> list[dict[str, Any]]:
        selected_field_ids = {str(row.get("field_id", "")).strip() for row in selected_fields}
        merged: list[dict[str, Any]] = []
        seen_expressions: set[str] = set(submitted_expressions)
        seen_skeletons: set[str] = set()
        seen_high_risk_chassis: set[str] = set()
        archetype_counts: Counter[str] = Counter()
        field_counts: Counter[str] = Counter()

        for source_row in candidate_families + fallback_families:
            expr = _normalize_expression(str(source_row.get("expression", "")))
            if not expr or not _is_expression_valid(expr):
                continue
            if not _validate_expression_uses_known_fields(expr, known_fields):
                continue
            skeleton = str(source_row.get("skeleton", "")).strip() or _slug(expr[:80])
            if skeleton in blocked_skeletons or skeleton in seen_skeletons or expr in seen_expressions:
                continue
            fields = self._extract_used_fields(expr, selected_fields)
            if not fields or not set(fields).issubset(selected_field_ids):
                continue
            archetype = str(source_row.get("archetype", "")).strip() or _infer_archetype(
                expr,
                str(source_row.get("signal_idea", "Composite")),
                fields,
            )
            self_corr_risk = str(source_row.get("self_corr_risk", "")).strip() or _self_corr_risk_for_archetype(archetype)
            chassis = str(source_row.get("chassis", "")).strip() or _build_chassis(expr, fields)
            if chassis in blocked_chassis:
                continue
            if archetype_counts[archetype] >= _archetype_budget(max_families, archetype, self_corr_risk):
                continue
            if self_corr_risk == HIGH_SELF_CORR and chassis in seen_high_risk_chassis:
                continue
            if any(field_counts[field_id] >= 2 for field_id in fields):
                continue
            merged.append(
                {
                    "family_id": f"g{len(merged) + 1}",
                    "dataset": selected_dataset,
                    "family": str(source_row.get("family", skeleton)).strip() or skeleton,
                    "skeleton": skeleton,
                    "signal_idea": str(source_row.get("signal_idea", "Composite")).strip() or "Composite",
                    "fields": fields,
                    "expression": expr,
                    "reason": str(source_row.get("reason", "Generated from mined alpha principles.")).strip(),
                    "archetype": archetype,
                    "self_corr_risk": self_corr_risk,
                    "chassis": chassis,
                    "novelty_score": self._novelty_score(expr, fields, archetype, chassis, submitted_expressions, seen_high_risk_chassis),
                }
            )
            seen_expressions.add(expr)
            seen_skeletons.add(skeleton)
            archetype_counts[archetype] += 1
            for field_id in fields:
                field_counts[field_id] += 1
            if self_corr_risk == HIGH_SELF_CORR:
                seen_high_risk_chassis.add(chassis)
            if len(merged) >= max_families:
                break
        return merged

    def _novelty_score(
        self,
        expression: str,
        fields: list[str],
        archetype: str,
        chassis: str,
        submitted_expressions: set[str],
        seen_chassis: set[str],
    ) -> float:
        score = 1.0
        if _normalize_expression(expression) in submitted_expressions:
            score -= 1.0
        if chassis in seen_chassis:
            score -= 0.5
        if len(fields) >= 2:
            score += 0.25
        if archetype not in {"reversal_anchor", "eps_sales_reversal"}:
            score += 0.20
        if any(op in expression for op in ("ts_zscore", "ts_corr", "ts_decay_linear", "ts_mean")):
            score += 0.20
        return round(score, 3)

    def _call_llm(self, prompt: str) -> str:
        if self.provider is None:
            raise LLMProviderError(
                code="invalid_configuration",
                message="Candidate generation has no configured LLM provider.",
            )
        request = LLMRequest(
            system_prompt=(
                "You are a quantitative alpha research assistant. "
                "Improve and diversify seed FASTEXPR alpha families while preserving the proven principles. "
                "Output only a JSON array with no markdown or surrounding explanation."
            ),
            user_prompt=prompt,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            response_format="json",
            metadata={"consumer": "candidate_template_generation"},
        )
        return self.provider.complete(request).content

    def _build_prompt(
        self,
        *,
        workspace_root: Path,
        run_dir: Path,
        selected_dataset: str,
        selected_fields: list[dict[str, Any]],
        principles: dict[str, Any],
        seed_families: list[dict[str, Any]],
        blocked_skeletons: set[str],
        blocked_chassis: set[str],
        submitted_expressions: set[str],
    ) -> str:
        parts: list[str] = []
        parts.append("Generate 16-24 diverse alpha family drafts for WorldQuant BRAIN using FASTEXPR; local code will select the best 8-12.")
        parts.append("Do not invent unsupported fields. Start from verified principles, but deliberately cover multiple structural slots instead of cloning one winner chassis.")
        parts.append("")

        parts.append("## VERIFIED ARCHETYPES FROM SUBMITTED AND HIGH-PERFORMING ALPHAS")
        for item in principles.get("top_examples", [])[:6]:
            metrics = item.get("metrics") or {}
            parts.append(
                f"- {item.get('expression', '')} | S={_safe_float(metrics.get('sharpe')):.2f} F={_safe_float(metrics.get('fitness')):.2f} | source={item.get('source', '')}"
            )

        parts.append("")
        parts.append("## MINED PRINCIPLES")
        parts.append(f"- Preferred baseline anchor: group_rank(field-normalized, {principles.get('anchor_group', 'subindustry')})")
        parts.append(f"- Strongest blend chassis: group_rank(rank(-returns) / {principles.get('rank_returns_divisor', 10)} + anchor / {principles.get('anchor_divisor', 10)}, {principles.get('inside_blend_group', 'industry')})")
        parts.append(f"- Strongest short-horizon alternative: group_rank(-ts_delta(close, 3) / {principles.get('short_reversal_divisor', 10)} + anchor, {principles.get('anchor_group', 'subindustry')})")
        parts.append(f"- Pure reversal module already exists: {PRICE_REVERSAL_EXPR}")
        parts.append("- Novelty must come from swapping in current-dataset fields, changing composition, or combining anchors, not from cloning submitted expressions.")

        parts.append("")
        parts.append(f"## CURRENT FIELD POOL ({selected_dataset})")
        for row in selected_fields[:10]:
            field_id = str(row.get("field_id", "")).strip()
            description = str(row.get("description", "")).strip()
            role = _infer_field_role(field_id, description)
            term = _field_term(field_id, description)
            parts.append(f"- {field_id}: role={role}, preferred_term={term}, description={description}")

        parts.append("")
        parts.append("## BLOCKED AND ALREADY SUBMITTED")
        for skeleton in sorted(blocked_skeletons)[:20]:
            parts.append(f"- blocked skeleton: {skeleton}")
        for chassis in sorted(blocked_chassis)[:20]:
            parts.append(f"- blocked chassis: {chassis}")
        for expression in sorted(submitted_expressions)[:10]:
            parts.append(f"- exact submitted expression: {expression}")

        knowledge = _read_json(run_dir / "knowledge_base.json", {})
        if isinstance(knowledge, dict) and (knowledge.get("success_patterns") or knowledge.get("failure_pitfalls") or knowledge.get("field_insights")):
            parts.append("")
            parts.append("## LIGHTWEIGHT KNOWLEDGE BASE FEEDBACK")
            for item in (knowledge.get("success_patterns") or [])[-5:]:
                parts.append(f"- worked: {item.get('pattern', '')} | dataset={item.get('dataset', '')} | metrics={item.get('metrics', {})}")
            for item in (knowledge.get("failure_pitfalls") or [])[-8:]:
                parts.append(f"- avoid: {item.get('pattern', '')} | route={item.get('route_decision', '')} | checks={item.get('failed_checks', [])}")
            effective_fields = [item.get("field", "") for item in (knowledge.get("field_insights") or []) if str(item.get("pattern", "")).startswith("FIELD_EFFECTIVE:")]
            problematic_fields = [item.get("field", "") for item in (knowledge.get("field_insights") or []) if str(item.get("pattern", "")).startswith("FIELD_PROBLEMATIC:")]
            if effective_fields:
                parts.append(f"- effective fields to prefer when present: {effective_fields[-8:]}")
            if problematic_fields:
                parts.append(f"- problematic fields to avoid: {problematic_fields[-8:]}")

        field_pool = _read_json(run_dir / "field_pool.json", {})
        diversity = field_pool.get("diversity_suggestions", {}) if isinstance(field_pool, dict) else {}
        if diversity:
            parts.append("")
            parts.append("## DIVERSITY SUGGESTIONS FOR THIS RUN")
            parts.append(f"- Underused fields to cover: {diversity.get('underused_fields', [])}")
            parts.append(f"- Overused fields to avoid over-repeating: {diversity.get('overused_fields', [])}")
            parts.append(f"- Required structural slots: {diversity.get('required_slots', [])}")
        if isinstance(field_pool, dict) and field_pool.get("structural_jump_required"):
            parts.append("")
            parts.append("## FORCED STRUCTURAL JUMP")
            parts.append("- Recent rounds exhausted the prior dataset/chassis neighborhood; at least half of drafts must use a different operator chain from ts_rank/mean-reversion/reversal-anchor repeats.")
            parts.append("- Prefer orthogonal constructions: rank(ts_corr(...)), ts_zscore spreads, conditional gates, group_neutralize, or dual-field relative changes where units are normalized before addition.")
            parts.append("- Do not recycle the same field plus only a window, decay, divisor, or group change.")

        state = _read_json(run_dir / "iteration_state.json", {})
        completed = state.get("completed_stages", [])
        scan_entries = [entry for entry in completed if entry.get("stage") in {"scan", "scan-retry"}][-3:]
        if scan_entries:
            parts.append("")
            parts.append("## RECENT FAILURES AND NEAR PASSES")
            high_self_corr_samples = []
            invalid_samples = []
            for entry in scan_entries:
                scan_output = entry.get("scan_output")
                if not scan_output:
                    continue
                rows = _read_json(workspace_root / str(scan_output), [])
                fail_samples = []
                near_samples = []
                for row in rows:
                    metrics = row.get("metrics") or {}
                    sharpe = _safe_float(metrics.get("sharpe"))
                    fitness = _safe_float(metrics.get("fitness"))
                    turnover = _safe_float(metrics.get("turnover"))
                    expression = str(row.get("expression", ""))
                    checks = row.get("checks") or []
                    check_names = {str(check.get("name", "")) for check in checks if isinstance(check, dict)}
                    if "SELF_CORRELATION" in check_names and expression:
                        high_self_corr_samples.append(f"{expression} | S={sharpe:.2f} F={fitness:.2f}")
                    if (row.get("error") or not row.get("alpha_id")) and expression:
                        invalid_samples.append(f"{expression} | error={row.get('error', '')}")
                    if sharpe >= 1.15 and fitness >= 0.90 and turnover <= PASS_TURNOVER:
                        near_samples.append(expression)
                    elif expression:
                        fail_samples.append(expression)
                if near_samples:
                    parts.append(f"- iteration {entry.get('iteration')}: near-pass examples -> {near_samples[:2]}")
                if fail_samples:
                    parts.append(f"- iteration {entry.get('iteration')}: failed examples -> {fail_samples[:2]}")
            if high_self_corr_samples:
                parts.append("")
                parts.append("## HIGH SELF-CORRELATION CHASSIS TO ESCAPE")
                for sample in high_self_corr_samples[:6]:
                    parts.append(f"- {sample}")
                parts.append("- Do not only change decay for these; change structure via weaker reversal legs, grouping shifts, ts_delta/ts_rank anchors, or orthogonal conditional gates.")
            if invalid_samples:
                parts.append("")
                parts.append("## INVALID FIELD/DATASET LESSONS")
                for sample in invalid_samples[:6]:
                    parts.append(f"- {sample}")
                parts.append("- Avoid structural identifiers, universe membership, currency/code fields, vector fields, and divide-by metadata patterns such as top1000/cap or *_currency_code/cap.")

        parts.append("")
        parts.append("## SEED FAMILIES TO IMPROVE OR MUTATE")
        for item in seed_families:
            parts.append(
                f"- {item['skeleton']}: {item['expression']} | fields={','.join(item['fields'])} | rationale={item['reason']}"
            )

        parts.append("")
        parts.append("## OUTPUT RULES")
        parts.append("- Output only a JSON array of objects with keys: expression, skeleton, family, signal_idea, reason, archetype")
        parts.append("- Use only fields from the current field pool")
        parts.append("- Do not return an exact submitted expression, blocked skeleton, or blocked chassis")
        parts.append("- Allocate drafts across slots: 20% normalized-anchor, 20% mild-reversal-blend, 20% temporal/delta variants, 15% spread/ratio, 15% conditional/gated, 10% correlation/orthogonal variants")
        parts.append("- If recent strong candidates failed SELF_CORRELATION, include at least two self-corr escape drafts with a materially changed chassis")
        parts.append("- Avoid UNITS warnings: when adding heterogeneous terms, rank/zscore each component or convert both to dimensionless ratios before addition")
        parts.append("- Treat SELF_CORRELATION pending as not submission-ready; prefer structural novelty over decay/window-only variants")
        parts.append("- At least half of the outputs must have a materially different operator chassis from the seed expressions")
        parts.append("- Favor industry over sector when blending reversal and anchor inside group_rank unless field semantics strongly argue otherwise")
        parts.append("- Keep expressions concise and simulation-ready")
        parts.append("")
        parts.append("## FASTEXPR REFERENCE")
        parts.append(FASTEXPR_OPERATORS_CORE)
        return "\n".join(parts)

    def _parse_llm_json(self, content: str) -> list[dict[str, Any]]:
        content = content.strip()
        fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", content, re.DOTALL)
        if fence_match:
            content = fence_match.group(1).strip()
        elif content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "families" in data:
                return data["families"]
            if isinstance(data, dict) and "alphas" in data:
                return data["alphas"]
            return [data] if isinstance(data, dict) else []
        except json.JSONDecodeError:
            for pattern in (r"\[.*\]", r"\{.*\}"):
                match = re.search(pattern, content, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(0))
                        if isinstance(data, list):
                            return data
                        if isinstance(data, dict):
                            return data.get("families", data.get("alphas", [data]))
                    except json.JSONDecodeError:
                        pass
            print(f"[LLM] Failed to parse JSON from response: {content[:500]}")
            return []

    def _extract_used_fields(self, expression: str, selected_fields: list[dict[str, Any]]) -> list[str]:
        field_ids = {str(row.get("field_id", "")).strip() for row in selected_fields}
        tokens = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", expression))
        return sorted(tokens & field_ids)
