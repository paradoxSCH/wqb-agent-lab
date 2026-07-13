from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


WORKFLOW_NAME = "continuous-alpha"


@dataclass(frozen=True, slots=True)
class ProjectLayout:
    workspace_root: Path
    run_tag: str
    legacy: bool = False

    @property
    def run_dir(self) -> Path:
        if self.legacy:
            return self.workspace_root / ".local" / "data" / "workflow" / WORKFLOW_NAME / self.run_tag
        return self.workspace_root / ".local" / "data" / "runs" / WORKFLOW_NAME / self.run_tag

    @property
    def scan_config_dir(self) -> Path:
        if self.legacy:
            return self.workspace_root / "scan_configs" / "workflow" / WORKFLOW_NAME / self.run_tag
        return self.workspace_root / ".local" / "research" / "scans" / WORKFLOW_NAME / self.run_tag

    @property
    def state_path(self) -> Path:
        return self.run_dir / "iteration_state.json"

    @classmethod
    def from_state_path(cls, workspace_root: Path, state_path: Path, run_tag: str | None = None) -> "ProjectLayout":
        resolved_state = state_path.resolve()
        resolved_root = workspace_root.resolve()
        tag = run_tag or resolved_state.parent.name
        legacy_root = (resolved_root / ".local" / "data" / "workflow" / WORKFLOW_NAME).resolve()
        try:
            legacy = resolved_state.is_relative_to(legacy_root)
        except ValueError:
            legacy = False
        return cls(resolved_root, tag, legacy=legacy)


def resolve_state_path(workspace_root: Path, run_tag: str | None, state_path: str | None) -> Path:
    root = workspace_root.resolve()
    if state_path:
        path = Path(state_path)
        return (root / path).resolve() if not path.is_absolute() else path
    if not run_tag:
        raise ValueError("Either run_tag or state_path must be provided.")

    new_state = root / ".local" / "data" / "runs" / WORKFLOW_NAME / run_tag / "iteration_state.json"
    legacy_state = root / ".local" / "data" / "workflow" / WORKFLOW_NAME / run_tag / "iteration_state.json"
    if new_state.exists() or not legacy_state.exists():
        return new_state
    return legacy_state