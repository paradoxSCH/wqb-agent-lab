from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.contracts import assert_valid_contract, schema_digest


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "session_token",
)


class SensitiveManifestValueError(ValueError):
    pass


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _metadata(value: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    payload = dict(value or {})
    _reject_sensitive_keys(payload)
    frozen = _freeze(payload)
    if not isinstance(frozen, Mapping):
        raise TypeError("manifest metadata must be an object")
    return frozen


def _reject_sensitive_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            child_path = f"{path}.{key}"
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                raise SensitiveManifestValueError(f"sensitive manifest key is forbidden: {child_path}")
            _reject_sensitive_keys(item, child_path)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_sensitive_keys(item, f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    path: str
    kind: str
    sha256: str
    size_bytes: int
    schema_name: str = ""
    schema_digest: str = ""
    producer: str = ""
    extensions: Mapping[str, Any] = field(default_factory=_metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "schema_name": self.schema_name,
            "schema_digest": self.schema_digest,
            "producer": self.producer,
            "extensions": _thaw(self.extensions),
        }


@dataclass(frozen=True, slots=True)
class RunManifest:
    schema_version: int
    run_id: str
    created_at: str
    code: Mapping[str, Any] = field(default_factory=_metadata)
    runtime: Mapping[str, Any] = field(default_factory=_metadata)
    configuration: Mapping[str, Any] = field(default_factory=_metadata)
    llm: Mapping[str, Any] = field(default_factory=_metadata)
    research: Mapping[str, Any] = field(default_factory=_metadata)
    artifacts: tuple[ArtifactProvenance, ...] = ()
    extensions: Mapping[str, Any] = field(default_factory=_metadata)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        created_at: str,
        code: Mapping[str, Any] | None = None,
        runtime: Mapping[str, Any] | None = None,
        configuration: Mapping[str, Any] | None = None,
        llm: Mapping[str, Any] | None = None,
        research: Mapping[str, Any] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> RunManifest:
        manifest = cls(
            schema_version=1,
            run_id=run_id,
            created_at=created_at,
            code=_metadata(code),
            runtime=_metadata(runtime),
            configuration=_metadata(configuration),
            llm=_metadata(llm),
            research=_metadata(research),
            extensions=_metadata(extensions),
        )
        manifest.validate()
        return manifest

    def with_artifact(self, artifact: ArtifactProvenance) -> RunManifest:
        return self.with_artifacts((artifact,))

    def with_artifacts(self, artifacts: tuple[ArtifactProvenance, ...]) -> RunManifest:
        paths = {artifact.path for artifact in self.artifacts}
        for artifact in artifacts:
            if artifact.path in paths:
                raise ValueError(f"artifact already exists in manifest: {artifact.path}")
            paths.add(artifact.path)
        updated = replace(self, artifacts=(*self.artifacts, *artifacts))
        updated.validate()
        return updated

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "code": _thaw(self.code),
            "runtime": _thaw(self.runtime),
            "configuration": _thaw(self.configuration),
            "llm": _thaw(self.llm),
            "research": _thaw(self.research),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "extensions": _thaw(self.extensions),
        }

    def validate(self) -> None:
        payload = self.to_dict()
        _reject_sensitive_keys(payload)
        assert_valid_contract("run_manifest", payload)

    def digest(self) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def artifact_provenance(
    workspace_root: Path | str,
    path: Path | str,
    *,
    kind: str,
    schema_name: str = "",
    producer: str = "",
    extensions: Mapping[str, Any] | None = None,
) -> ArtifactProvenance:
    root = Path(workspace_root).resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"artifact must be inside the workspace: {resolved}")
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    content = resolved.read_bytes()
    artifact = ArtifactProvenance(
        path=resolved.relative_to(root).as_posix(),
        kind=kind,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        schema_name=schema_name,
        schema_digest=schema_digest(schema_name) if schema_name else "",
        producer=producer,
        extensions=_metadata(extensions),
    )
    _reject_sensitive_keys(artifact.to_dict())
    return artifact


def collect_artifact_provenance(
    workspace_root: Path | str,
    artifact_root: Path | str,
    *,
    exclude: tuple[Path | str, ...] = (),
    producer: str = "",
) -> tuple[ArtifactProvenance, ...]:
    """Snapshot every durable file below an artifact root in stable path order."""

    root = Path(workspace_root).resolve()
    artifacts_root = Path(artifact_root).resolve()
    if not artifacts_root.is_relative_to(root):
        raise ValueError(f"artifact root must be inside the workspace: {artifacts_root}")
    excluded = {Path(path).resolve() for path in exclude}
    artifacts: list[ArtifactProvenance] = []
    if not artifacts_root.exists():
        return ()
    for path in sorted(artifacts_root.rglob("*"), key=lambda item: item.as_posix()):
        resolved = path.resolve()
        if resolved in excluded or not resolved.is_file():
            continue
        try:
            artifacts.append(
                artifact_provenance(
                    root,
                    resolved,
                    kind=_artifact_kind(resolved),
                    producer=producer,
                )
            )
        except FileNotFoundError:
            # Atomic-write scratch files can disappear after enumeration and are
            # not durable run artifacts.
            continue
    return tuple(artifacts)


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".md":
        return "text/markdown"
    if suffix in {".log", ".txt"}:
        return "text/plain"
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return "application/vnd.sqlite3"
    return "application/octet-stream"
