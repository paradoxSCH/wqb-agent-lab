from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory


def test_repository_layout_keeps_mutable_assets_under_local() -> None:
    from src.wqb_agent_lab.layout import RepositoryLayout

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve()
        layout = RepositoryLayout.from_root(root)

        assert layout.local_root == root / ".local"
        assert layout.data == root / ".local" / "data"
        assert layout.research == root / ".local" / "research"
        assert layout.logs == root / ".local" / "logs"
        assert layout.pids == root / ".local" / "pids"
        assert layout.build == root / ".local" / "build"


def test_repository_layout_ensure_is_idempotent() -> None:
    from src.wqb_agent_lab.layout import RepositoryLayout

    with TemporaryDirectory() as temp_dir:
        layout = RepositoryLayout.from_root(Path(temp_dir))

        assert layout.ensure() is layout
        assert layout.ensure() is layout
        assert all(path.is_dir() for path in layout.mutable_directories())
