from __future__ import annotations

import json
import hashlib
import os
import subprocess
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


PidChecker = Callable[[int], bool]
IdentityReader = Callable[[int], str]


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f'$p=Get-CimInstance Win32_Process -Filter "ProcessId = {pid}"; if ($null -eq $p) {{ exit 1 }}',
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_identity(pid: int) -> str:
    """Return a hashed process identity that changes when a PID is reused."""
    if pid <= 0:
        return ""
    if os.name == "nt":
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f'$p=Get-CimInstance Win32_Process -Filter "ProcessId = {pid}"; '
                    "if ($null -eq $p) { exit 1 }; "
                    "Write-Output ($p.CreationDate.ToUniversalTime().ToString('o') + '|' + $p.ExecutablePath)"
                ),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        raw = completed.stdout.strip() if completed.returncode == 0 else ""
    else:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            executable = os.readlink(f"/proc/{pid}/exe")
            raw = f"{stat.split()[21]}|{executable}"
        except (OSError, IndexError):
            raw = ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


class PidFileLock:
    def __init__(
        self,
        path: Path | str,
        *,
        owner: str,
        pid_checker: PidChecker | None = None,
        identity_reader: IdentityReader | None = None,
    ) -> None:
        self.path = Path(path)
        self.owner = owner
        self.pid_checker = pid_checker or pid_is_running
        self.identity_reader = identity_reader or process_identity
        self.nonce = uuid.uuid4().hex

    def __enter__(self) -> "PidFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        for _attempt in range(2):
            try:
                descriptor = os.open(str(self.path), flags)
                break
            except FileExistsError as exc:
                if self._reclaim_stale_lock():
                    continue
                raise RuntimeError(f"{self.owner} already running: {self.path}") from exc
        else:
            raise RuntimeError(f"{self.owner} already running: {self.path}")

        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "pid": os.getpid(),
                    "owner": self.owner,
                    "nonce": self.nonce,
                    "process_identity": self.identity_reader(os.getpid()),
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.flush()
            os.fsync(handle.fileno())
        return self

    def _reclaim_stale_lock(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        try:
            pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
        except (TypeError, ValueError):
            pid = 0
        if pid > 0 and self.pid_checker(pid):
            saved_identity = str(payload.get("process_identity") or "")
            current_identity = self.identity_reader(pid)
            if not saved_identity or not current_identity or saved_identity == current_identity:
                return False
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and payload.get("nonce") == self.nonce:
            self.path.unlink(missing_ok=True)
