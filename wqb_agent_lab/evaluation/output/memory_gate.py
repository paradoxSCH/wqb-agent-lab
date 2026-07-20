from __future__ import annotations

from typing import Any, Mapping


def resolve_memory_promotion_permission(memory_event: Mapping[str, Any]) -> dict[str, Any]:
    level = str(memory_event.get("evidence_level") or "").upper()
    target = str(memory_event.get("target") or "")

    if level in {"L0", "L1"}:
        return {
            "evidence_level": level or "unknown",
            "target": target,
            "can_use_in_prompt": level == "L1" and target != "long_term",
            "can_affect_budget": False,
            "can_promote_to_long_term": False,
            "can_be_default_prior": False,
            "reason": "weak_evidence_requires_audit_before_planning_or_long_term_memory",
        }
    if level == "L2":
        return {
            "evidence_level": level,
            "target": target,
            "can_use_in_prompt": True,
            "can_affect_budget": False,
            "can_promote_to_long_term": False,
            "can_be_default_prior": False,
            "reason": "usable_as_prompt_context_but_not_budget_or_default_prior",
        }
    if level == "L3":
        return {
            "evidence_level": level,
            "target": target,
            "can_use_in_prompt": True,
            "can_affect_budget": True,
            "can_promote_to_long_term": target == "long_term",
            "can_be_default_prior": False,
            "reason": "validated_controlled_budget_signal",
        }
    if level == "L4":
        return {
            "evidence_level": level,
            "target": target,
            "can_use_in_prompt": True,
            "can_affect_budget": True,
            "can_promote_to_long_term": target == "long_term",
            "can_be_default_prior": True,
            "reason": "validated_reusable_prior",
        }
    return {
        "evidence_level": level or "unknown",
        "target": target,
        "can_use_in_prompt": False,
        "can_affect_budget": False,
        "can_promote_to_long_term": False,
        "can_be_default_prior": False,
        "reason": "unknown_evidence_level",
    }
