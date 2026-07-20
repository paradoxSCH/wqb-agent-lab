from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OutputRegistryEntry:
    artifact: str
    stage: str
    producer: str
    consumers: tuple[str, ...]
    validators: tuple[str, ...]
    diagnosis_types: tuple[str, ...]
    policy_evaluator: str
    success_metrics: tuple[str, ...]
    failure_metrics: tuple[str, ...]
    can_affect_budget: bool = False
    can_affect_memory: bool = False


@dataclass(frozen=True)
class OutputDiagnosis:
    diagnosis_type: str
    severity: str
    evidence: dict[str, Any] = field(default_factory=dict)
    recommended_action: str = "quarantine_unknown_diagnosis"
    policy: str = "quarantine_unknown_diagnosis"
    success_metric: str = "classification_resolution_rate"
    failure_metric: str = "repeat_failure_rate"


@dataclass(frozen=True)
class OutputEvaluationRecord:
    artifact: str
    stage: str
    validation_status: str
    diagnoses: tuple[OutputDiagnosis, ...]
    metrics: dict[str, Any] = field(default_factory=dict)
