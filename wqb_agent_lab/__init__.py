"""Stable installed namespace for WQB Agent Lab."""

from importlib import import_module
from types import ModuleType

from .layout import RepositoryLayout


_SUBMODULES = frozenset(
    {"evaluation", "governance", "memory", "planning", "platform", "research", "runtime", "workflow"}
)


def __getattr__(name: str) -> ModuleType:
    if name not in _SUBMODULES:
        raise AttributeError(name)
    module = import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module


__all__ = ["RepositoryLayout"]
