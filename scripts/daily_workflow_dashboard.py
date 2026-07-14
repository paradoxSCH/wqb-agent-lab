from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scripts.dashboard_assets import HTML_PAGE as WORKBENCH_HTML_PAGE
from src.daily_workflow_dashboard import EVALUATIONS_ROOT, RUNS_ROOT, build_dashboard_model, collect_evaluation_reports, collect_run_snapshots


HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>每日挖掘进度看板</title>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: rgba(255, 252, 246, 0.82);
      --ink: #182126;
      --muted: #5e6a71;
      --accent: #0f766e;
      --accent-soft: #9bd5ca;
      --warn: #b45309;
      --danger: #b42318;
      --border: rgba(24, 33, 38, 0.10);
      --shadow: 0 24px 60px rgba(24, 33, 38, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.16), transparent 28%),
        radial-gradient(circle at top right, rgba(180,83,9,0.14), transparent 24%),
        linear-gradient(180deg, #fbf6ee 0%, var(--bg) 58%, #efe7da 100%);
      font-family: Georgia, "Times New Roman", serif;
      min-height: 100vh;
    }
    .shell {
      width: min(1200px, calc(100vw - 32px));
      margin: 24px auto 48px;
    }
    .hero, .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .hero {
      padding: 28px;
      display: grid;
      gap: 14px;
    }
    .eyebrow {
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--muted);
      font-size: 12px;
    }
    h1 {
      margin: 0;
      font-size: clamp(32px, 6vw, 58px);
      line-height: 0.94;
      font-weight: 600;
    }
    .hero p, .meta, .subtle {
      margin: 0;
      color: var(--muted);
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
    }
    .stat {
      padding: 14px 16px;
      border-radius: 16px;
      background: rgba(255,255,255,0.56);
      border: 1px solid rgba(24,33,38,0.08);
    }
    .stat strong {
      display: block;
      font-size: 24px;
      color: var(--ink);
    }
    .run-grid {
      margin-top: 18px;
      display: grid;
      gap: 16px;
    }
    .card {
      padding: 20px;
      display: grid;
      gap: 16px;
    }
    .run-head {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
    }
    .run-head h2 {
      margin: 0;
      font-size: 28px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      background: rgba(255,255,255,0.7);
      border: 1px solid rgba(24,33,38,0.08);
    }
    .pill.active, .pill.inferred-active { color: var(--accent); }
    .pill.stalled { color: var(--danger); }
    .pill.complete { color: var(--warn); }
    .progress {
      height: 12px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(24,33,38,0.08);
    }
    .progress > span {
      display: block;
      height: 100%;
      background: linear-gradient(90deg, var(--accent) 0%, #1d4ed8 100%);
      border-radius: inherit;
      transition: width 0.35s ease;
    }
    .stage-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
    }
    .stage {
      padding: 12px;
      border-radius: 16px;
      background: rgba(255,255,255,0.58);
      border: 1px solid rgba(24,33,38,0.07);
      display: grid;
      gap: 8px;
    }
    .stage strong { font-size: 14px; }
    .file-list {
      display: grid;
      gap: 6px;
      font-family: Consolas, "SFMono-Regular", monospace;
      font-size: 12px;
    }
    .file-row {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 8px;
      color: var(--muted);
    }
    @media (max-width: 720px) {
      .shell { width: min(100vw - 20px, 1200px); margin-top: 10px; }
      .hero, .card { border-radius: 18px; }
      .hero { padding: 20px; }
      .card { padding: 16px; }
      h1 { font-size: 34px; }
      .run-head h2 { font-size: 22px; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">WorldQuant 研究监控</div>
      <h1>每日挖掘进度总览</h1>
      <p>只读本地仪表盘。自动刷新，直接从 run ledger 和结果文件推断当前进度，不依赖 workflow 额外写新状态。</p>
      <div class="stats" id="top-stats"></div>
      <p class="subtle" id="refresh-note"></p>
    </section>
    <section class="run-grid" id="runs"></section>
  </main>
  <script>
    const fmt = (value) => value == null ? '--' : value;
    const clamp = (value) => Math.max(0, Math.min(100, Number(value || 0)));
    function topStats(runs) {
      const active = runs.filter(r => r.health === 'active' || r.health === 'inferred-active').length;
      const stalled = runs.filter(r => r.health === 'stalled').length;
      const complete = runs.filter(r => r.health === 'complete').length;
      const latest = runs[0];
      return [
        ['跟踪中的 run', runs.length],
        ['进行中', active],
        ['疑似卡住', stalled],
        ['已完成', complete],
        ['最新 run', latest ? latest.run_tag : '--']
      ];
    }
    function healthLabel(health) {
      const labels = {
        'active': '进行中',
        'inferred-active': '推断进行中',
        'stalled': '疑似卡住',
        'complete': '已完成'
      };
      return labels[health] || health;
    }
    function stageStatusLabel(status) {
      const labels = {
        'pending': '未开始',
        'partial': '部分完成',
        'complete': '已完成'
      };
      return labels[status] || status;
    }
    function renderStage(stage) {
      const etaText = stage.eta_hours != null ? ` · 预计还需 ${stage.eta_hours} 小时` : '';
      return `
        <article class="stage">
          <strong>${stage.stage}</strong>
          <div class="meta">${stage.effective_spend} / ${stage.budget} 次模拟</div>
          <div class="progress"><span style="width:${clamp(stage.percent)}%"></span></div>
          <div class="meta">${stageStatusLabel(stage.status)} · ledger ${stage.recorded_spend} · 推断 ${stage.inferred_spend}${etaText}</div>
        </article>`;
    }
    function renderFiles(files) {
      if (!files.length) {
        return '<div class="file-row"><span>还没有结果文件</span></div>';
      }
      return files.slice(0, 6).map(file => `
        <div class="file-row">
          <span>${file.name}</span>
          <span>${file.rows} 条 · ${fmt(file.updated_at)}</span>
        </div>`).join('');
    }
    function renderRun(run) {
      const percent = run.daily_budget > 0 ? clamp((run.inferred_spent_simulations / run.daily_budget) * 100) : 0;
      const eta = run.eta;
      const etaText = eta ? `预计 ${eta.eta_at} 完成 · 速度 ${eta.speed_per_hour} 次/小时` : '';
      return `
        <article class="card">
          <div class="run-head">
            <div>
              <div class="eyebrow">${fmt(run.date)}</div>
              <h2>${run.run_tag}</h2>
            </div>
            <div class="pill ${run.health}">${healthLabel(run.health)}</div>
          </div>
          <div class="stats">
            <div class="stat"><strong>${run.inferred_spent_simulations}</strong><span class="meta">已消耗 / 推断</span></div>
            <div class="stat"><strong>${run.daily_budget}</strong><span class="meta">当日预算</span></div>
            <div class="stat"><strong>${fmt(run.current_stage)}</strong><span class="meta">当前阶段</span></div>
            <div class="stat"><strong>${fmt(run.latest_activity_age_minutes)}</strong><span class="meta">距最近活动分钟数</span></div>
          </div>
          <div class="progress"><span style="width:${percent}%"></span></div>
          <div class="meta">${etaText ? etaText + ' · ' : ''}ledger 已记账 ${run.spent_simulations} · 最近活动 ${fmt(run.latest_activity_at)} · ${fmt(run.latest_activity_path)}</div>
          <div class="stage-grid">${run.stages.map(renderStage).join('')}</div>
          <div class="file-list">${renderFiles(run.result_files)}</div>
        </article>`;
    }
    async function refresh() {
      const response = await fetch('/api/runs');
      const payload = await response.json();
      const runs = payload.runs || [];
      document.getElementById('top-stats').innerHTML = topStats(runs).map(([label, value]) => `
        <div class="stat"><strong>${value}</strong><span class="meta">${label}</span></div>`).join('');
      document.getElementById('runs').innerHTML = runs.map(renderRun).join('');
      document.getElementById('refresh-note').textContent = `最近刷新时间 ${payload.generated_at}，每 15 秒自动刷新一次。`;
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    workspace_root = Path(".")
    runs_root = RUNS_ROOT
    evaluations_root = EVALUATIONS_ROOT

    def _write_response(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
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
        if parsed.path == "/":
            self._write_response(200, WORKBENCH_HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/favicon.ico":
            self._write_response(204, b"", "image/x-icon")
            return
        self._write_response(404, "未找到页面".encode("utf-8"), "text/plain; charset=utf-8")

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
