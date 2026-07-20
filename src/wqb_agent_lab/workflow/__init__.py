"""Compatibility import for the canonical installed namespace."""

from wqb_agent_lab.workflow import (
    ResearchWorkflow,
    StageCheckpointStore,
    StageError,
    StageInterruptionRequiresReconciliation,
    StageOutcome,
    StageResult,
    StageRunner,
)

__all__ = [
    "ResearchWorkflow",
    "StageCheckpointStore",
    "StageError",
    "StageInterruptionRequiresReconciliation",
    "StageOutcome",
    "StageResult",
    "StageRunner",
]
