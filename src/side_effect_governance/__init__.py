"""Runtime permission checks for autonomous WQB side effects."""

from .capabilities import (
    CapabilityDecision,
    SideEffectCapabilityDisabled,
    evaluate_side_effect_capability,
    require_side_effect_capability,
)

__all__ = [
    "CapabilityDecision",
    "SideEffectCapabilityDisabled",
    "evaluate_side_effect_capability",
    "require_side_effect_capability",
]
