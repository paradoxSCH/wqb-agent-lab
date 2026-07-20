from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast


DEFAULT_STDOUT_LIMIT_BYTES = 1024 * 1024
DEFAULT_STDERR_LIMIT_BYTES = 256 * 1024

_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
    }
)


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessTimedOut(Exception):
    def __init__(self, stdout: bytes, stderr: bytes) -> None:
        self.stdout = stdout
        self.stderr = stderr
        super().__init__("CLI process timed out")


class ProcessOutputLimitExceeded(Exception):
    def __init__(
        self,
        *,
        stream: str,
        observed_bytes: int,
        limit_bytes: int,
        stdout: bytes,
        stderr: bytes,
    ) -> None:
        self.stream = stream
        self.observed_bytes = observed_bytes
        self.limit_bytes = limit_bytes
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(f"CLI {stream} exceeded {limit_bytes} bytes")


def build_cli_environment(
    source: Mapping[str, str],
    *,
    credential_env_name: str = "",
    credential_value: str | None = None,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in source.items()
        if key.upper() in _ENVIRONMENT_ALLOWLIST
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    if credential_env_name and credential_value is not None:
        environment[credential_env_name] = credential_value
    return environment


def resolve_cli_executable(
    executable: str, *, cwd: Path, environment: dict[str, str]
) -> str:
    if executable.lower().endswith((".cmd", ".bat")):
        raise ValueError("Windows batch executables are not supported")
    token = Path(executable)
    has_path = token.is_absolute() or any(
        separator and separator in executable for separator in (os.sep, os.altsep)
    )
    if has_path:
        candidate = token if token.is_absolute() else cwd / token
        resolved = candidate.resolve()
        result = str(resolved) if resolved.is_file() else None
    else:
        result = shutil.which(executable, path=environment.get("PATH"))
    if result is None:
        raise FileNotFoundError(executable)
    if Path(result).suffix.lower() in {".cmd", ".bat"}:
        raise ValueError("Windows batch executables are not supported")
    return str(Path(result).resolve())


def execute_bounded_process(
    command: tuple[str, ...],
    *,
    stdin_bytes: bytes | None,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    stdout_limit_bytes: int,
    stderr_limit_bytes: int,
) -> BoundedProcessResult:
    if os.name == "nt":
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=environment,
            shell=False,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED,
        )
    else:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=environment,
            shell=False,
            start_new_session=True,
        )
    windows_job: _WindowsJob | None = None
    readers: list[threading.Thread] = []
    writer: threading.Thread | None = None
    tree_terminated = False
    try:
        windows_job = _create_windows_job(process)
        if os.name == "nt":
            _resume_windows_process(process)
        assert process.stdout is not None
        assert process.stderr is not None

        overflow = threading.Event()
        stdout_buffer = _BoundedBuffer("stdout", stdout_limit_bytes, overflow)
        stderr_buffer = _BoundedBuffer("stderr", stderr_limit_bytes, overflow)
        stdout_done = threading.Event()
        stderr_done = threading.Event()
        readers.append(
            _reader_thread(cast(BinaryIO, process.stdout), stdout_buffer, stdout_done)
        )
        readers.append(
            _reader_thread(cast(BinaryIO, process.stderr), stderr_buffer, stderr_done)
        )
        writer = _writer_thread(cast(BinaryIO | None, process.stdin), stdin_bytes)
        deadline = time.monotonic() + timeout_seconds
        termination_reason: str | None = None
        while True:
            if overflow.is_set():
                termination_reason = "overflow"
                break
            if time.monotonic() >= deadline:
                termination_reason = "timeout"
                break
            if (
                process.poll() is not None
                and stdout_done.is_set()
                and stderr_done.is_set()
            ):
                break
            overflow.wait(0.01)

        if termination_reason is not None:
            _terminate_process_tree(process, environment, windows_job)
            tree_terminated = True
        _wait_for_exit(process)
        for thread in readers:
            thread.join(timeout=1)
        if writer is not None:
            writer.join(timeout=1)

        stdout = stdout_buffer.value()
        stderr = stderr_buffer.value()
        if termination_reason == "timeout":
            raise ProcessTimedOut(stdout, stderr)
        exceeded = stdout_buffer if stdout_buffer.exceeded else stderr_buffer
        if termination_reason == "overflow":
            raise ProcessOutputLimitExceeded(
                stream=exceeded.name,
                observed_bytes=exceeded.observed_bytes,
                limit_bytes=exceeded.limit_bytes,
                stdout=stdout,
                stderr=stderr,
            )
        return BoundedProcessResult(process.returncode or 0, stdout, stderr)
    except BaseException:
        if not tree_terminated:
            _cleanup_process_tree(process, environment, windows_job)
            tree_terminated = True
        raise
    finally:
        if process.poll() is None:
            _cleanup_process_tree(process, environment, windows_job)
        for thread in readers:
            thread.join(timeout=1)
        if writer is not None:
            writer.join(timeout=1)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()
        if windows_job is not None:
            windows_job.close()


class _BoundedBuffer:
    def __init__(self, name: str, limit_bytes: int, overflow: threading.Event) -> None:
        self.name = name
        self.limit_bytes = limit_bytes
        self.observed_bytes = 0
        self.exceeded = False
        self._data = bytearray()
        self._lock = threading.Lock()
        self._overflow = overflow

    def append(self, chunk: bytes) -> bool:
        with self._lock:
            self.observed_bytes += len(chunk)
            remaining = max(0, self.limit_bytes - len(self._data))
            self._data.extend(chunk[:remaining])
            if self.observed_bytes > self.limit_bytes:
                self.exceeded = True
                self._overflow.set()
                return False
            return True

    def value(self) -> bytes:
        with self._lock:
            return bytes(self._data)


def _reader_thread(
    pipe: BinaryIO, buffer: _BoundedBuffer, done: threading.Event
) -> threading.Thread:
    def read() -> None:
        try:
            while chunk := pipe.read(8192):
                if not buffer.append(chunk):
                    break
        finally:
            pipe.close()
            done.set()

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread


def _writer_thread(
    pipe: BinaryIO | None, payload: bytes | None
) -> threading.Thread | None:
    if pipe is None or payload is None:
        return None

    def write() -> None:
        try:
            pipe.write(payload)
            pipe.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            pipe.close()

    thread = threading.Thread(target=write, daemon=True)
    thread.start()
    return thread


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    environment: dict[str, str],
    windows_job: _WindowsJob | None,
) -> None:
    if os.name == "nt":
        if windows_job is not None:
            windows_job.terminate()
        else:
            system_root = environment.get("SystemRoot") or environment.get("SYSTEMROOT")
            taskkill = (
                Path(system_root) / "System32" / "taskkill.exe"
                if system_root
                else Path("taskkill.exe")
            )
            try:
                killer = subprocess.Popen(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=environment,
                    shell=False,
                )
                killer.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if process.poll() is None:
        process.kill()


def _wait_for_exit(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _cleanup_process_tree(
    process: subprocess.Popen[bytes],
    environment: dict[str, str],
    windows_job: _WindowsJob | None,
) -> None:
    try:
        _terminate_process_tree(process, environment, windows_job)
    except Exception:
        if process.poll() is None:
            process.kill()
    try:
        _wait_for_exit(process)
    except (OSError, subprocess.SubprocessError):
        if process.poll() is None:
            process.kill()


class _WindowsJob:
    def __init__(self, handle: int) -> None:
        self._handle = handle

    def terminate(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject(wintypes.HANDLE(self._handle), 1)

    def close(self) -> None:
        import ctypes
        from ctypes import wintypes

        if self._handle:
            kernel32 = ctypes.windll.kernel32
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = 0


def _create_windows_job(process: subprocess.Popen[bytes]) -> _WindowsJob | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None
    process_handle = wintypes.HANDLE(int(getattr(process, "_handle")))
    if not kernel32.AssignProcessToJobObject(handle, process_handle):
        kernel32.CloseHandle(handle)
        return None
    return _WindowsJob(int(handle))


_CREATE_SUSPENDED = 0x00000004


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.windll.ntdll
    ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    ntdll.NtResumeProcess.restype = wintypes.LONG
    status = ntdll.NtResumeProcess(
        wintypes.HANDLE(int(getattr(process, "_handle")))
    )
    if status != 0:
        process.kill()
        process.wait(timeout=5)
        raise OSError(f"Unable to resume suspended CLI process: NTSTATUS {status}")
