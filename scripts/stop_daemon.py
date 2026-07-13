from __future__ import annotations

import os
import signal
from pathlib import Path


def kill_pidfile(path: Path, name: str) -> None:
    if not path.exists():
        print(f"{name}: no PID file")
        return
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
        print(f"{name}: sent SIGTERM to PID {pid}")
    except ProcessLookupError:
        print(f"{name}: PID {pid} not found (already dead)")
    except (ValueError, OSError) as exc:
        print(f"{name}: failed to kill: {exc}")


def main() -> int:
    root = Path(__file__).parent.parent.resolve()
    kill_pidfile(root / ".workflow.pid", "workflow daemon")
    kill_pidfile(root / ".dashboard.pid", "dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
