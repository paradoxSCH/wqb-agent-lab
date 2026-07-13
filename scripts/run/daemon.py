"""Run the supervised production workflow daemon."""

from scripts.launch_daemon import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
