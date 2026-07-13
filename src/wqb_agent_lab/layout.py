"""Canonical repository paths for private and mutable local state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RepositoryLayout:
    root: Path
    local_root: Path
    data: Path
    research: Path
    logs: Path
    pids: Path
    build: Path

    @classmethod
    def from_root(cls, root: Path) -> "RepositoryLayout":
        resolved_root = Path(root).resolve()
        local_root = resolved_root / ".local"
        return cls(
            root=resolved_root,
            local_root=local_root,
            data=local_root / "data",
            research=local_root / "research",
            logs=local_root / "logs",
            pids=local_root / "pids",
            build=local_root / "build",
        )

    def mutable_directories(self) -> tuple[Path, ...]:
        return (self.data, self.research, self.logs, self.pids, self.build)

    def ensure(self) -> "RepositoryLayout":
        for directory in self.mutable_directories():
            directory.mkdir(parents=True, exist_ok=True)
        return self


__all__ = ["RepositoryLayout"]
