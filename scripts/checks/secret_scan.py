"""Scan the generated public snapshot with pinned Gitleaks rules."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


GITLEAKS_GO_MODULE = "github.com/zricethezav/gitleaks/v8@v8.30.1"


@dataclass(frozen=True)
class SecretScanReport:
    status: str
    source: str
    report: str
    tool: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gitleaks_command(source: Path, report: Path) -> tuple[str, ...]:
    executable = shutil.which("gitleaks")
    prefix = (executable,) if executable else ()
    if not prefix:
        go = shutil.which("go")
        if go:
            prefix = (go, "run", GITLEAKS_GO_MODULE)
        else:
            prefix = ("gitleaks",)
    return (
        *prefix,
        "dir",
        str(source),
        "--no-banner",
        "--redact",
        "--report-format",
        "json",
        "--report-path",
        str(report),
    )


def scan_snapshot(source: Path, report: Path) -> SecretScanReport:
    source = source.resolve()
    report = report.resolve()
    if not source.is_dir():
        return SecretScanReport("fail", str(source), str(report), "", "snapshot source does not exist")
    report.parent.mkdir(parents=True, exist_ok=True)
    command = gitleaks_command(source, report)
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except FileNotFoundError as exc:
        return SecretScanReport("missing_tool", str(source), str(report), command[0], str(exc))
    if completed.returncode != 0:
        error = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        return SecretScanReport("fail", str(source), str(report), command[0], error)
    if not report.exists():
        report.write_text("[]\n", encoding="utf-8")
    return SecretScanReport("pass", str(source), str(report), command[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="dist/public-snapshot")
    parser.add_argument("--report", default="dist/audit/gitleaks-public-snapshot.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = scan_snapshot(Path(args.source), Path(args.report))
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(f"secret scan: {result.status}")
    return 0 if result.status == "pass" else 2 if result.status == "missing_tool" else 1


if __name__ == "__main__":
    raise SystemExit(main())
