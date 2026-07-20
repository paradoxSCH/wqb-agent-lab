"""Export, build, and install the generated public source snapshot."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.checks.artifact_smoke import select_wheel, smoke_wheel
from scripts.lib.json_output import write_json_line
from scripts.release.export_public_snapshot import export_public_snapshot


FORBIDDEN_PACKAGE_PREFIXES = (
    ".local/",
    "configs/scans/",
    "configs/workflows/",
    "docs/archive/",
    "docs/superpowers/",
    "logs/",
)
RELEASE_CHECK_SNAPSHOT = "dist/release-check/public-snapshot"
RELEASE_CHECK_AUDIT = "dist/release-check/public-snapshot-audit"
GENERATED_DIRECTORY_NAMES = frozenset({"build", "dist", "node_modules", "__pycache__"})
GENERATED_METADATA_NAMES = frozenset({"PUBLIC_SNAPSHOT_MANIFEST.json", "PUBLIC_SNAPSHOT_BLOCKERS.json"})


@dataclass(frozen=True)
class PublicSnapshotSmokeReport:
    status: str
    snapshot: str
    selected_file_count: int
    wheel: str = ""
    sdist: str = ""
    error: str = ""
    audit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _reset_canonical_output(workspace_root: Path, output: Path, expected_relative: str) -> Path:
    workspace_root = workspace_root.resolve()
    expected = (workspace_root / expected_relative).resolve()
    resolved = output.resolve()
    if resolved != expected:
        raise ValueError(f"snapshot output must be {expected}")
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise ValueError("snapshot output must be a real directory")
        shutil.rmtree(resolved)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def reset_snapshot_output(workspace_root: Path, output: Path) -> Path:
    return _reset_canonical_output(workspace_root, output, RELEASE_CHECK_SNAPSHOT)


def reset_audit_output(workspace_root: Path) -> Path:
    return _reset_canonical_output(
        workspace_root,
        workspace_root / RELEASE_CHECK_AUDIT,
        RELEASE_CHECK_AUDIT,
    )


def snapshot_inventory(snapshot: Path) -> dict[str, str]:
    return {
        path.relative_to(snapshot).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(snapshot.rglob("*"))
        if path.is_file()
    }


def generated_snapshot_paths(snapshot: Path) -> tuple[str, ...]:
    contaminated: list[str] = []
    for path in sorted(snapshot.rglob("*")):
        relative = path.relative_to(snapshot)
        if any(part in GENERATED_DIRECTORY_NAMES or part.endswith(".egg-info") for part in relative.parts):
            contaminated.append(relative.as_posix())
        elif path.is_file() and path.name in GENERATED_METADATA_NAMES:
            contaminated.append(relative.as_posix())
    return tuple(sorted(contaminated))


def forbidden_sdist_members(path: Path) -> tuple[str, ...]:
    forbidden: list[str] = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts
            relative = PurePosixPath(*parts[1:]).as_posix() if len(parts) > 1 else ""
            if any(relative == prefix.rstrip("/") or relative.startswith(prefix) for prefix in FORBIDDEN_PACKAGE_PREFIXES):
                forbidden.append(relative)
    return tuple(sorted(set(forbidden)))


def run_public_snapshot_smoke(workspace_root: Path, output: Path) -> PublicSnapshotSmokeReport:
    workspace_root = workspace_root.resolve()
    try:
        snapshot = reset_snapshot_output(workspace_root, output)
        audit_output = reset_audit_output(workspace_root)
        exported = export_public_snapshot(
            workspace_root,
            snapshot,
            workspace_root / "release/public_snapshot_manifest.json",
            audit_output=audit_output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return PublicSnapshotSmokeReport("fail", str(output), 0, error=str(exc))

    package_dir = workspace_root / "dist/public-packages"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    contamination = generated_snapshot_paths(snapshot)
    if contamination:
        return PublicSnapshotSmokeReport(
            "fail",
            str(snapshot),
            exported.report["selected_file_count"],
            error=f"generated files in pristine snapshot: {', '.join(contamination)}",
            audit=str(audit_output),
        )
    pristine_inventory = snapshot_inventory(snapshot)
    smoke_parent = workspace_root / "dist/release-check"
    smoke_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="public-snapshot-smoke-", dir=smoke_parent) as raw:
        smoke_workspace = Path(raw) / "source"
        shutil.copytree(snapshot, smoke_workspace)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--sdist",
                "--wheel",
                "--outdir",
                str(package_dir),
                ".",
            ],
            cwd=smoke_workspace,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    if completed.returncode != 0:
        error = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        return PublicSnapshotSmokeReport(
            "fail",
            str(snapshot),
            exported.report["selected_file_count"],
            error=error,
            audit=str(audit_output),
        )

    if snapshot_inventory(snapshot) != pristine_inventory or generated_snapshot_paths(snapshot):
        return PublicSnapshotSmokeReport(
            "fail",
            str(snapshot),
            exported.report["selected_file_count"],
            error="public snapshot changed during smoke build",
            audit=str(audit_output),
        )

    try:
        wheel = select_wheel(package_dir)
        sdist = max(package_dir.glob("wqb_agent_lab-*.tar.gz"), key=lambda path: path.stat().st_mtime)
    except (FileNotFoundError, ValueError) as exc:
        return PublicSnapshotSmokeReport(
            "fail", str(snapshot), exported.report["selected_file_count"], error=str(exc)
        )
    forbidden = forbidden_sdist_members(sdist)
    if forbidden:
        return PublicSnapshotSmokeReport(
            "fail",
            str(snapshot),
            exported.report["selected_file_count"],
            str(wheel),
            str(sdist),
            f"private or archived paths in sdist: {', '.join(forbidden)}",
        )
    installed = smoke_wheel(wheel)
    return PublicSnapshotSmokeReport(
        installed.status,
        str(snapshot),
        exported.report["selected_file_count"],
        str(wheel),
        str(sdist),
        installed.error,
        str(audit_output),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--output", default=RELEASE_CHECK_SNAPSHOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_public_snapshot_smoke(Path(args.workspace_root), Path(args.output))
    if args.json:
        write_json_line(report.to_dict(), sys.stdout)
    else:
        print(f"public snapshot smoke: {report.status}")
    return 0 if report.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
