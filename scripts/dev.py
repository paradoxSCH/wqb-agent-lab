"""Canonical local and CI engineering commands for WQB Agent Lab."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

from scripts.lib.json_output import write_json_line


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ("doctor", "check", "test", "build", "release-check")


@dataclass(frozen=True)
class Stage:
    name: str
    command: tuple[str, ...]
    working_directory: str = "."


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class DevReport:
    command: str
    status: str
    duration_seconds: float
    failed_stage: str = ""
    completed_stages: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    manual_gates: tuple[dict[str, str], ...] = ()
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["duration_seconds"] = round(self.duration_seconds, 3)
        return payload


Runner = Callable[[Stage, Path], ProcessResult]


def _python(*args: str) -> tuple[str, ...]:
    return (sys.executable, *args)


def _npm(*args: str) -> tuple[str, ...]:
    executable = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    return (executable, *args)


ENVIRONMENT_STAGE = Stage(
    "environment-doctor",
    _python("-m", "scripts.dev", "doctor", "--profile", "full", "--json"),
)

CHECK_STAGES = (
    Stage("python-ruff", _python("-m", "ruff", "check", ".")),
    Stage(
        "python-product-typecheck",
        _python(
            "-m",
            "pyright",
            "wqb_agent_lab",
        ),
    ),
    Stage(
        "python-compile",
        _python(
            "-m",
            "compileall",
            "-q",
            "src",
            "scripts",
        ),
    ),
    Stage("mcp-typecheck", _npm("run", "typecheck"), "packages/wqb-agent-mcp"),
    Stage("ui-typecheck", _npm("run", "typecheck"), "packages/wqb-agent-ui"),
    Stage("schema-tests", _python("-m", "unittest", "tests.test_schema_contracts", "-q")),
)

TEST_STAGES = (
    Stage(
        "python-tests",
        _python(
            "-m",
            "pytest",
            "-q",
            "--cov=src",
            "--cov=scripts",
            "--cov=wqb_agent_lab",
            "--cov-report=term",
            "--cov-fail-under=70",
        ),
    ),
    Stage("mcp-tests", _npm("test"), "packages/wqb-agent-mcp"),
    Stage("ui-tests", _npm("test"), "packages/wqb-agent-ui"),
)

BUILD_STAGES = (
    Stage(
        "python-build",
        _python("-m", "build", "--sdist", "--wheel", "--outdir", "dist/packages", "."),
    ),
    Stage(
        "artifact-smoke",
        _python("-m", "scripts.checks.artifact_smoke", "--dist-dir", "dist/packages", "--json"),
    ),
    Stage("mcp-build", _npm("run", "build"), "packages/wqb-agent-mcp"),
    Stage("ui-build", _npm("run", "build"), "packages/wqb-agent-ui"),
)

RELEASE_STAGES = (
    Stage("release-version-consistency", _python("-m", "scripts.checks.release_version", "--json")),
    Stage(
        "clean-checkout-smoke",
        _python(
            "-m",
            "scripts.checks.artifact_smoke",
            "--clean-checkout",
            "--workspace-root",
            ".",
            "--json",
        ),
    ),
    Stage("release-audit", _python("-m", "scripts.checks.release_audit", "--json")),
    Stage(
        "public-snapshot-check",
        _python(
            "-m",
            "scripts.release.export_public_snapshot",
            "--workspace-root",
            ".",
            "--output",
            "dist/release-check/public-snapshot",
            "--check",
            "--json",
        ),
    ),
    Stage(
        "public-snapshot-smoke",
        _python(
            "-m",
            "scripts.checks.public_snapshot_smoke",
            "--workspace-root",
            ".",
            "--output",
            "dist/release-check/public-snapshot",
            "--json",
        ),
    ),
    Stage(
        "public-snapshot-secret-scan",
        _python(
            "-m",
            "scripts.checks.secret_scan",
            "--source",
            "dist/release-check/public-snapshot",
            "--report",
            "dist/audit/gitleaks-public-snapshot.json",
            "--json",
        ),
    ),
    Stage(
        "supply-chain-reports",
        _python(
            "-m",
            "scripts.checks.supply_chain",
            "--workspace-root",
            ".",
            "--output",
            "dist/audit",
            "--json",
        ),
    ),
)

ALL_STAGE_NAMES = frozenset(
    stage.name
    for stage in (ENVIRONMENT_STAGE, *CHECK_STAGES, *TEST_STAGES, *BUILD_STAGES, *RELEASE_STAGES)
)

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|token|authorization)\s*([:=])\s*([^\s,;]+)"
)


def redact(text: str) -> str:
    return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)


def run_process(stage: Stage, cwd: Path) -> ProcessResult:
    if stage.name == "python-build":
        build_cache = (cwd / "build").resolve()
        if build_cache.parent != cwd.resolve():
            raise RuntimeError(f"Refusing to clean build cache outside workspace: {build_cache}")
        shutil.rmtree(build_cache, ignore_errors=True)
    completed = subprocess.run(
        list(stage.command),
        cwd=cwd / stage.working_directory,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


def stages_for(command: str) -> tuple[Stage, ...]:
    if command == "check":
        return (ENVIRONMENT_STAGE, *CHECK_STAGES)
    if command == "test":
        return (ENVIRONMENT_STAGE, *TEST_STAGES)
    if command == "build":
        return (ENVIRONMENT_STAGE, *BUILD_STAGES)
    if command == "release-check":
        return (ENVIRONMENT_STAGE, *CHECK_STAGES, *TEST_STAGES, *BUILD_STAGES, *RELEASE_STAGES)
    raise ValueError(f"Invalid command: {command}")


def _parse(argv: Sequence[str]) -> tuple[str, bool, frozenset[str], str]:
    if not argv:
        raise ValueError("invalid command: expected check, test, build, or release-check")
    if argv[0] in {"-h", "--help"}:
        return "help", False, frozenset(), "runtime"

    command = argv[0]
    if command not in COMMANDS:
        raise ValueError(f"invalid command: {command}")

    json_output = False
    profile = "runtime"
    skipped: set[str] = set()
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--json":
            json_output = True
            index += 1
            continue
        if argument == "--profile" and command == "doctor":
            if index + 1 >= len(argv) or argv[index + 1] not in {"runtime", "full"}:
                raise ValueError("--profile requires runtime or full")
            profile = argv[index + 1]
            index += 2
            continue
        if argument == "--skip-completed" and command == "release-check":
            if index + 1 >= len(argv):
                raise ValueError("--skip-completed requires a comma-separated stage list")
            skipped.update(value for value in argv[index + 1].split(",") if value)
            index += 2
            continue
        raise ValueError(f"invalid argument: {argument}")

    unknown = skipped - ALL_STAGE_NAMES
    if unknown:
        raise ValueError(f"unknown stage in --skip-completed: {', '.join(sorted(unknown))}")
    return command, json_output, frozenset(skipped), profile


def _write_report(report: DevReport, *, json_output: bool, stdout: TextIO, stderr: TextIO) -> None:
    if json_output:
        write_json_line(report.to_dict(), stdout)
        return
    target = stdout if report.status in {"pass", "manual_gate"} else stderr
    target.write(f"{report.command}: {report.status}\n")
    if report.failed_stage:
        target.write(f"failed stage: {report.failed_stage}\n")
    if report.message:
        target.write(redact(report.message).rstrip() + "\n")


def run(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = run_process,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    workspace_root: Path = ROOT,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr

    try:
        command, json_output, skipped, profile = _parse(args)
    except ValueError as exc:
        err_stream.write(str(exc) + "\n")
        return 2

    if command == "help":
        out_stream.write(
            "usage: python -m scripts.dev {doctor|check|test|build|release-check} "
            "[--profile runtime|full] [--json]\n"
        )
        return 0

    if command == "doctor":
        from scripts.onboarding import build_doctor_report, write_doctor_report

        report = build_doctor_report(profile, workspace_root)
        write_doctor_report(report, json_output=json_output, stdout=out_stream)
        return 2 if report["status"] == "blocked" else 0

    started = time.perf_counter()
    completed: list[str] = []
    for stage in stages_for(command):
        if stage.name in skipped:
            continue
        if not json_output:
            out_stream.write(f"[{stage.name}] {' '.join(stage.command)}\n")
        try:
            result = runner(stage, workspace_root)
        except FileNotFoundError as exc:
            report = DevReport(
                command=command,
                status="missing_tool",
                duration_seconds=time.perf_counter() - started,
                failed_stage=stage.name,
                completed_stages=tuple(completed),
                message=f"Required tool was not found: {exc}",
            )
            _write_report(report, json_output=json_output, stdout=out_stream, stderr=err_stream)
            return 2
        if result.returncode != 0:
            report = DevReport(
                command=command,
                status="fail",
                duration_seconds=time.perf_counter() - started,
                failed_stage=stage.name,
                completed_stages=tuple(completed),
                message="\n".join(part for part in (result.stdout, result.stderr) if part),
            )
            _write_report(report, json_output=json_output, stdout=out_stream, stderr=err_stream)
            return 1
        completed.append(stage.name)

    artifacts = ("dist/packages",) if command in {"build", "release-check"} else ()
    manual_gates: tuple[dict[str, str], ...] = ()
    status = "pass"
    if command == "release-check":
        manifest = json.loads((workspace_root / "release/public_snapshot_manifest.json").read_text(encoding="utf-8"))
        manual_gates = tuple(
            {"id": str(item["id"]), "message": str(item["message"])}
            for item in manifest.get("release_blockers", [])
            if isinstance(item, dict) and item.get("id") and item.get("message")
        )
        if manual_gates:
            status = "manual_gate"
        artifacts = (
            "dist/packages",
            "dist/public-packages",
            "dist/release-check/public-snapshot",
            "dist/release-check/public-snapshot-audit",
            "dist/audit",
        )
    report = DevReport(
        command=command,
        status=status,
        duration_seconds=time.perf_counter() - started,
        completed_stages=tuple(completed),
        artifact_paths=artifacts,
        manual_gates=manual_gates,
    )
    _write_report(report, json_output=json_output, stdout=out_stream, stderr=err_stream)
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
