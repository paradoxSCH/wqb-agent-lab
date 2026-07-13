from __future__ import annotations

import argparse

from scripts.submit.submit_alpha_v2 import main as submit_one


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit one checked alpha by id")
    parser.add_argument("alpha_id")
    args = parser.parse_args()
    return submit_one(args.alpha_id)


if __name__ == "__main__":
    raise SystemExit(main())