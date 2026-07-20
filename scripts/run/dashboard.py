"""Serve the read-only daily workflow dashboard."""

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wqb_agent_lab.runtime.atomic_json import atomic_write_json
from wqb_agent_lab.workflow.dashboard import (
    EVALUATIONS_ROOT,
    RUNS_ROOT,
    build_dashboard_model,
    collect_evaluation_reports,
    collect_run_snapshots,
)
from wqb_agent_lab.research.policy import ResearchPolicyError, load_research_policy, policy_digest


class DashboardHandler(BaseHTTPRequestHandler):
    workspace_root = Path(".")
    runs_root = RUNS_ROOT
    evaluations_root = EVALUATIONS_ROOT
    ui_root = Path("packages/wqb-agent-ui/dist")
    policy_path = Path(".local/research/workflows/production.json")

    def _write_response(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "public, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/runs":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["20"])[0])
            runs = collect_run_snapshots(self.workspace_root / self.runs_root)
            visible_runs = runs[:limit]
            evaluation_reports = collect_evaluation_reports(self.workspace_root / self.evaluations_root)
            payload = {
                "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "model": build_dashboard_model(visible_runs, evaluation_reports=evaluation_reports),
                "runs": visible_runs,
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._write_response(200, body, "application/json; charset=utf-8")
            return
        if parsed.path == "/api/policy":
            path = self.workspace_root / self.policy_path
            if not path.exists():
                self._json_response(404, {"status": "missing", "path": self.policy_path.as_posix()})
                return
            config = json.loads(path.read_text(encoding="utf-8-sig"))
            policy = load_research_policy(config)
            self._json_response(200, {"status": "ok", "research_policy": policy.to_dict(), "digest": policy_digest(policy)})
            return
        if parsed.path == "/favicon.ico":
            self._write_response(204, b"", "image/x-icon")
            return
        self._serve_ui(parsed.path)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/policy":
            self._json_response(404, {"status": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 1_000_000:
                raise ValueError("request body must be between 1 byte and 1 MB")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            raw_policy = payload.get("research_policy") if isinstance(payload, dict) else None
            policy = load_research_policy({"research_policy": raw_policy})
            path = self.workspace_root / self.policy_path
            config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
            config["research_policy"] = policy.to_dict()
            atomic_write_json(path, config)
            self._json_response(200, {"status": "saved", "research_policy": policy.to_dict(), "digest": policy_digest(policy)})
        except (ValueError, json.JSONDecodeError, ResearchPolicyError) as exc:
            self._json_response(400, {"status": "invalid_policy", "error": str(exc)})

    def _json_response(self, status: int, payload: object) -> None:
        self._write_response(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _serve_ui(self, request_path: str) -> None:
        root = (self.workspace_root / self.ui_root).resolve()
        relative = "index.html" if request_path == "/" else request_path.lstrip("/")
        candidate = (root / relative).resolve()
        if root not in candidate.parents and candidate != root:
            self._write_response(404, b"not found", "text/plain; charset=utf-8")
            return
        if not candidate.is_file() and "." not in Path(relative).name:
            candidate = root / "index.html"
        if not candidate.is_file():
            self._write_response(
                503,
                "React UI is not built. Run npm run build --prefix packages/wqb-agent-ui.".encode("utf-8"),
                "text/plain; charset=utf-8",
            )
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix in {".html", ".js", ".css"}:
            content_type += "; charset=utf-8"
        self._write_response(200, candidate.read_bytes(), content_type)

    def log_message(self, format: str, *args) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动每日工作流本地进度看板。")
    parser.add_argument("--workspace-root", default=".", help="包含 .local/data/runs/continuous-alpha 的工作区根目录。")
    parser.add_argument("--host", default="127.0.0.1", help="绑定的监听地址。")
    parser.add_argument("--port", type=int, default=8765, help="绑定的端口。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    DashboardHandler.workspace_root = Path(args.workspace_root).resolve()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"看板已启动: http://{args.host}:{args.port}")
    print(f"正在读取 run 目录: {(DashboardHandler.workspace_root / DashboardHandler.runs_root).as_posix()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
