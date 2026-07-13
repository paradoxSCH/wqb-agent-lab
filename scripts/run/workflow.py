"""Run one production daily research workflow."""

from src.kimi_daily_workflow import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
