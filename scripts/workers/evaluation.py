"""Run the asynchronous evaluation worker."""

from scripts.evaluation_worker import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
