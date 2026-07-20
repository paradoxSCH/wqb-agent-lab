from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from wqb_agent_lab.contracts import assert_valid_contract


StageStatus = Literal["running", "completed", "skipped", "deferred", "failed"]
TerminalStageStatus = Literal["completed", "skipped", "deferred"]
ReplayPolicy = Literal["safe", "reconcile"]
_STAGE_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")


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


def _mapping(value: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    frozen = _freeze(dict(value or {}))
    if not isinstance(frozen, Mapping):
        raise TypeError("stage data must be an object")
    return frozen


@dataclass(frozen=True, slots=True)
class StageError:
    code: str
    error_type: str
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "error_type": self.error_type,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: TerminalStageStatus = "completed"
    artifacts: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    output: Mapping[str, Any] = field(default_factory=_mapping)
    extensions: Mapping[str, Any] = field(default_factory=_mapping)

    @classmethod
    def create(
        cls,
        *,
        status: TerminalStageStatus = "completed",
        artifacts: tuple[str, ...] = (),
        messages: tuple[str, ...] = (),
        output: Mapping[str, Any] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> StageOutcome:
        if status not in {"completed", "skipped", "deferred"}:
            raise ValueError(f"invalid terminal stage status: {status}")
        return cls(
            status=status,
            artifacts=tuple(artifacts),
            messages=tuple(messages),
            output=_mapping(output),
            extensions=_mapping(extensions),
        )


@dataclass(frozen=True, slots=True)
class StageResult:
    schema_version: int
    run_id: str
    stage_id: str
    attempt_id: str
    attempt_number: int
    status: StageStatus
    started_at: str
    completed_at: str | None
    input_digest: str
    artifacts: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    output: Mapping[str, Any] = field(default_factory=_mapping)
    error: StageError | None = None
    extensions: Mapping[str, Any] = field(default_factory=_mapping)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        stage_id: str,
        attempt_id: str,
        attempt_number: int,
        status: StageStatus,
        started_at: str,
        completed_at: str | None,
        input_digest: str = "",
        artifacts: tuple[str, ...] = (),
        messages: tuple[str, ...] = (),
        output: Mapping[str, Any] | None = None,
        error: StageError | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> StageResult:
        if not _STAGE_ID.fullmatch(stage_id):
            raise ValueError(f"invalid orchestration stage id: {stage_id}")
        result = cls(
            schema_version=1,
            run_id=run_id,
            stage_id=stage_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            input_digest=input_digest,
            artifacts=tuple(artifacts),
            messages=tuple(messages),
            output=_mapping(output),
            error=error,
            extensions=_mapping(extensions),
        )
        result.validate()
        return result

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StageResult:
        error_payload = payload.get("error")
        error = (
            StageError(
                code=str(error_payload.get("code") or ""),
                error_type=str(error_payload.get("error_type") or ""),
                retryable=bool(error_payload.get("retryable")),
            )
            if isinstance(error_payload, Mapping)
            else None
        )
        return cls.create(
            run_id=str(payload.get("run_id") or ""),
            stage_id=str(payload.get("stage_id") or ""),
            attempt_id=str(payload.get("attempt_id") or ""),
            attempt_number=int(payload.get("attempt_number") or 0),
            status=str(payload.get("status") or "failed"),  # type: ignore[arg-type]
            started_at=str(payload.get("started_at") or ""),
            completed_at=(str(payload["completed_at"]) if payload.get("completed_at") is not None else None),
            input_digest=str(payload.get("input_digest") or ""),
            artifacts=tuple(str(item) for item in payload.get("artifacts") or ()),
            messages=tuple(str(item) for item in payload.get("messages") or ()),
            output=payload.get("output") if isinstance(payload.get("output"), Mapping) else {},
            error=error,
            extensions=(payload.get("extensions") if isinstance(payload.get("extensions"), Mapping) else {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "stage_id": self.stage_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "input_digest": self.input_digest,
            "artifacts": list(self.artifacts),
            "messages": list(self.messages),
            "output": _thaw(self.output),
            "error": self.error.to_dict() if self.error else None,
            "extensions": _thaw(self.extensions),
        }

    def validate(self) -> None:
        assert_valid_contract("workflow_stage_result", self.to_dict())


class StageInterruptionRequiresReconciliation(RuntimeError):
    pass


class StageCheckpointStore:
    def __init__(self, run_dir: Path | str) -> None:
        self.root = Path(run_dir).resolve() / "stage_checkpoints"

    def path_for(self, stage_id: str) -> Path:
        if not _STAGE_ID.fullmatch(stage_id):
            raise ValueError(f"invalid orchestration stage id: {stage_id}")
        return self.root / f"{stage_id}.json"

    def load(self, stage_id: str) -> StageResult | None:
        path = self.path_for(stage_id)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"stage checkpoint must be an object: {path}")
        return StageResult.from_dict(payload)

    def write(self, result: StageResult) -> Path:
        result.validate()
        path = self.path_for(result.stage_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path


class StageRunner:
    def __init__(
        self,
        store: StageCheckpointStore,
        *,
        clock: Callable[[], datetime] = datetime.now,
        attempt_ids: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.store = store
        self.clock = clock
        self.attempt_ids = attempt_ids

    def run(
        self,
        *,
        run_id: str,
        stage_id: str,
        input_digest: str,
        execute: Callable[[], StageOutcome],
        replay_policy: ReplayPolicy,
        started_at: datetime | None = None,
        reconcile: Callable[[StageResult], bool] | None = None,
    ) -> StageResult:
        previous = self.store.load(stage_id)
        if previous is not None and previous.run_id != run_id:
            raise ValueError(
                f"stage checkpoint run id mismatch: expected {run_id}, found {previous.run_id}"
            )
        if previous is not None and previous.status == "running" and replay_policy == "reconcile":
            if reconcile is None or not reconcile(previous):
                raise StageInterruptionRequiresReconciliation(
                    f"stage {stage_id} has an interrupted attempt requiring reconciliation: {previous.attempt_id}"
                )
        started = started_at or self.clock()
        attempt_number = (previous.attempt_number if previous is not None else 0) + 1
        attempt_id = self.attempt_ids()
        resume_extensions = {
            "replay_policy": replay_policy,
            "resumed_from_attempt_id": (
                previous.attempt_id if previous is not None and previous.status == "running" else ""
            ),
            "reconciled_interrupted_attempt": bool(
                previous is not None
                and previous.status == "running"
                and replay_policy == "reconcile"
            ),
        }
        running = StageResult.create(
            run_id=run_id,
            stage_id=stage_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            status="running",
            started_at=started.isoformat(timespec="seconds"),
            completed_at=None,
            input_digest=input_digest,
            extensions=resume_extensions,
        )
        self.store.write(running)
        try:
            outcome = execute()
            if not isinstance(outcome, StageOutcome):
                raise TypeError("stage executor must return StageOutcome")
            completed = StageResult.create(
                run_id=run_id,
                stage_id=stage_id,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                status=outcome.status,
                started_at=running.started_at,
                completed_at=self.clock().isoformat(timespec="seconds"),
                input_digest=input_digest,
                artifacts=outcome.artifacts,
                messages=outcome.messages,
                output=outcome.output,
                extensions={**_thaw(outcome.extensions), **resume_extensions},
            )
        except Exception as exc:
            failed = StageResult.create(
                run_id=run_id,
                stage_id=stage_id,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                status="failed",
                started_at=running.started_at,
                completed_at=self.clock().isoformat(timespec="seconds"),
                input_digest=input_digest,
                error=StageError(code="stage_exception", error_type=type(exc).__name__),
                extensions=resume_extensions,
            )
            try:
                self.store.write(failed)
            except Exception as checkpoint_exc:
                exc.add_note(f"failed stage checkpoint also failed: {type(checkpoint_exc).__name__}")
            raise
        self.store.write(completed)
        return completed
