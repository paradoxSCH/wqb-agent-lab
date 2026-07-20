from __future__ import annotations

import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module_name",
    [
        "scripts.evaluation.agent_ablation",
        "scripts.evaluation.diagnosis_policies",
        "scripts.evaluation.output_artifacts",
        "scripts.evaluation.policy_effectiveness",
        "scripts.memory.eval",
        "scripts.memory.export",
        "scripts.memory.ingest",
        "scripts.memory.integrity_check",
        "scripts.memory.query",
        "scripts.memory.rebuild_indexes",
        "scripts.memory.sync",
        "scripts.registry.fetch_submitted",
        "scripts.research.build_behavioral_candidate_generation",
        "scripts.research.build_behavioral_proxy_map",
        "scripts.research.build_self_corr_repair_scan",
        "scripts.research.hypothesis_ledger",
        "scripts.run.dashboard",
        "scripts.run.scan",
        "scripts.run.workflow",
        "scripts.run.daemon",
        "scripts.run.stop_daemon",
        "scripts.workers.submission",
        "scripts.workers.evaluation",
        "scripts.workers.memory",
        "scripts.workers.registry",
    ],
)
def test_grouped_command_exposes_main(module_name: str) -> None:
    module = importlib.import_module(module_name)
    assert callable(module.main)


@pytest.mark.parametrize(
    "relative_path",
    [
        "scripts/audit_daily_submit_ready.py",
        "scripts/build_behavioral_candidate_generation.py",
        "scripts/build_behavioral_proxy_map.py",
        "scripts/build_self_corr_repair_scan.py",
        "scripts/daily_workflow_dashboard.py",
        "scripts/evaluate_agent_ablation.py",
        "scripts/evaluate_diagnosis_policies.py",
        "scripts/evaluate_output_artifacts.py",
        "scripts/evaluate_policy_effectiveness.py",
        "scripts/evaluation_worker.py",
        "scripts/fetch_submitted.py",
        "scripts/hypothesis_ledger.py",
        "scripts/json_output.py",
        "scripts/launch_daemon.py",
        "scripts/memory_benchmark.py",
        "scripts/memory_eval.py",
        "scripts/memory_export.py",
        "scripts/memory_ingest.py",
        "scripts/memory_integrity_check.py",
        "scripts/memory_query.py",
        "scripts/memory_rebuild_indexes.py",
        "scripts/memory_worker.py",
        "scripts/pnl_corr.py",
        "scripts/registry_worker.py",
        "scripts/scan.py",
        "scripts/stop_daemon.py",
        "scripts/sync_agent_memory.py",
    ],
)
def test_grouped_implementations_are_not_duplicated_at_scripts_root(relative_path: str) -> None:
    assert not (ROOT / relative_path).is_file()
