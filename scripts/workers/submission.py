"""Run the independent submission queue worker."""

from scripts.submit.submission_worker import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
