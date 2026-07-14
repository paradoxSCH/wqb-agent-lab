"""Machine-readable onboarding diagnostics for humans and coding agents."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Callable
from typing import TextIO

from scripts.json_output import write_json_line


PYTHON_TARGET = "3.12"
UV_MINIMUM = (0, 11, 27)
SUPPORTED_NODE_RANGES = "Node.js 22.12+ or 24.x LTS"
NODE_DOWNLOAD_URL = "https://nodejs.org/en/download"
UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"


@dataclass(frozen=True)
class DoctorCheck:
    id: str
    status: str
    message: str
    detected: str = ""
    expected: str = ""
    fix_command: str = ""
    docs_url: str = ""


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", value)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def _command_version(executable: str, argument: str = "--version") -> str:
    completed = subprocess.run(
        [executable, argument],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return (completed.stdout or completed.stderr).strip()


def _npm_runtime_version(executable: str) -> str:
    completed = subprocess.run(
        [executable, "version", "--json"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("node") or "") if isinstance(payload, dict) else ""


def _copy_command(source: str, destination: str) -> str:
    if os.name == "nt":
        parent = str(Path(destination).parent).replace("/", "\\")
        return (
            f"New-Item -ItemType Directory -Force {parent} | Out-Null; "
            f"Copy-Item {source.replace('/', chr(92))} {destination.replace('/', chr(92))}"
        )
    return f"mkdir -p {Path(destination).parent.as_posix()} && cp {source} {destination}"


def _tool_check(
    *,
    check_id: str,
    command_names: tuple[str, ...],
    expected: str,
    supported: Callable[[tuple[int, int, int]], bool],
    fix_command: str,
    docs_url: str = "",
) -> DoctorCheck:
    executable = next((shutil.which(name) for name in command_names if shutil.which(name)), None)
    if executable is None:
        return DoctorCheck(
            check_id,
            "fail",
            f"Required tool is not installed or is not on PATH: {command_names[0]}",
            expected=expected,
            fix_command=fix_command,
            docs_url=docs_url,
        )
    raw_version = _command_version(executable)
    parsed = _version_tuple(raw_version)
    if parsed is None or not supported(parsed):
        return DoctorCheck(
            check_id,
            "fail",
            f"Detected {command_names[0]} version is outside the supported range.",
            detected=raw_version or executable,
            expected=expected,
            fix_command=fix_command,
            docs_url=docs_url,
        )
    return DoctorCheck(
        check_id,
        "pass",
        f"{command_names[0]} is available.",
        detected=raw_version,
        expected=expected,
    )


def _runtime_dependency_check() -> DoctorCheck:
    required = ("wqb-agent-lab", "pandas", "python-dotenv", "requests")
    missing: list[str] = []
    installed: list[str] = []
    for distribution in required:
        try:
            installed.append(f"{distribution}=={importlib.metadata.version(distribution)}")
        except importlib.metadata.PackageNotFoundError:
            missing.append(distribution)
    if missing:
        return DoctorCheck(
            "python_dependencies",
            "fail",
            f"Locked runtime dependencies are missing: {', '.join(missing)}",
            detected=", ".join(installed),
            expected="dependencies from pyproject.toml and uv.lock",
            fix_command="uv sync --python 3.12 --frozen",
        )
    return DoctorCheck(
        "python_dependencies",
        "pass",
        "Locked Python runtime dependencies are installed.",
        detected=", ".join(installed),
    )


def _project_files_check(root: Path) -> DoctorCheck:
    required = (
        "pyproject.toml",
        "uv.lock",
        ".env.example",
        "configs/examples/production-workflow.example.json",
    )
    missing = [path for path in required if not (root / path).is_file()]
    if missing:
        return DoctorCheck(
            "project_files",
            "fail",
            f"Repository checkout is incomplete: {', '.join(missing)}",
            expected="run from the root of a complete WQB Agent Lab checkout",
            fix_command="git status && git pull --ff-only",
        )
    return DoctorCheck("project_files", "pass", "Required repository files are present.")


def _local_config_checks(root: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    env_path = root / ".env"
    if env_path.is_file():
        checks.append(DoctorCheck("local_env", "pass", "Local .env exists; credential values were not read."))
    else:
        checks.append(
            DoctorCheck(
                "local_env",
                "warn",
                "Local .env is not initialized. Offline demo still works, but provider and WQB credentials cannot be configured yet.",
                expected=".env copied from .env.example",
                fix_command=_copy_command(".env.example", ".env"),
            )
        )

    config_path = root / ".local" / "research" / "workflows" / "production.json"
    if config_path.is_file():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            checks.append(
                DoctorCheck(
                    "workflow_config",
                    "fail",
                    f"Local workflow config cannot be parsed: {exc}",
                    detected=str(config_path),
                    expected="valid UTF-8 JSON",
                    fix_command="uv run wqb-engine policy.validate --config .local/research/workflows/production.json",
                )
            )
        else:
            if isinstance(payload, dict) and "research_policy" in payload:
                checks.append(DoctorCheck("workflow_config", "pass", "Local research workflow config is initialized."))
            else:
                checks.append(
                    DoctorCheck(
                        "workflow_config",
                        "fail",
                        "Local workflow config does not contain research_policy.",
                        expected="a workflow copied from the public production example",
                        fix_command="uv run wqb-engine policy.validate --config .local/research/workflows/production.json",
                    )
                )
    else:
        checks.append(
            DoctorCheck(
                "workflow_config",
                "warn",
                "Local research workflow config is not initialized. Offline demo still works.",
                expected=".local/research/workflows/production.json",
                fix_command=_copy_command(
                    "configs/examples/production-workflow.example.json",
                    ".local/research/workflows/production.json",
                ),
            )
        )
    return checks


def _node_checks(root: Path) -> list[DoctorCheck]:
    def supported_node(version: tuple[int, int, int]) -> bool:
        return (version[0] == 22 and version >= (22, 12, 0)) or version[0] == 24

    checks = [
        _tool_check(
            check_id="node",
            command_names=("node",),
            expected=SUPPORTED_NODE_RANGES,
            supported=supported_node,
            fix_command="Install an LTS release from https://nodejs.org/en/download, then open a new terminal.",
            docs_url=NODE_DOWNLOAD_URL,
        ),
        _tool_check(
            check_id="npm",
            command_names=(("npm.cmd", "npm") if os.name == "nt" else ("npm",)),
            expected="npm 10.x or 11.x",
            supported=lambda version: version[0] in {10, 11},
            fix_command="Install Node.js 22.12+ or 24 LTS, which includes a supported npm.",
            docs_url=NODE_DOWNLOAD_URL,
        ),
    ]
    node_executable = shutil.which("node")
    npm_executable = shutil.which("npm.cmd") or shutil.which("npm")
    if node_executable and npm_executable:
        direct_node = _version_tuple(_command_version(node_executable))
        npm_node_raw = _npm_runtime_version(npm_executable)
        npm_node = _version_tuple(npm_node_raw)
        if direct_node is None or npm_node is None or direct_node != npm_node:
            checks.append(
                DoctorCheck(
                    "npm_node_runtime",
                    "fail",
                    "npm is running under a different Node.js installation than the node command on PATH.",
                    detected=f"node={direct_node or 'unknown'}, npm_runtime={npm_node_raw or 'unknown'}",
                    expected="node and npm must resolve to the same Node.js installation",
                    fix_command=(
                        "Remove stale Node/npm entries from PATH, open a new terminal, then rerun doctor."
                    ),
                    docs_url=NODE_DOWNLOAD_URL,
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    "npm_node_runtime",
                    "pass",
                    "node and npm use the same Node.js runtime.",
                    detected=npm_node_raw,
                )
            )
    package_dirs = ("packages/wqb-agent-mcp", "packages/wqb-agent-ui")
    missing = [path for path in package_dirs if not (root / path / "node_modules").is_dir()]
    if missing:
        checks.append(
            DoctorCheck(
                "node_dependencies",
                "fail",
                f"Locked Node dependencies are not installed for: {', '.join(missing)}",
                expected="node_modules created by npm ci in both packages",
                fix_command=(
                    "npm ci --prefix packages/wqb-agent-mcp && "
                    "npm ci --prefix packages/wqb-agent-ui"
                ),
            )
        )
    else:
        checks.append(DoctorCheck("node_dependencies", "pass", "Locked Node dependencies are installed."))
    ui_index = root / "packages/wqb-agent-ui/dist/index.html"
    if not ui_index.is_file():
        checks.append(
            DoctorCheck(
                "dashboard_build",
                "fail",
                "The React dashboard production build is missing.",
                expected="packages/wqb-agent-ui/dist/index.html",
                fix_command="npm run build --prefix packages/wqb-agent-ui",
            )
        )
    else:
        checks.append(DoctorCheck("dashboard_build", "pass", "The React dashboard production build is available."))
    return checks


def build_doctor_report(profile: str, workspace_root: Path) -> dict[str, object]:
    root = workspace_root.resolve()
    if profile not in {"runtime", "full"}:
        raise ValueError(f"invalid doctor profile: {profile}")

    python_version = tuple(sys.version_info[:3])
    python_ok = (3, 11, 0) <= python_version < (3, 13, 0)
    checks = [
        DoctorCheck(
            "python",
            "pass" if python_ok else "fail",
            "Python runtime is supported." if python_ok else "Python runtime is outside the supported range.",
            detected=".".join(str(part) for part in python_version),
            expected="Python >=3.11,<3.13 (3.12 recommended)",
            fix_command="uv python install 3.12 && uv sync --python 3.12 --frozen" if not python_ok else "",
        ),
        _tool_check(
            check_id="uv",
            command_names=("uv",),
            expected="uv >=0.11.27",
            supported=lambda version: version >= UV_MINIMUM,
            fix_command="Install uv from https://docs.astral.sh/uv/getting-started/installation/",
            docs_url=UV_INSTALL_URL,
        ),
        _project_files_check(root),
        _runtime_dependency_check(),
        *_local_config_checks(root),
    ]
    if profile == "full":
        checks.extend(_node_checks(root))

    fail_count = sum(check.status == "fail" for check in checks)
    warn_count = sum(check.status == "warn" for check in checks)
    status = "blocked" if fail_count else "attention" if warn_count else "ready"
    actions = [
        {
            "check_id": check.id,
            "command": check.fix_command,
            "docs_url": check.docs_url,
        }
        for check in checks
        if check.status != "pass" and (check.fix_command or check.docs_url)
    ]
    next_command = (
        f"uv run python -m scripts.dev doctor --profile {profile} --json"
        if fail_count
        else "uv run wqb-engine demo --workspace-root . --run-tag product-demo"
        if profile == "runtime"
        else "uv run python -m scripts.dev check --json"
    )
    return {
        "command": "doctor",
        "status": status,
        "profile": profile,
        "workspace_root": str(root),
        "summary": {
            "pass": sum(check.status == "pass" for check in checks),
            "warn": warn_count,
            "fail": fail_count,
        },
        "checks": [asdict(check) for check in checks],
        "actions": actions,
        "next_command": next_command,
    }


def write_doctor_report(report: dict[str, object], *, json_output: bool, stdout: TextIO) -> None:
    if json_output:
        write_json_line(report, stdout)
        return
    stdout.write(f"doctor ({report['profile']}): {report['status']}\n")
    for item in report["checks"]:
        check = item if isinstance(item, dict) else {}
        stdout.write(f"[{str(check.get('status', '')).upper()}] {check.get('id')}: {check.get('message')}\n")
        if check.get("fix_command"):
            stdout.write(f"  fix: {check['fix_command']}\n")
    stdout.write(f"next: {report['next_command']}\n")
