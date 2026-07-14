"""Compatibility module for :mod:`wqb_agent_lab.platform.check_readiness`."""

from wqb_agent_lab.platform.check_readiness import (
    FAILED_RESULTS,
    REQUIRED_SUBMISSION_CHECK_NAMES,
    WAITING_RESULTS,
    CheckReadiness,
    evaluate_check_snapshot,
    normalize_checks,
)

__all__ = [
    "FAILED_RESULTS",
    "REQUIRED_SUBMISSION_CHECK_NAMES",
    "WAITING_RESULTS",
    "CheckReadiness",
    "evaluate_check_snapshot",
    "normalize_checks",
]
