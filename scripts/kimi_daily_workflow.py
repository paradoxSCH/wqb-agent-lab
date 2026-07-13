"""Deprecated compatibility launcher for the provider-neutral research workflow."""

from __future__ import annotations

import sys

from scripts.run.workflow import main as workflow_main


REMOVAL_VERSION = "0.3.0"


def main() -> int:
    print(
        "scripts.kimi_daily_workflow is deprecated; use scripts.run.workflow "
        f"(compatibility launcher will be removed in {REMOVAL_VERSION}).",
        file=sys.stderr,
    )
    return workflow_main()


if __name__ == "__main__":
    raise SystemExit(main())
