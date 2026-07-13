from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from .memory_gate import resolve_memory_promotion_permission
from .types import OutputDiagnosis, OutputEvaluationRecord
from src.wqb_agent_lab.platform import load_operator_names


PRICE_VOLUME_FIELDS = {"close", "returns", "volume", "vwap", "adv20"}
TIME_SERIES_OPERATORS = {"ts_delta", "ts_mean", "ts_zscore", "ts_rank", "ts_std_dev"}
UNSUPPORTED_RAW_FIELD_TYPES = {"event", "vector"}
RAW_FIELD_ARITHMETIC_OPERATORS = {"/", "*", "+", "-"}
KNOWN_OPERATORS = set(load_operator_names())
KNOWN_GROUPS = {"industry", "sector", "subindustry", "market", "country"}
KNOWN_CONSTANTS = {"nan", "true", "false"}
TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def validate_candidate_hypothesis_queue(artifact: str, payload: Mapping[str, Any]) -> OutputEvaluationRecord:
    hypotheses = payload.get("hypotheses") or []
    diagnoses: list[OutputDiagnosis] = []
    invalid_hypotheses: set[str] = set()

    for index, hypothesis in enumerate(_as_mapping_items(hypotheses)):
        hypothesis_id = str(hypothesis.get("hypothesis_id") or f"hypothesis_{index}")
        primary_proxy = str(hypothesis.get("primary_proxy") or "")
        kill_conditions = hypothesis.get("kill_conditions") or []
        preflight_requirements = hypothesis.get("preflight_requirements") or []
        if primary_proxy in PRICE_VOLUME_FIELDS:
            invalid_hypotheses.add(hypothesis_id)
            diagnoses.append(
                _diag(
                    "pure_price_volume_primary_proxy",
                    "high",
                    {
                        "hypothesis_id": hypothesis_id,
                        "primary_proxy": primary_proxy,
                        "preflight_requirements": preflight_requirements,
                    },
                    "replace_or_add_non_price_volume_behavior_proxy",
                    "block_or_downweight_price_volume_only_candidate",
                )
            )
        if not kill_conditions:
            invalid_hypotheses.add(hypothesis_id)
            diagnoses.append(
                _diag(
                    "missing_kill_condition",
                    "medium",
                    {"hypothesis_id": hypothesis_id},
                    "add_ex_ante_kill_conditions_before_generation",
                    "require_kill_condition",
                )
            )

    return OutputEvaluationRecord(
        artifact=artifact,
        stage="candidate_generation",
        validation_status="block" if diagnoses else "pass",
        diagnoses=tuple(diagnoses),
        metrics={
            "row_count": len(hypotheses) if isinstance(hypotheses, Sequence) else 0,
            "invalid_count": len(invalid_hypotheses),
            "budget_saved_estimate": len(invalid_hypotheses),
        },
    )


def validate_expression_candidates(
    artifact: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    field_types: Mapping[str, str],
) -> OutputEvaluationRecord:
    diagnoses: list[OutputDiagnosis] = []
    invalid_rows: set[int] = set()
    known_fields = set(field_types)

    for index, candidate in enumerate(candidates):
        expression = str(candidate.get("expression") or "")
        row_diag_types: set[str] = set()
        for operator in _extract_unknown_operator_tokens(expression):
            row_diag_types.add("unknown_operator")
            diagnoses.append(
                _diag(
                    "unknown_operator",
                    "high",
                    {"row_index": index, "operator": operator, "expression": expression},
                    "replace_operator_with_current_catalog_name",
                    "static_preflight_block",
                    success_metric="prevented_invalid_simulation_count",
                    failure_metric="unknown_operator_recurrence",
                )
            )
        for token in _extract_field_tokens(expression):
            if token not in known_fields:
                row_diag_types.add("missing_field_reference")
                diagnoses.append(
                    _diag(
                        "missing_field_reference",
                        "high",
                        {"row_index": index, "field": token, "expression": expression},
                        "remove_or_map_missing_field_before_simulation",
                        "static_preflight_block",
                    )
                )
        for operator, field in _unsupported_field_operator_usages(expression, field_types):
            row_diag_types.add("field_type_operator_mismatch")
            diagnoses.append(
                _diag(
                    "field_type_operator_mismatch",
                    "high",
                    {"row_index": index, "operator": operator, "field": field, "expression": expression},
                    "replace_event_operator_or_use_event_safe_proxy",
                    "static_preflight_block",
                    success_metric="prevented_invalid_simulation_count",
                    failure_metric="event_operator_error_recurrence",
                )
            )
        if row_diag_types:
            invalid_rows.add(index)

    return OutputEvaluationRecord(
        artifact=artifact,
        stage="scan_config_expression",
        validation_status="block" if diagnoses else "pass",
        diagnoses=tuple(diagnoses),
        metrics={
            "row_count": len(candidates),
            "invalid_count": len(invalid_rows),
            "budget_saved_estimate": len(invalid_rows),
        },
    )


def validate_memory_sync_report(artifact: str, payload: Mapping[str, Any]) -> OutputEvaluationRecord:
    diagnoses: list[OutputDiagnosis] = []
    nodes_written = int(payload.get("nodes_written") or 0)
    events_recorded = int(payload.get("events_recorded") or 0)
    if nodes_written > 0 and events_recorded == 0:
        diagnoses.append(
            _diag(
                "missing_memory_event_trace",
                "medium",
                {"nodes_written": nodes_written, "events_recorded": events_recorded},
                "write_event_trace_for_each_memory_mutation",
                "require_memory_event_trace",
                success_metric="trace_coverage_rate",
                failure_metric="missing_trace_count",
            )
        )

    for promotion in _as_mapping_items(payload.get("promotions") or []):
        permission = resolve_memory_promotion_permission(promotion)
        if promotion.get("target") == "long_term" and not permission["can_promote_to_long_term"]:
            diagnoses.append(
                _diag(
                    "unsupported_memory_promotion",
                    "high",
                    {"promotion": dict(promotion), "permission": permission},
                    "downgrade_to_audit_or_prompt_context_until_evidence_level_improves",
                    "memory_promotion_gate",
                    success_metric="unsupported_promotion_block_rate",
                    failure_metric="polluted_memory_count",
                )
            )

    return OutputEvaluationRecord(
        artifact=artifact,
        stage="memory",
        validation_status="block" if any(diag.severity == "high" for diag in diagnoses) else ("warn" if diagnoses else "pass"),
        diagnoses=tuple(diagnoses),
        metrics={"nodes_written": nodes_written, "events_recorded": events_recorded},
    )


def validate_report_text(artifact: str, text: str) -> OutputEvaluationRecord:
    diagnoses: list[OutputDiagnosis] = []
    if "Syntax error in text" in text or "mermaid version" in text:
        diagnoses.append(
            _diag(
                "render_syntax_error",
                "high",
                {"artifact": artifact},
                "fix_render_source_and_verify_browser_render",
                "render_block_until_valid",
                success_metric="render_pass_rate",
                failure_metric="syntax_error_count",
            )
        )
    if "\ufffd" in text:
        diagnoses.append(
            _diag(
                "encoding_corruption",
                "high",
                {"artifact": artifact},
                "rewrite_artifact_as_utf8",
                "encoding_block_until_clean",
                success_metric="utf8_clean_rate",
                failure_metric="encoding_corruption_count",
            )
        )
    lowered = text.lower()
    if "submit-ready" in lowered and "submitted successfully" in lowered:
        diagnoses.append(
            _diag(
                "submit_status_conflation",
                "high",
                {"artifact": artifact},
                "separate_submit_ready_from_live_submitted_status",
                "status_semantics_block",
                success_metric="status_semantics_pass_rate",
                failure_metric="status_conflation_count",
            )
        )

    return OutputEvaluationRecord(
        artifact=artifact,
        stage="report_ui",
        validation_status="block" if diagnoses else "pass",
        diagnoses=tuple(diagnoses),
        metrics={"text_length": len(text)},
    )


def _extract_field_tokens(expression: str) -> set[str]:
    tokens = set(TOKEN_RE.findall(expression))
    call_names = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", expression))
    return {
        token
        for token in tokens
        if token not in call_names
        and token not in KNOWN_OPERATORS
        and token not in KNOWN_GROUPS
        and token not in KNOWN_CONSTANTS
    }


def _extract_unknown_operator_tokens(expression: str) -> set[str]:
    call_names = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", expression))
    return call_names - KNOWN_OPERATORS


def _unsupported_field_operator_usages(expression: str, field_types: Mapping[str, str]) -> Iterable[tuple[str, str]]:
    yielded: set[tuple[str, str]] = set()
    for operator in TIME_SERIES_OPERATORS:
        for match in re.finditer(rf"\b{re.escape(operator)}\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)", expression):
            field = match.group(1)
            if _is_unsupported_raw_field(field, field_types):
                key = (operator, field)
                yielded.add(key)
                yield key
    for field in _extract_field_tokens(expression):
        if field not in field_types:
            continue
        if not _is_unsupported_raw_field(field, field_types):
            continue
        if re.search(rf"\b{re.escape(field)}\s*[/+*\-]", expression) or re.search(rf"[/+*\-]\s*{re.escape(field)}\b", expression):
            key = ("raw_arithmetic", field)
            if key not in yielded:
                yield key


def _is_unsupported_raw_field(field: str, field_types: Mapping[str, str]) -> bool:
    return str(field_types.get(field) or "").lower() in UNSUPPORTED_RAW_FIELD_TYPES


def _as_mapping_items(items: Any) -> Iterable[Mapping[str, Any]]:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return []
    return [item for item in items if isinstance(item, Mapping)]


def _diag(
    diagnosis_type: str,
    severity: str,
    evidence: Mapping[str, Any],
    recommended_action: str,
    policy: str,
    *,
    success_metric: str = "classification_resolution_rate",
    failure_metric: str = "repeat_failure_rate",
) -> OutputDiagnosis:
    return OutputDiagnosis(
        diagnosis_type=diagnosis_type,
        severity=severity,
        evidence=dict(evidence),
        recommended_action=recommended_action,
        policy=policy,
        success_metric=success_metric,
        failure_metric=failure_metric,
    )
