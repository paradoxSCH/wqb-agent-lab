"""Compatibility imports for the canonical WQB platform package."""

from src.wqb_agent_lab.platform import (
    WQBAlphaDetail,
    WQBCheck,
    WQBClient,
    WQBSubmitResult,
    WQBSimulationCreated,
    WQBSimulationRequest,
    is_submitted_status,
)

__all__ = [
    "WQBAlphaDetail",
    "WQBCheck",
    "WQBClient",
    "WQBSubmitResult",
    "WQBSimulationCreated",
    "WQBSimulationRequest",
    "is_submitted_status",
]
