from __future__ import annotations

import argparse
import json

from wqb_agent_lab.platform import WQBClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only WQB API contract checks.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = WQBClient.from_config().contract_probe()
    print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))
    return 0 if report["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
