"""Run the submitted-alpha registry synchronization worker."""

from scripts.registry_worker import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
