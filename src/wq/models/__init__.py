"""Typed records shared by scan, triage, optimize, and submit workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Candidate:
    expression: str
    settings: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CheckResult:
    name: str
    result: str
    limit: Any = None
    value: Any = None


@dataclass(slots=True)
class SimulationResult:
    expression: str
    settings: dict[str, Any]
    success: bool
    alpha_id: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AlphaRecord:
    alpha_id: str
    expression: str
    settings: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""
    status: str = "unknown"


__all__ = ["AlphaRecord", "Candidate", "CheckResult", "SimulationResult"]