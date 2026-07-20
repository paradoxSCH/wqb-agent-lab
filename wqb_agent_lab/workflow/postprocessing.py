from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from wqb_agent_lab.evaluation.output.evaluator import write_run_output_evaluation

from .artifacts import read_json, relative_path, write_json
from .stages import StageCheckpointStore, StageOutcome, StageRunner


class PostprocessingWorkflow(Protocol):
    root: Path
    run_dir: Path
    run_tag: str
    config: dict[str, Any]
    dry_run: bool
    stage_checkpoint_store: StageCheckpointStore

    def _local_stage_input_digest(
        self, payload: dict[str, Any], paths: list[Path]
    ) -> str: ...
    def run_memory_stage(self, *, now: datetime | None = None) -> Path | None: ...
    def run_evaluation_stage(
        self, *, now: datetime | None = None
    ) -> tuple[Path, Path]: ...


class WorkflowPostprocessor:
    """Run replay-safe local memory and output-evaluation stages."""

    def __init__(self, workflow: PostprocessingWorkflow) -> None:
        self.workflow = workflow

    def run_closed_loop(
        self,
        state: dict[str, Any],
        iteration_state_path: Path,
        *,
        now: datetime | None = None,
    ) -> None:
        workflow = self.workflow
        now = now or datetime.now()
        memory_sync_report = workflow.run_memory_stage(now=now)
        if memory_sync_report is not None:
            state["artifacts"]["memory_sync_report"] = relative_path(
                memory_sync_report,
                workflow.root,
            )
            state["artifacts"]["memory_sync_state"] = relative_path(
                workflow.stage_checkpoint_store.path_for("memory"),
                workflow.root,
            )
        output_report_path, output_summary_path = workflow.run_evaluation_stage(now=now)
        state["artifacts"]["output_evaluation_report"] = relative_path(
            output_report_path, workflow.root
        )
        state["artifacts"]["output_evaluation_summary"] = relative_path(
            output_summary_path, workflow.root
        )
        write_json(iteration_state_path, state)

    def memory_input_paths(self) -> list[Path]:
        workflow = self.workflow
        names = (
            "daily_budget_ledger.json",
            "direct_submit.json",
            "submit_ready.json",
            "submission_backlog.json",
            "optimize_next.json",
            "low_value_avoid.json",
            "alpha_skeleton_blocklist.json",
            "family_efficiency.json",
            "iteration_state.json",
            "scan_results_snapshot.json",
            "self_corr_repair_effect_summary.json",
        )
        paths = [workflow.run_dir / name for name in names]
        paths.extend(workflow.run_dir.glob("*_results.json"))
        paths.append(
            workflow.root
            / ".local"
            / "data"
            / "behavioral_proxy"
            / "behavioral_proxy_map.json"
        )
        return [path for path in paths if path.is_file()]

    def run_memory(self, *, now: datetime | None = None) -> Path | None:
        workflow = self.workflow
        config = workflow.config.get("post_stage_memory_sync") or {}
        if not isinstance(config, dict) or not config.get("enabled") or workflow.dry_run:
            return None
        from wqb_agent_lab.memory.sync import sync_run_memory

        now = now or datetime.now()
        db_path = workflow.root / str(
            config.get("db_path") or ".local/data/memory/alpha_memory.db"
        )
        report_path = workflow.run_dir / "memory_sync_report.json"
        result: Any = None

        def execute() -> StageOutcome:
            nonlocal result
            result = sync_run_memory(workflow.root, workflow.run_dir, db_path=db_path)
            return StageOutcome.create(
                artifacts=(relative_path(report_path, workflow.root),),
                output={
                    "nodes_written": int(result.nodes_written),
                    "edges_written": int(result.edges_written),
                    "events_recorded": int(result.events_recorded),
                },
                extensions={
                    "remote_side_effects": False,
                    "sqlite_upserts_are_idempotent": True,
                    "evaluation_waits_for_completion": True,
                },
            )

        StageRunner(workflow.stage_checkpoint_store).run(
            run_id=workflow.run_tag,
            stage_id="memory",
            input_digest=workflow._local_stage_input_digest(
                {"db_path": relative_path(db_path, workflow.root)},
                self.memory_input_paths(),
            ),
            execute=execute,
            replay_policy="safe",
            started_at=now,
        )
        if result is None or not report_path.is_file():
            raise RuntimeError("memory stage completed without a report")
        return report_path

    def evaluation_input_paths(self) -> list[Path]:
        workflow = self.workflow
        names = (
            "candidate_hypothesis_queue.json",
            "preflight_evaluation_report.json",
            "scan_results_snapshot.json",
            "memory_sync_report.json",
            "policy_feedback_shadow_evaluation.json",
            "triage_summary.md",
            "diagnosis_policy_evaluation.md",
            "wqb-agent-latest-workflow-uml.html",
        )
        return [
            workflow.run_dir / name
            for name in names
            if (workflow.run_dir / name).is_file()
        ]

    def run_evaluation(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[Path, Path]:
        workflow = self.workflow
        now = now or datetime.now()
        report_path = workflow.run_dir / "output_evaluation_report.json"
        summary_path = workflow.run_dir / "output_evaluation_summary.md"

        def execute() -> StageOutcome:
            written_report, written_summary = write_run_output_evaluation(
                workflow.run_dir,
                now=now,
            )
            payload = read_json(written_report, {})
            return StageOutcome.create(
                artifacts=(
                    relative_path(written_report, workflow.root),
                    relative_path(written_summary, workflow.root),
                ),
                output={
                    "record_count": int(payload.get("record_count") or 0),
                    "status_counts": payload.get("status_counts") or {},
                },
                extensions={
                    "remote_side_effects": False,
                    "consumes_completed_memory_stage": (
                        workflow.run_dir / "memory_sync_report.json"
                    ).is_file(),
                    "policy_actions_are_observations": True,
                },
            )

        if workflow.dry_run:
            return report_path, summary_path
        StageRunner(workflow.stage_checkpoint_store).run(
            run_id=workflow.run_tag,
            stage_id="evaluation",
            input_digest=workflow._local_stage_input_digest(
                {"run_tag": workflow.run_tag},
                self.evaluation_input_paths(),
            ),
            execute=execute,
            replay_policy="safe",
            started_at=now,
        )
        if not report_path.is_file() or not summary_path.is_file():
            raise RuntimeError("evaluation stage completed without artifacts")
        return report_path, summary_path
