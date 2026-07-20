"""Generate dependency vulnerability, license, and CycloneDX release reports."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from importlib.metadata import distributions
from pathlib import Path
from typing import Any, Mapping

from scripts.lib.json_output import write_json_line


_CLASSIFIER_LICENSES = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: Public Domain": "Unlicense",
}
_LICENSE_ALIASES = {
    "Apache 2.0": "Apache-2.0",
    "Apache-2.0": "Apache-2.0",
    "BSD-2-Clause": "BSD-2-Clause",
    "BSD-3-Clause": "BSD-3-Clause",
    "ISC": "ISC",
    "MIT": "MIT",
    "MIT-0": "MIT-0",
    "MPL-2.0": "MPL-2.0",
    "PSF": "PSF-2.0",
    "PSFL": "PSF-2.0",
    "Unlicense": "Unlicense",
}
_EXPRESSION_WORDS = frozenset({"AND", "OR", "WITH"})


@dataclass(frozen=True)
class CommandReport:
    name: str
    status: str
    output: str
    returncode: int
    error: str = ""


@dataclass(frozen=True)
class SupplyChainReport:
    status: str
    reports: tuple[CommandReport, ...]
    license_unknown: tuple[str, ...]
    license_disallowed: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reports": [asdict(report) for report in self.reports],
            "license_unknown": list(self.license_unknown),
            "license_disallowed": list(self.license_disallowed),
        }


def canonical_license(metadata: Mapping[str, Any]) -> str:
    expression = str(metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    raw_license = str(metadata.get("License") or "").strip()
    if raw_license in _LICENSE_ALIASES:
        return _LICENSE_ALIASES[raw_license]
    if raw_license.startswith("MIT License"):
        return "MIT"
    if "Redistribution and use in source and binary forms" in raw_license:
        return "BSD-3-Clause"
    classifiers = metadata.get("Classifier") or ()
    if isinstance(classifiers, str):
        classifiers = (classifiers,)
    normalized_classifiers = sorted(
        {
            _CLASSIFIER_LICENSES[str(classifier)]
            for classifier in classifiers
            if str(classifier) in _CLASSIFIER_LICENSES
        }
    )
    if normalized_classifiers:
        return " OR ".join(normalized_classifiers)
    return "UNKNOWN"


def license_ids(expression: str) -> frozenset[str]:
    if not expression or expression == "UNKNOWN":
        return frozenset()
    return frozenset(
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", expression)
        if token.upper() not in _EXPRESSION_WORDS
    )


def evaluate_license_inventory(
    inventory: list[dict[str, str]], policy: Mapping[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    allowed = {str(value) for value in policy.get("allowed_spdx_ids", [])}
    exceptions = {
        (str(item.get("name", "")).lower(), str(item.get("version", "")))
        for item in policy.get("exceptions", [])
        if isinstance(item, Mapping) and str(item.get("rationale", "")).strip()
    }
    unknown: list[str] = []
    disallowed: list[str] = []
    for package in inventory:
        identity = f"{package['name']}=={package['version']}"
        if (package["name"].lower(), package["version"]) in exceptions:
            continue
        ids = license_ids(package["license"])
        if not ids:
            unknown.append(identity)
            continue
        rejected = sorted(ids - allowed)
        if rejected:
            disallowed.append(f"{identity}: {', '.join(rejected)}")
    return tuple(sorted(unknown)), tuple(sorted(disallowed))


def installed_license_inventory() -> list[dict[str, str]]:
    inventory: dict[tuple[str, str], dict[str, str]] = {}
    for item in distributions():
        name = str(item.metadata.get("Name") or "").strip()
        if not name:
            continue
        version = str(item.version)
        metadata = {
            "License-Expression": item.metadata.get("License-Expression"),
            "License": item.metadata.get("License"),
            "Classifier": item.metadata.get_all("Classifier", []),
        }
        inventory[(name.lower(), version)] = {
            "name": name,
            "version": version,
            "license": canonical_license(metadata),
        }
    return [inventory[key] for key in sorted(inventory)]


def _run_report(name: str, command: tuple[str, ...], cwd: Path, output: Path) -> CommandReport:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except FileNotFoundError as exc:
        return CommandReport(name, "missing_tool", str(output), 2, str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(completed.stdout, encoding="utf-8")
    status = "pass" if completed.returncode == 0 else "fail"
    return CommandReport(name, status, str(output), completed.returncode, completed.stderr.strip())


def generate_supply_chain_reports(workspace_root: Path, output_dir: Path) -> SupplyChainReport:
    workspace_root = workspace_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    commands = (
        (
            "python-vulnerabilities",
            (sys.executable, "-m", "pip_audit", "--local", "--format", "json", "--progress-spinner", "off"),
            workspace_root,
            output_dir / "python-vulnerabilities.json",
        ),
        (
            "python-sbom",
            (
                sys.executable,
                "-m",
                "pip_audit",
                "--local",
                "--format",
                "cyclonedx-json",
                "--progress-spinner",
                "off",
            ),
            workspace_root,
            output_dir / "python-sbom.cdx.json",
        ),
        (
            "mcp-vulnerabilities",
            (npm, "audit", "--json", "--audit-level=high", "--registry=https://registry.npmjs.org"),
            workspace_root / "packages/wqb-agent-mcp",
            output_dir / "mcp-vulnerabilities.json",
        ),
        (
            "mcp-sbom",
            (npm, "sbom", "--package-lock-only", "--sbom-format", "cyclonedx"),
            workspace_root / "packages/wqb-agent-mcp",
            output_dir / "mcp-sbom.cdx.json",
        ),
        (
            "ui-vulnerabilities",
            (npm, "audit", "--json", "--audit-level=high", "--registry=https://registry.npmjs.org"),
            workspace_root / "packages/wqb-agent-ui",
            output_dir / "ui-vulnerabilities.json",
        ),
        (
            "ui-sbom",
            (npm, "sbom", "--package-lock-only", "--sbom-format", "cyclonedx"),
            workspace_root / "packages/wqb-agent-ui",
            output_dir / "ui-sbom.cdx.json",
        ),
    )
    reports = tuple(_run_report(name, command, cwd, output) for name, command, cwd, output in commands)

    inventory = installed_license_inventory()
    license_output = output_dir / "python-licenses.json"
    license_output.write_text(json.dumps(inventory, indent=2, sort_keys=True), encoding="utf-8")
    policy_path = workspace_root / "release/allowed_dependency_licenses.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    unknown, disallowed = evaluate_license_inventory(inventory, policy)
    license_status = "pass" if not unknown and not disallowed else "fail"
    license_report = CommandReport("python-licenses", license_status, str(license_output), 0 if license_status == "pass" else 1)
    all_reports = (*reports, license_report)
    if any(report.status == "missing_tool" for report in all_reports):
        status = "missing_tool"
    elif any(report.status == "fail" for report in all_reports):
        status = "fail"
    else:
        status = "pass"
    summary = SupplyChainReport(status, all_reports, unknown, disallowed)
    (output_dir / "summary.json").write_text(
        json.dumps(summary.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--output", default="dist/audit")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = generate_supply_chain_reports(Path(args.workspace_root), Path(args.output))
    if args.json:
        write_json_line(report.to_dict(), sys.stdout)
    else:
        print(f"supply chain: {report.status}")
    return 0 if report.status == "pass" else 2 if report.status == "missing_tool" else 1


if __name__ == "__main__":
    raise SystemExit(main())
