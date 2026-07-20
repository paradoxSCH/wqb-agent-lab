"""Compatibility module for :mod:`wqb_agent_lab.runtime.scan`.

This root-level module remains available through the documented compatibility
window. New code must import the canonical runtime module.
"""

from __future__ import annotations

import sys

from wqb_agent_lab.runtime import scan as _scan


if __name__ == "__main__":
    raise SystemExit(_scan.main())

sys.modules[__name__] = _scan
