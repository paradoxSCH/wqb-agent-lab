"""Public package for the WQB agent research product."""

from importlib import import_module
from types import ModuleType

from .layout import RepositoryLayout


_SUBMODULES = frozenset({"evaluation", "governance", "memory", "platform", "research", "workflow"})


def __getattr__(name: str) -> ModuleType:
    if name not in _SUBMODULES:
        raise AttributeError(name)
    module = import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module

__all__ = ["RepositoryLayout"]
