from __future__ import annotations

import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module_name",
    [
        "scripts.run.scan",
        "scripts.run.workflow",
        "scripts.run.daemon",
        "scripts.workers.submission",
        "scripts.workers.evaluation",
        "scripts.workers.memory",
        "scripts.workers.registry",
    ],
)
def test_grouped_command_exposes_main(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert callable(module.main)


@pytest.mark.parametrize(
    "relative_path",
    [
        "scripts/evaluation_worker.py",
        "scripts/memory_worker.py",
        "scripts/registry_worker.py",
    ],
)
def test_worker_implementations_are_not_duplicated_at_scripts_root(relative_path: str) -> None:
    assert not (ROOT / relative_path).is_file()
