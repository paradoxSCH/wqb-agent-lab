from __future__ import annotations

import json
import tempfile
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests

from scripts.run.dashboard import DashboardHandler
from wqb_agent_lab.workflow.dashboard import (
    build_dashboard_model,
    build_run_snapshot,
    collect_evaluation_reports,
    collect_run_snapshots,
)


def _policy() -> dict[str, object]:
    return {
        "version": 1,
        "budget": {
            "daily_simulation_limit": 10,
            "exploration_share_limit": 0.2,
            "exploration_stages": ["probe"],
            "stage_allocations": {"probe": 2, "scale": 8},
        },
        "behavioral_boundaries": {
            "block_unclassified_candidates": True,
            "require_kill_conditions": True,
            "forbid_pure_price_volume": True,
            "mechanisms": [
                {
                    "mechanism_id": "anchoring",
                    "enabled": True,
                    "allowed_proxy_fields": ["fundamental_*"],
                    "kill_conditions": ["SELF_CORRELATION"],
                }
            ],
        },
    }


def test_build_run_snapshot_and_model_expose_budget_memory_and_evaluation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run-1"
        run_dir.mkdir()
        (run_dir / "daily_budget_ledger.json").write_text(
            json.dumps(
                {
                    "daily_run_tag": "run-1",
                    "daily_budget": 10,
                    "spent_simulations": 4,
                    "remaining_simulations_after_commitments": 6,
                    "stage_order": ["probe", "scale"],
                    "stage_budgets": {"probe": 2, "scale": 8},
                    "stage_spend": {"probe": 2, "scale": 2},
                    "current_stage": "scale_partial",
                }
            ),
            encoding="utf-8",
        )
        snapshot = build_run_snapshot(run_dir, now=datetime.now())
        model = build_dashboard_model([snapshot], evaluation_reports=[{"run_tag": "eval-1", "verdict": "keep"}])
        assert snapshot["inferred_spent_simulations"] >= 4
        assert model["summary"]["total_budget"] == 10
        assert [layer["id"] for layer in model["memory_layers"]] == ["short_term", "long_term", "knowledge_graph"]
        assert model["agent_evaluation"]["summary"]["report_count"] == 1


def test_collectors_ignore_unrelated_directories_and_load_evaluations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "ignored").mkdir()
        run_dir = root / "runs" / "valid"
        run_dir.mkdir(parents=True)
        (run_dir / "daily_budget_ledger.json").write_text("{}", encoding="utf-8")
        evaluation_dir = root / "evaluations" / "eval-1"
        evaluation_dir.mkdir(parents=True)
        (evaluation_dir / "ablation_report.json").write_text(
            json.dumps({"verdict": "keep", "fairness": {"comparison_type": "paired"}}),
            encoding="utf-8",
        )
        assert len(collect_run_snapshots(root / "runs")) == 1
        assert collect_evaluation_reports(root / "evaluations")[0]["comparison_type"] == "paired"


def test_dashboard_serves_react_assets_and_validates_policy_updates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ui_root = root / "ui"
        ui_root.mkdir()
        (ui_root / "index.html").write_text('<html lang="zh-CN"><div id="root"></div></html>', encoding="utf-8")
        policy_path = root / "production.json"
        policy_path.write_text(json.dumps({"research_policy": _policy()}), encoding="utf-8")

        class Handler(DashboardHandler):
            workspace_root = root
            runs_root = Path("runs")
            evaluations_root = Path("evaluations")
            ui_root = Path("ui")
            policy_path = Path("production.json")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            assert requests.get(base, timeout=2).status_code == 200
            assert requests.get(f"{base}/api/runs", timeout=2).json()["runs"] == []
            assert requests.get(f"{base}/api/policy", timeout=2).json()["research_policy"]["budget"]["daily_simulation_limit"] == 10
            invalid = _policy()
            invalid["budget"]["daily_simulation_limit"] = 11  # type: ignore[index]
            assert requests.put(f"{base}/api/policy", json={"research_policy": invalid}, timeout=2).status_code == 400
            saved = requests.put(f"{base}/api/policy", json={"research_policy": _policy()}, timeout=2)
            assert saved.status_code == 200
            assert saved.json()["status"] == "saved"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_react_source_owns_chinese_policy_memory_and_behavior_views() -> None:
    source = (Path(__file__).parents[1] / "packages/wqb-agent-ui/src/App.tsx").read_text(encoding="utf-8")
    for text in ("研究边界", "行为经济学逻辑库", "分层记忆", "知识图谱", "保存研究边界"):
        assert text in source
    assert 'fetch("/api/policy"' in source
    assert 'method: "PUT"' in source
