from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from typing import Any, Sequence, TextIO

from scripts.checks.release_audit import audit_candidates


@dataclass(frozen=True, slots=True)
class ReleaseBlocker:
    id: str
    message: str


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    schema_version: int
    include_files: tuple[str, ...]
    include_trees: tuple[str, ...]
    exclude_paths: tuple[str, ...]
    exclude_trees: tuple[str, ...]
    exclude_glob_patterns: tuple[str, ...]
    required_files: tuple[str, ...]
    release_blockers: tuple[ReleaseBlocker, ...]


@dataclass(frozen=True, slots=True)
class SelectedFile:
    relative_path: str
    source_path: Path
    size: int


@dataclass(frozen=True, slots=True)
class ExportedFile:
    relative_path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SnapshotExportResult:
    output_path: Path
    report: dict[str, object]
    files: tuple[ExportedFile, ...]


class SnapshotExportError(RuntimeError):
    def __init__(self, code: str, message: str, *, path: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.path = path


def load_manifest(path: Path | str) -> SnapshotManifest:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotExportError("invalid_manifest", "Unable to load snapshot manifest.") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SnapshotExportError("invalid_manifest", "Snapshot manifest schema_version must be 1.")

    include = _require_object(payload, "include")
    exclude = _require_object(payload, "exclude")
    blockers_payload = payload.get("release_blockers")
    if not isinstance(blockers_payload, list):
        raise SnapshotExportError("invalid_manifest", "release_blockers must be a list.")

    blocker_ids: set[str] = set()
    blockers: list[ReleaseBlocker] = []
    for item in blockers_payload:
        if not isinstance(item, dict):
            raise SnapshotExportError("invalid_manifest", "Each release blocker must be an object.")
        blocker_id = item.get("id")
        message = item.get("message")
        if not isinstance(blocker_id, str) or not blocker_id.strip() or not isinstance(message, str) or not message.strip():
            raise SnapshotExportError("invalid_manifest", "Release blockers require non-empty id and message.")
        if blocker_id in blocker_ids:
            raise SnapshotExportError("invalid_manifest", "Release blocker ids must be unique.")
        blocker_ids.add(blocker_id)
        blockers.append(ReleaseBlocker(blocker_id, message))

    return SnapshotManifest(
        schema_version=1,
        include_files=_path_list(include, "files"),
        include_trees=_path_list(include, "trees"),
        exclude_paths=_path_list(exclude, "paths"),
        exclude_trees=_path_list(exclude, "trees"),
        exclude_glob_patterns=_string_list(exclude, "glob_patterns"),
        required_files=_path_list(payload, "required_files"),
        release_blockers=tuple(blockers),
    )


def select_snapshot_files(workspace_root: Path | str, manifest: SnapshotManifest) -> list[SelectedFile]:
    root = Path(workspace_root).resolve()
    for relative_path in manifest.required_files:
        path = root / PurePosixPath(relative_path)
        if not path.is_file() or path.is_symlink():
            raise SnapshotExportError(
                "missing_required_file",
                "A required public snapshot file is missing.",
                path=relative_path,
            )

    candidates: dict[str, Path] = {}
    for relative_path in manifest.include_files:
        source = root / PurePosixPath(relative_path)
        if _is_excluded(relative_path, manifest):
            continue
        if source.is_symlink():
            raise SnapshotExportError("symlink_rejected", "Symlinks cannot be exported.", path=relative_path)
        if source.is_file():
            candidates[relative_path] = source

    for tree_path in manifest.include_trees:
        tree_root = root / PurePosixPath(tree_path)
        if not tree_root.exists() or _is_excluded(tree_path, manifest):
            continue
        if tree_root.is_symlink():
            raise SnapshotExportError("symlink_rejected", "Symlinks cannot be exported.", path=tree_path)
        if not tree_root.is_dir():
            raise SnapshotExportError("invalid_manifest", "An included tree is not a directory.", path=tree_path)
        for directory, directory_names, file_names in os.walk(tree_root, followlinks=False):
            directory_path = Path(directory)
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                child = directory_path / name
                relative = child.relative_to(root).as_posix()
                if _is_excluded(relative, manifest):
                    continue
                if child.is_symlink():
                    raise SnapshotExportError("symlink_rejected", "Symlinks cannot be exported.", path=relative)
                kept_directories.append(name)
            directory_names[:] = kept_directories
            for name in sorted(file_names):
                source = directory_path / name
                relative = source.relative_to(root).as_posix()
                if _is_excluded(relative, manifest):
                    continue
                if source.is_symlink():
                    raise SnapshotExportError("symlink_rejected", "Symlinks cannot be exported.", path=relative)
                if source.is_file():
                    candidates[relative] = source

    return [
        SelectedFile(relative_path=relative, source_path=source, size=source.stat().st_size)
        for relative, source in sorted(candidates.items())
    ]


def build_snapshot_report(
    workspace_root: Path | str,
    manifest: SnapshotManifest,
    selected_files: Sequence[SelectedFile],
) -> dict[str, object]:
    source_commit, source_dirty = _git_provenance(Path(workspace_root).resolve())
    blockers = [{"id": blocker.id, "message": blocker.message} for blocker in manifest.release_blockers]
    return {
        "schema_version": manifest.schema_version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "status": "draft" if blockers else "ready",
        "publish_ready": not blockers,
        "selected_file_count": len(selected_files),
        "selected_total_bytes": sum(item.size for item in selected_files),
        "selected_paths": [item.relative_path for item in selected_files],
        "release_blockers": blockers,
    }


def export_public_snapshot(
    workspace_root: Path | str,
    output: Path | str,
    manifest_path: Path | str,
) -> SnapshotExportResult:
    root = Path(workspace_root).resolve()
    destination = Path(output).resolve()
    manifest = load_manifest(manifest_path)
    selected = select_snapshot_files(root, manifest)
    _validate_output(root, destination, manifest, selected)
    output_preexisted = destination.exists()
    destination.mkdir(parents=True, exist_ok=True)

    exported: list[ExportedFile] = []
    try:
        for item in selected:
            target = destination / PurePosixPath(item.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item.source_path, target)
            content = target.read_bytes()
            exported.append(
                ExportedFile(
                    relative_path=item.relative_path,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )

        audit_report = audit_candidates(destination, [item.relative_path for item in exported])
        if not audit_report.ok:
            first = audit_report.findings[0]
            raise SnapshotExportError(
                "release_audit_failed",
                f"Exported file failed release audit with finding {first.code}.",
                path=first.path,
            )

        report = build_snapshot_report(root, manifest, selected)
        metadata = {
            **report,
            "files": [
                {"path": item.relative_path, "size": item.size, "sha256": item.sha256}
                for item in exported
            ],
        }
        blockers = {
            "schema_version": manifest.schema_version,
            "status": report["status"],
            "publish_ready": report["publish_ready"],
            "release_blockers": report["release_blockers"],
        }
        _write_json_atomic(destination / "PUBLIC_SNAPSHOT_MANIFEST.json", metadata)
        _write_json_atomic(destination / "PUBLIC_SNAPSHOT_BLOCKERS.json", blockers)
        return SnapshotExportResult(destination, report, tuple(exported))
    except SnapshotExportError:
        _cleanup_failed_output(destination, output_preexisted)
        raise
    except OSError as exc:
        _cleanup_failed_output(destination, output_preexisted)
        raise SnapshotExportError("copy_failed", "Unable to create public snapshot.") from exc


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Create a history-free draft public source snapshot.")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--output", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(list(argv) if argv is not None else None)
    out_stream = stdout or sys.stdout
    err_stream = stderr or sys.stderr
    root = Path(args.workspace_root).resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else root / "release" / "public_snapshot_manifest.json"

    try:
        if args.check:
            manifest = load_manifest(manifest_path)
            selected = select_snapshot_files(root, manifest)
            report = build_snapshot_report(root, manifest, selected)
        else:
            result = export_public_snapshot(root, Path(args.output), manifest_path)
            report = result.report
    except SnapshotExportError as exc:
        payload = {"ok": False, "error": {"code": exc.code, "message": str(exc), "path": exc.path}}
        if args.as_json:
            out_stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        else:
            err_stream.write(f"[{exc.code}] {exc.path}: {exc}\n")
        return 1

    if args.as_json:
        out_stream.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        out_stream.write(
            f"Snapshot check selected {report['selected_file_count']} files; "
            f"status={report['status']} publish_ready={str(report['publish_ready']).lower()}.\n"
        )
    return 0


def main() -> None:
    raise SystemExit(run())


def _require_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise SnapshotExportError("invalid_manifest", f"{key} must be an object.")
    return value


def _path_list(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _string_list(payload, key)
    normalized = tuple(_validate_manifest_path(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise SnapshotExportError("invalid_manifest", f"{key} contains duplicate paths.")
    return normalized


def _string_list(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise SnapshotExportError("invalid_manifest", f"{key} must be a list of non-empty strings.")
    return tuple(value)


def _validate_manifest_path(value: str) -> str:
    if "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise SnapshotExportError("unsafe_manifest_path", "Manifest paths must use relative POSIX syntax.", path=value)
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("./") or any(part in {"", ".", ".."} for part in path.parts):
        raise SnapshotExportError("unsafe_manifest_path", "Manifest path escapes or ambiguously names the workspace.", path=value)
    return path.as_posix()


def _is_excluded(relative_path: str, manifest: SnapshotManifest) -> bool:
    normalized = PurePosixPath(relative_path).as_posix()
    if normalized in manifest.exclude_paths:
        return True
    if any(normalized == tree or normalized.startswith(f"{tree}/") for tree in manifest.exclude_trees):
        return True
    return any(_glob_matches(normalized, pattern) for pattern in manifest.exclude_glob_patterns)


def _glob_matches(relative_path: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(relative_path, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatchcase(relative_path, pattern[3:]):
        return True
    return False


def _git_provenance(root: Path) -> tuple[str, bool | None]:
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if commit.returncode != 0 or status.returncode != 0:
        return "", None
    return commit.stdout.strip(), bool(status.stdout.strip())


def _validate_output(
    root: Path,
    destination: Path,
    manifest: SnapshotManifest,
    selected_files: Sequence[SelectedFile],
) -> None:
    if destination.is_symlink():
        raise SnapshotExportError("output_overlaps_source", "Snapshot output cannot be a symlink.")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise SnapshotExportError("output_not_empty", "Snapshot output must be an empty directory.")
    if destination == root or destination in root.parents:
        raise SnapshotExportError("output_overlaps_source", "Snapshot output overlaps the workspace.")

    for tree in manifest.include_trees:
        source_tree = (root / PurePosixPath(tree)).resolve()
        if destination == source_tree or source_tree in destination.parents or destination in source_tree.parents:
            raise SnapshotExportError(
                "output_overlaps_source",
                "Snapshot output overlaps an included source tree.",
                path=tree,
            )
    for item in selected_files:
        source = item.source_path.resolve()
        if destination == source or source in destination.parents or destination in source.parents:
            raise SnapshotExportError(
                "output_overlaps_source",
                "Snapshot output overlaps an included source file.",
                path=item.relative_path,
            )


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _cleanup_failed_output(destination: Path, output_preexisted: bool) -> None:
    if not destination.exists() or not destination.is_dir():
        return
    for child in list(destination.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    if not output_preexisted:
        destination.rmdir()


if __name__ == "__main__":
    main()
