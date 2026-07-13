from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.behavioral_proxy.map import build_behavioral_proxy_map


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_fields(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, {})
    if isinstance(payload, dict):
        fields = payload.get("fields") or []
    else:
        fields = payload
    return [field for field in fields if isinstance(field, dict)]


def load_results(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        payload = read_json(path, [])
        if isinstance(payload, list):
            rows.extend(row for row in payload if isinstance(row, dict))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a field-first behavioral proxy map for WQB alpha mining.")
    parser.add_argument("--fields", default=".local/data/all_wqb_fields.json", help="Path to all_wqb_fields.json.")
    parser.add_argument("--results", action="append", default=[], help="Scan result JSON file. Can be passed multiple times.")
    parser.add_argument("--run-dir", default="", help="Optional run directory; all *_results.json files are included.")
    parser.add_argument("--output", default=".local/data/behavioral_proxy/behavioral_proxy_map.json", help="Output JSON path.")
    args = parser.parse_args()

    result_paths = [Path(path) for path in args.results]
    if args.run_dir:
        result_paths.extend(sorted(Path(args.run_dir).glob("*_results.json")))

    report = build_behavioral_proxy_map(load_fields(Path(args.fields)), result_rows=load_results(result_paths))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output} mechanisms={report['mechanism_count']} results={len(result_paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
