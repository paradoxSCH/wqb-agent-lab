"""Build and verify WQB Agent Lab from a non-editable wheel installation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scripts.dev import ProcessResult, Stage
from scripts.lib.json_output import write_json_line


@dataclass(frozen=True)
class SmokeReport:
    status: str
    wheel: str
    completed_stages: tuple[str, ...]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_process(stage: Stage, cwd: Path) -> ProcessResult:
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


def select_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("wqb_agent_lab-*.whl"), key=lambda path: path.stat().st_mtime)
    if not wheels:
        raise FileNotFoundError(f"No wqb_agent_lab wheel found in {dist_dir}")
    return wheels[-1].resolve()


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_engine(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/wqb-engine.exe" if os.name == "nt" else "bin/wqb-engine")


def smoke_stages(wheel: Path, temp_dir: Path) -> tuple[Stage, ...]:
    venv_dir = temp_dir / "venv"
    python = str(_venv_python(venv_dir))
    engine = str(_venv_engine(venv_dir))
    config_path = temp_dir / "disabled-llm.json"
    config_path.write_text(
        json.dumps({"llm_provider": {"provider": "disabled"}}, indent=2),
        encoding="utf-8",
    )
    return (
        Stage("create-venv", (sys.executable, "-m", "venv", str(venv_dir))),
        Stage("install-wheel", (python, "-m", "pip", "install", str(wheel))),
        Stage(
            "namespace-import",
            (
                python,
                "-c",
                "from wqb_agent_lab.platform import WQBClient; from wqb_agent_lab.workflow import ResearchWorkflow",
            ),
        ),
        Stage("engine-help", (engine, "--help")),
        Stage("schemas-list", (engine, "schemas.list")),
        Stage("schema-digest", (engine, "schemas.digest", "--schema", "candidate")),
        Stage("llm-disabled", (engine, "llm.validate", "--config", str(config_path))),
    )


def _validate_stage_output(stage: Stage, result: ProcessResult) -> None:
    if stage.name not in {"engine-help", "schemas-list", "schema-digest", "llm-disabled"}:
        return
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{stage.name} did not emit JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"{stage.name} returned an unsuccessful payload: {payload}")
    if stage.name == "schemas-list" and "candidate" not in payload.get("data", {}).get("schemas", []):
        raise RuntimeError("installed schema registry does not contain candidate")
    if stage.name == "schema-digest" and not payload.get("data", {}).get("digest"):
        raise RuntimeError("installed schema digest is empty")
    if stage.name == "llm-disabled":
        provider = payload.get("data", {}).get("provider", {})
        provider_id = provider.get("provider") if isinstance(provider, dict) else provider
        if provider_id != "disabled":
            raise RuntimeError("disabled LLM provider did not survive wheel installation")


def smoke_wheel(
    wheel: Path,
    *,
    runner=run_process,
    temp_parent: Path | None = None,
) -> SmokeReport:
    completed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="wqb-artifact-smoke-", dir=temp_parent) as raw_temp:
        temp_dir = Path(raw_temp).resolve()
        for stage in smoke_stages(wheel.resolve(), temp_dir):
            try:
                result = runner(stage, temp_dir)
            except FileNotFoundError as exc:
                return SmokeReport("missing_tool", str(wheel), tuple(completed), str(exc))
            if result.returncode != 0:
                error = "\n".join(part for part in (result.stdout, result.stderr) if part)
                return SmokeReport("fail", str(wheel), tuple(completed), error)
            try:
                _validate_stage_output(stage, result)
            except RuntimeError as exc:
                return SmokeReport("fail", str(wheel), tuple(completed), str(exc))
            completed.append(stage.name)
    return SmokeReport("pass", str(wheel), tuple(completed))


def export_clean_checkout(workspace_root: Path, destination: Path, *, revision: str = "HEAD") -> None:
    archive = destination.parent / "source.tar"
    completed = subprocess.run(
        ["git", "archive", "--format=tar", "-o", str(archive), revision],
        cwd=workspace_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or "git archive failed")
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    with tarfile.open(archive, "r") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if destination_root not in target.parents and target != destination_root:
                raise RuntimeError(f"unsafe path in git archive: {member.name}")
        handle.extractall(destination)


def smoke_clean_checkout(workspace_root: Path) -> SmokeReport:
    with tempfile.TemporaryDirectory(prefix="wqb-clean-checkout-") as raw_temp:
        temp_root = Path(raw_temp).resolve()
        checkout = temp_root / "source"
        try:
            export_clean_checkout(workspace_root.resolve(), checkout)
        except (FileNotFoundError, RuntimeError) as exc:
            return SmokeReport("fail", "", (), str(exc))
        build = Stage(
            "clean-checkout-build",
            (
                sys.executable,
                "-m",
                "build",
                "--sdist",
                "--wheel",
                "--outdir",
                str(temp_root / "dist"),
                ".",
            ),
        )
        result = run_process(build, checkout)
        if result.returncode != 0:
            error = "\n".join(part for part in (result.stdout, result.stderr) if part)
            return SmokeReport("fail", "", (), error)
        try:
            wheel = select_wheel(temp_root / "dist")
        except FileNotFoundError as exc:
            return SmokeReport("fail", "", ("clean-checkout-build",), str(exc))
        report = smoke_wheel(wheel)
        return SmokeReport(
            report.status,
            report.wheel,
            ("clean-checkout-build", *report.completed_stages),
            report.error,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", default="dist/packages")
    parser.add_argument("--wheel")
    parser.add_argument("--clean-checkout", action="store_true")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.clean_checkout:
            report = smoke_clean_checkout(Path(args.workspace_root))
        else:
            wheel = Path(args.wheel).resolve() if args.wheel else select_wheel(Path(args.dist_dir))
            report = smoke_wheel(wheel)
    except FileNotFoundError as exc:
        report = SmokeReport("missing_artifact", "", (), str(exc))
    if args.json:
        write_json_line(report.to_dict(), sys.stdout)
    else:
        print(f"artifact smoke: {report.status}")
        if report.error:
            print(report.error, file=sys.stderr)
    return 0 if report.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
