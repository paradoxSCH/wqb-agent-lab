"""Run one production daily research workflow."""

from wqb_agent_lab.workflow.engine import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
