from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wqb_agent_lab.runtime.process_lock import pid_is_running


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        encoding="utf-8",
    )
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            json.dump(payload, temporary, indent=2, ensure_ascii=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def locked_atomic_json_merge(
    path: Path,
    updates: Mapping[str, Any],
    *,
    delete_keys: tuple[str, ...] = (),
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except (FileExistsError, PermissionError) as exc:
            if time.monotonic() >= deadline:
                if isinstance(exc, FileExistsError) and _reclaim_stale_merge_lock(lock_path):
                    deadline = time.monotonic() + timeout_seconds
                    continue
                if isinstance(exc, PermissionError):
                    raise
                raise TimeoutError(f"Timed out acquiring JSON lock: {lock_path}")
            time.sleep(0.01)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
    except Exception:
        os.close(descriptor)
        descriptor = None
        lock_path.unlink(missing_ok=True)
        raise
    try:
        current: dict[str, Any] = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(loaded, dict):
                raise ValueError(f"JSON merge target must contain an object: {path}")
            current = loaded
        current.update(dict(updates))
        for key in delete_keys:
            current.pop(key, None)
        atomic_write_json(path, current)
        return current
    finally:
        if descriptor is not None:
            os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _reclaim_stale_merge_lock(lock_path: Path) -> bool:
    try:
        pid = int(lock_path.read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        pid = 0
    if pid <= 0 or pid == os.getpid():
        return False
    if pid > 0 and pid_is_running(pid):
        return False
    try:
        lock_path.unlink()
        return True
    except FileNotFoundError:
        return True
    except PermissionError:
        return False
