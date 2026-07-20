from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from wqb_agent_lab.contracts import ValidationError

from .models import PlanProposal, PlanProposalValidationError, parse_plan_proposal


ProposalGenerator = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class RepairAttempt:
    attempt: int
    structural_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GeneratedPlanProposal:
    proposal: PlanProposal
    failed_attempts: tuple[RepairAttempt, ...]

    @property
    def repair_count(self) -> int:
        return len(self.failed_attempts)


class PlanProposalRepairExhausted(PlanProposalValidationError):
    def __init__(self, errors: list[ValidationError], attempts: tuple[RepairAttempt, ...]) -> None:
        self.attempts = attempts
        super().__init__(errors)


def generate_plan_proposal(
    prompt: str,
    generate: ProposalGenerator,
    *,
    max_repairs: int = 2,
) -> PlanProposal:
    return generate_plan_proposal_result(prompt, generate, max_repairs=max_repairs).proposal


def generate_plan_proposal_result(
    prompt: str,
    generate: ProposalGenerator,
    *,
    max_repairs: int = 2,
) -> GeneratedPlanProposal:
    """Generate and structurally repair a proposal without changing research policy.

    The generator remains provider-neutral. A repair attempt receives the original prompt,
    the invalid output, and precise structural errors. Simulation and submission
    capabilities are deliberately outside this function.
    """

    if isinstance(max_repairs, bool) or not isinstance(max_repairs, int):
        raise TypeError("max_repairs must be an integer")
    if max_repairs < 0 or max_repairs > 5:
        raise ValueError("max_repairs must be between 0 and 5")

    next_prompt = prompt
    attempts: list[RepairAttempt] = []
    for attempt_index in range(max_repairs + 1):
        payload = generate(next_prompt)
        try:
            return GeneratedPlanProposal(parse_plan_proposal(payload), tuple(attempts))
        except PlanProposalValidationError as exc:
            attempts.append(
                RepairAttempt(
                    attempt=attempt_index + 1,
                    structural_errors=tuple(str(error) for error in exc.errors),
                )
            )
            if attempt_index >= max_repairs:
                raise PlanProposalRepairExhausted(list(exc.errors), tuple(attempts)) from exc
            next_prompt = _repair_prompt(prompt, payload, exc)

    raise AssertionError("repair loop did not return or raise")


def _repair_prompt(original_prompt: str, payload: Any, error: PlanProposalValidationError) -> str:
    return "\n\n".join(
        (
            original_prompt,
            "The previous response contained useful research content but did not match the structural envelope.",
            error.repair_feedback(),
            "Previous response:\n" + _render_payload(payload),
        )
    )


def _render_payload(payload: Any) -> str:
    if isinstance(payload, Mapping):
        try:
            return json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            pass
    return str(payload)
