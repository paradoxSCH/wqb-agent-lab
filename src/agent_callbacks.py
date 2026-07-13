from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CALLBACK_OUTBOX = Path(".local/data/callbacks/wqb-agent")


@dataclass(frozen=True)
class CallbackEmitResult:
    event_path: Path | None
    webhook_status: str | None = None
    error: str | None = None


def emit_agent_callback(
    root: Path | str,
    event_type: str,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    outbox_dir: Path | str | None = None,
    webhook_url: str | None = None,
) -> CallbackEmitResult:
    """Emit an event-driven callback without blocking the mining workflow."""
    if _callbacks_disabled():
        return CallbackEmitResult(event_path=None, webhook_status="disabled")

    workspace = Path(root)
    now = now or datetime.now()
    event = {
        "event_type": event_type,
        "generated_at": now.isoformat(timespec="seconds"),
        "workspace": str(workspace),
        "payload": payload,
    }
    event_path: Path | None = None
    error: str | None = None

    target_dir = _resolve_outbox_dir(workspace, outbox_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        event_path = target_dir / _event_filename(now, event_type, payload)
        event_path.write_text(json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        error = _sanitize_error(str(exc))

    webhook_status = _send_webhook(event, webhook_url)
    return CallbackEmitResult(event_path=event_path, webhook_status=webhook_status, error=error)


def _callbacks_disabled() -> bool:
    return str(os.getenv("WQB_AGENT_CALLBACK_ENABLED", "1")).strip().lower() in {"0", "false", "no", "off"}


def _resolve_outbox_dir(workspace: Path, outbox_dir: Path | str | None) -> Path:
    configured = outbox_dir or os.getenv("WQB_AGENT_CALLBACK_OUTBOX")
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else workspace / path
    return workspace / DEFAULT_CALLBACK_OUTBOX


def _event_filename(now: datetime, event_type: str, payload: dict[str, Any]) -> str:
    safe_type = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in event_type)[:80] or "event"
    run_tag = str(payload.get("run_tag") or payload.get("daily_run_tag") or "run")
    safe_run = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in run_tag)[:80] or "run"
    stamp = now.strftime("%Y%m%dT%H%M%S%f")
    return f"{stamp}_{safe_type}_{safe_run}.json"


def _send_webhook(event: dict[str, Any], webhook_url: str | None) -> str | None:
    url = (webhook_url if webhook_url is not None else os.getenv("WQB_AGENT_CALLBACK_WEBHOOK_URL", "")).strip()
    if not url:
        return None
    timeout = float(os.getenv("WQB_AGENT_CALLBACK_WEBHOOK_TIMEOUT_SECONDS", "10") or "10")
    data = json.dumps(event, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return f"http_{response.status}"
    except (OSError, urllib.error.URLError) as exc:
        return f"webhook_error:{_sanitize_error(str(exc))}"


def _sanitize_error(text: str) -> str:
    webhook_url = os.getenv("WQB_AGENT_CALLBACK_WEBHOOK_URL", "")
    if webhook_url:
        text = text.replace(webhook_url, "<webhook_url>")
    return text[:300]
