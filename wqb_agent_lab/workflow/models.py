from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class StagePlan:
    stage: str
    budget: int
    remaining_stage_budget: int
    remaining_daily_budget: int
    source_config: Path | None = None
    sliced_config: Path | None = None
    output_path: Path | None = None
    candidate_count: int = 0
    action: str = "none"
    policy_feedback_governance: dict[str, Any] | None = None


