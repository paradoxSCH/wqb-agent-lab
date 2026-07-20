"""Canonical production workflow boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .stages import (
    StageCheckpointStore,
    StageError,
    StageInterruptionRequiresReconciliation,
    StageOutcome,
    StageResult,
    StageRunner,
)

if TYPE_CHECKING:
    from src.kimi_daily_workflow import KimiDailyWorkflow as ResearchWorkflow


def __getattr__(name: str) -> Any:
    if name != "ResearchWorkflow":
        raise AttributeError(name)
    from src.kimi_daily_workflow import KimiDailyWorkflow

    globals()[name] = KimiDailyWorkflow
    return KimiDailyWorkflow


__all__ = [
    "ResearchWorkflow",
    "StageCheckpointStore",
    "StageError",
    "StageInterruptionRequiresReconciliation",
    "StageOutcome",
    "StageResult",
    "StageRunner",
]
