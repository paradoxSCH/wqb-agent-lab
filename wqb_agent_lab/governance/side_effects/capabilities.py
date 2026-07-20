from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Literal, Mapping


SideEffectOperation = Literal["simulation", "submission"]

_CAPABILITY_ENVIRONMENT_VARIABLES: dict[str, str] = {
    "simulation": "WQB_LIVE_SIMULATION_CAPABILITY",
    "submission": "WQB_LIVE_SUBMIT_CAPABILITY",
}


@dataclass(frozen=True)
class CapabilityDecision:
    operation: SideEffectOperation
    environment_variable: str
    enabled: bool
    status: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SideEffectCapabilityDisabled(RuntimeError):
    def __init__(self, decision: CapabilityDecision) -> None:
        self.decision = decision
        super().__init__(
            f"{decision.operation} side effect is disabled; "
            f"set {decision.environment_variable}=1 to enable this runtime capability"
        )


def evaluate_side_effect_capability(
    operation: SideEffectOperation,
    *,
    env: Mapping[str, str] | None = None,
) -> CapabilityDecision:
    try:
        environment_variable = _CAPABILITY_ENVIRONMENT_VARIABLES[operation]
    except KeyError as exc:
        raise ValueError(f"unsupported side-effect operation: {operation}") from exc
    source = os.environ if env is None else env
    enabled = str(source.get(environment_variable, "")).strip() == "1"
    return CapabilityDecision(
        operation=operation,
        environment_variable=environment_variable,
        enabled=enabled,
        status="capability_enabled" if enabled else "capability_disabled",
        reason=(
            "runtime capability explicitly enabled"
            if enabled
            else f"runtime capability requires {environment_variable}=1"
        ),
    )


def require_side_effect_capability(
    operation: SideEffectOperation,
    *,
    env: Mapping[str, str] | None = None,
) -> CapabilityDecision:
    decision = evaluate_side_effect_capability(operation, env=env)
    if not decision.enabled:
        raise SideEffectCapabilityDisabled(decision)
    return decision
