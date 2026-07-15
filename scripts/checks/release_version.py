from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from scripts.json_output import write_json_line


TAG_PATTERN = re.compile(r"^v(?P<version>\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)$")


def github_release_tag(environ: Mapping[str, str] = os.environ) -> str:
    ref_type = environ.get("GITHUB_REF_TYPE", "")
    ref = environ.get("GITHUB_REF", "")
    if ref_type != "tag" and not ref.startswith("refs/tags/"):
        return ""
    return environ.get("GITHUB_REF_NAME", "") or ref.removeprefix("refs/tags/")


def check_release_versions(root: Path, *, tag: str | None = None) -> dict[str, Any]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    expected = str(project["project"]["version"])
    observed = {
        "pyproject.toml": expected,
        "CITATION.cff": _citation_version(root / "CITATION.cff"),
        "packages/wqb-agent-mcp/package.json": _python_version_from_semver(_package_version(root / "packages/wqb-agent-mcp/package.json")),
        "packages/wqb-agent-ui/package.json": _python_version_from_semver(_package_version(root / "packages/wqb-agent-ui/package.json")),
    }
    mismatches = {path: version for path, version in observed.items() if version != expected}
    tag_error = ""
    if tag:
        match = TAG_PATTERN.fullmatch(tag)
        if match is None:
            tag_error = f"release tag must use PEP 440 form such as v1.2.0 or v1.2.0a1: {tag}"
        elif str(Version(match.group("version"))) != str(Version(expected)):
            tag_error = f"release tag {tag} does not match project version {expected}"
    return {
        "status": "ok" if not mismatches and not tag_error else "failed",
        "expected_version": expected,
        "observed_versions": observed,
        "tag": tag or "",
        "mismatches": mismatches,
        "tag_error": tag_error,
    }


def _citation_version(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"\'')
    return ""


def _package_version(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("version") or "")


def _python_version_from_semver(value: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-(alpha|beta|rc)\.(\d+))?", value)
    if match is None:
        try:
            return str(Version(value))
        except InvalidVersion:
            return value
    base, label, number = match.groups()
    if label is None:
        return base
    pep_label = {"alpha": "a", "beta": "b", "rc": "rc"}[label]
    return f"{base}{pep_label}{number}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify release metadata and tag version consistency.")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--tag", default=github_release_tag())
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = check_release_versions(Path(args.workspace_root).resolve(), tag=args.tag or None)
    if args.json:
        write_json_line(report, sys.stdout)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
