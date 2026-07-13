from __future__ import annotations

import importlib

import pytest


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
