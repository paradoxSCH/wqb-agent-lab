"""Run the asynchronous memory governance worker."""

from scripts.memory_worker import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
