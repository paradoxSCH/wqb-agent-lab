"""Run a config-driven WQB simulation scan."""

from __future__ import annotations

import argparse
import asyncio

from run_scan import run_scan


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a config-driven BRAIN scan")
    parser.add_argument("--config", required=True, help="Path to scan config JSON")
    parser.add_argument("--continue-on-pass", action="store_true", help="Continue scanning after finding a PASS")
    parser.add_argument("--max-concurrency", type=int, default=None, help="Maximum concurrent simulations, capped at 3")
    args = parser.parse_args()
    asyncio.run(run_scan(args.config, cli_continue_on_pass=args.continue_on_pass, max_concurrency=args.max_concurrency))


if __name__ == "__main__":
    main()
