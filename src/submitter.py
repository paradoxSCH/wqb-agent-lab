"""BRAIN Alpha 提交管理：资格校验、队列、速率控制、结果追踪与进展报告。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .evaluator import AlphaMetrics
from .session import BrainSession
from .side_effect_governance import require_side_effect_capability
from .wqb_agent_lab.platform.third_party import wqb_sdk as wqb


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _unwrap_session(session: wqb.WQBSession | BrainSession) -> wqb.WQBSession:
    """兼容原始 ``WQBSession`` 和 ``BrainSession`` 封装。"""
    return session.session if isinstance(session, BrainSession) else session


# ---------------------------------------------------------------------------
# 提交配置
# ---------------------------------------------------------------------------


@dataclass
class SubmissionPolicy:
    """提交速率与限制策略。"""

    max_per_day: int = 50
    interval_seconds: float = 5.0
    dry_run: bool = True
    min_sharpe: float = 1.25
    min_fitness: float = 1.0
    max_turnover: float = 0.7


# ---------------------------------------------------------------------------
# 提交记录
# ---------------------------------------------------------------------------


@dataclass
class SubmissionRecord:
    """单次提交的完整记录。"""

    alpha_id: str
    expression: str
    sharpe: float = 0.0
    fitness: float = 0.0
    turnover: float = 0.0
    composite_score: float = 0.0
    check_result: dict[str, Any] = field(default_factory=dict)
    submit_result: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | checked | submitted | failed | skipped
    timestamp: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha_id": self.alpha_id,
            "expression": self.expression,
            "sharpe": self.sharpe,
            "fitness": self.fitness,
            "turnover": self.turnover,
            "composite_score": self.composite_score,
            "status": self.status,
            "timestamp": self.timestamp,
            "error": self.error,
            "check_result": self.check_result,
            "submit_result": self.submit_result,
        }


# ---------------------------------------------------------------------------
# 基础 API 调用
# ---------------------------------------------------------------------------


def check_submission(
    session: wqb.WQBSession | BrainSession,
    alpha_id: str,
) -> dict[str, Any]:
    """检查 Alpha 是否满足提交前置条件。"""
    raw_session = _unwrap_session(session)
    try:
        resp = asyncio.run(raw_session.check(alpha_id))
        if resp is None:
            return {
                "alpha_id": alpha_id,
                "eligible": False,
                "error": "BRAIN 未返回提交流程检查结果。",
            }
        return {
            "alpha_id": alpha_id,
            "status_code": resp.status_code,
            "eligible": resp.ok,
            "data": resp.json() if resp.ok else None,
            "error": resp.text if not resp.ok else None,
        }
    except Exception as e:
        logger.error("检查 Alpha %s 是否可提交时失败：%s", alpha_id, e)
        return {
            "alpha_id": alpha_id,
            "eligible": False,
            "error": str(e),
        }


def submit_alpha(
    session: wqb.WQBSession | BrainSession,
    alpha_id: str,
) -> dict[str, Any]:
    """向 BRAIN 提交 Alpha。"""
    require_side_effect_capability("submission")
    raw_session = _unwrap_session(session)
    try:
        resp = asyncio.run(raw_session.submit(alpha_id))
        if resp is None:
            return {
                "alpha_id": alpha_id,
                "submitted": False,
                "error": "BRAIN 未返回提交结果。",
            }
        return {
            "alpha_id": alpha_id,
            "status_code": resp.status_code,
            "submitted": resp.ok,
            "data": resp.json() if resp.ok else None,
            "error": resp.text if not resp.ok else None,
        }
    except Exception as e:
        logger.error("提交 Alpha %s 失败：%s", alpha_id, e)
        return {
            "alpha_id": alpha_id,
            "submitted": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# 资格校验
# ---------------------------------------------------------------------------


def check_eligibility(
    candidate: AlphaMetrics,
    policy: SubmissionPolicy | None = None,
) -> tuple[bool, str]:
    """检查候选是否满足提交资格。返回 (合格, 原因)。"""
    policy = policy or SubmissionPolicy()

    if not candidate.alpha_id:
        return False, "缺少 alpha_id"
    if candidate.sharpe < policy.min_sharpe:
        return False, f"Sharpe {candidate.sharpe:.4f} < {policy.min_sharpe}"
    if candidate.fitness < policy.min_fitness:
        return False, f"Fitness {candidate.fitness:.4f} < {policy.min_fitness}"
    if candidate.turnover > policy.max_turnover:
        return False, f"Turnover {candidate.turnover:.4f} > {policy.max_turnover}"
    return True, "满足所有阈值"


# ---------------------------------------------------------------------------
# 提交队列
# ---------------------------------------------------------------------------


class SubmissionQueue:
    """带速率控制的提交队列管理器。"""

    def __init__(
        self,
        session: wqb.WQBSession | BrainSession,
        policy: SubmissionPolicy | None = None,
        tracker: "SubmissionTracker | None" = None,
    ) -> None:
        self.session = session
        self.policy = policy or SubmissionPolicy()
        self.tracker = tracker or SubmissionTracker()
        self._submitted_today: int = 0
        self._last_submit_time: float = 0.0

    def _rate_limit(self) -> None:
        """确保两次提交之间满足最小间隔。"""
        if self.policy.interval_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_submit_time
        remaining = self.policy.interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _can_submit_more(self) -> bool:
        return self._submitted_today < self.policy.max_per_day

    def enqueue(self, candidates: list[AlphaMetrics]) -> list[SubmissionRecord]:
        """对候选列表执行「校验 → 检查 → 提交」流程。"""
        records: list[SubmissionRecord] = []

        for candidate in candidates:
            record = SubmissionRecord(
                alpha_id=candidate.alpha_id,
                expression=candidate.expression,
                sharpe=candidate.sharpe,
                fitness=candidate.fitness,
                turnover=candidate.turnover,
                composite_score=candidate.composite_score,
            )

            eligible, reason = check_eligibility(candidate, self.policy)
            if not eligible:
                record.status = "skipped"
                record.error = reason
                record.timestamp = datetime.now().isoformat()
                logger.info("跳过 %s：%s", candidate.alpha_id or candidate.expression[:40], reason)
                records.append(record)
                self.tracker.add(record)
                continue

            if not self._can_submit_more():
                record.status = "skipped"
                record.error = f"已达当日提交上限 {self.policy.max_per_day}"
                record.timestamp = datetime.now().isoformat()
                logger.warning("已达当日提交上限，跳过 %s", candidate.alpha_id)
                records.append(record)
                self.tracker.add(record)
                continue

            check_result = check_submission(self.session, candidate.alpha_id)
            record.check_result = check_result
            if not check_result.get("eligible"):
                record.status = "failed"
                record.error = check_result.get("error", "平台资格检查未通过")
                record.timestamp = datetime.now().isoformat()
                logger.info("Alpha %s 未通过平台资格检查：%s", candidate.alpha_id, record.error)
                records.append(record)
                self.tracker.add(record)
                continue

            record.status = "checked"

            if self.policy.dry_run:
                record.status = "checked"
                record.timestamp = datetime.now().isoformat()
                logger.info("演练模式：Alpha %s 通过检查，不执行提交", candidate.alpha_id)
                records.append(record)
                self.tracker.add(record)
                continue

            self._rate_limit()
            submit_result = submit_alpha(self.session, candidate.alpha_id)
            record.submit_result = submit_result
            record.timestamp = datetime.now().isoformat()

            if submit_result.get("submitted"):
                record.status = "submitted"
                self._submitted_today += 1
                self._last_submit_time = time.monotonic()
                logger.info("Alpha %s 提交成功", candidate.alpha_id)
            else:
                record.status = "failed"
                record.error = submit_result.get("error", "提交失败")
                logger.error("Alpha %s 提交失败：%s", candidate.alpha_id, record.error)

            records.append(record)
            self.tracker.add(record)

        return records


# ---------------------------------------------------------------------------
# 提交结果追踪
# ---------------------------------------------------------------------------


class SubmissionTracker:
    """提交历史追踪与持久化。"""

    def __init__(self, history_path: str | Path = ".local/data/submission_history.json") -> None:
        self._path = Path(history_path)
        self._records: list[SubmissionRecord] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for entry in data.get("records", []):
                    self._records.append(
                        SubmissionRecord(
                            alpha_id=entry.get("alpha_id", ""),
                            expression=entry.get("expression", ""),
                            sharpe=entry.get("sharpe", 0),
                            fitness=entry.get("fitness", 0),
                            turnover=entry.get("turnover", 0),
                            composite_score=entry.get("composite_score", 0),
                            status=entry.get("status", "unknown"),
                            timestamp=entry.get("timestamp", ""),
                            error=entry.get("error", ""),
                            check_result=entry.get("check_result", {}),
                            submit_result=entry.get("submit_result", {}),
                        )
                    )
            except (json.JSONDecodeError, KeyError):
                logger.warning("提交历史文件格式异常，已忽略")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "updated_at": datetime.now().isoformat(),
            "total": len(self._records),
            "records": [r.to_dict() for r in self._records],
        }
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def add(self, record: SubmissionRecord) -> None:
        """追加一条提交记录并持久化。"""
        self._records.append(record)
        self._save()

    @property
    def records(self) -> list[SubmissionRecord]:
        return list(self._records)

    @property
    def total(self) -> int:
        return len(self._records)

    def count_by_status(self) -> dict[str, int]:
        """按状态统计提交记录数量。"""
        counts: dict[str, int] = {}
        for r in self._records:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# 进展报告
# ---------------------------------------------------------------------------


def generate_progress_report(tracker: SubmissionTracker) -> dict[str, Any]:
    """生成积分与阶段进展的汇总报告。"""
    counts = tracker.count_by_status()
    submitted = counts.get("submitted", 0)
    checked = counts.get("checked", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)

    sharpe_values = [r.sharpe for r in tracker.records if r.status == "submitted"]
    avg_sharpe = sum(sharpe_values) / len(sharpe_values) if sharpe_values else 0.0

    best_record = max(
        (r for r in tracker.records if r.status == "submitted"),
        key=lambda r: r.composite_score,
        default=None,
    )

    return {
        "summary": {
            "total_records": tracker.total,
            "submitted": submitted,
            "checked_only": checked,
            "failed": failed,
            "skipped": skipped,
        },
        "metrics": {
            "avg_sharpe_submitted": round(avg_sharpe, 4),
            "best_composite_score": round(best_record.composite_score, 4) if best_record else 0.0,
            "best_expression": best_record.expression if best_record else "",
        },
        "stage_estimate": _estimate_stage(submitted),
    }


def _estimate_stage(submitted_count: int) -> dict[str, Any]:
    """根据已提交 Alpha 数量估算当前阶段进展。"""
    stages = [
        (10, "Bronze", "已入门，继续积累提交量"),
        (50, "Silver", "初具规模，目标多样化表达式"),
        (200, "Gold", "进阶阶段，关注区域与数据集覆盖"),
        (500, "Platinum", "高级阶段，优化组合与复合策略"),
        (float("inf"), "Diamond", "顶级，持续创新与探索"),
    ]
    for threshold, name, tip in stages:
        if submitted_count < threshold:
            return {
                "current_stage": name,
                "submitted": submitted_count,
                "next_threshold": threshold if threshold != float("inf") else None,
                "tip": tip,
            }
    return {"current_stage": "Diamond", "submitted": submitted_count, "next_threshold": None, "tip": "持续探索"}


def format_progress_text(report: dict[str, Any]) -> str:
    """将进展报告格式化为可读文本。"""
    s = report["summary"]
    m = report["metrics"]
    st = report["stage_estimate"]

    lines = [
        "═══ Alpha 提交进展报告 ═══",
        "",
        f"  总记录数:   {s['total_records']}",
        f"  已提交:     {s['submitted']}",
        f"  仅通过检查: {s['checked_only']}",
        f"  失败:       {s['failed']}",
        f"  跳过:       {s['skipped']}",
        "",
        f"  已提交平均 Sharpe: {m['avg_sharpe_submitted']:.4f}",
        f"  最佳综合评分:      {m['best_composite_score']:.4f}",
    ]
    if m["best_expression"]:
        lines.append(f"  最佳表达式:        {m['best_expression']}")
    lines.extend([
        "",
        f"  当前阶段:  {st['current_stage']}",
        f"  已提交数:  {st['submitted']}",
    ])
    if st["next_threshold"]:
        lines.append(f"  下一阶段:  提交 {st['next_threshold']} 个 Alpha")
    lines.append(f"  建议:      {st['tip']}")
    lines.append("")
    lines.append("═" * 30)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 兼容旧接口
# ---------------------------------------------------------------------------


def batch_check_and_submit(
    session: wqb.WQBSession | BrainSession,
    candidates: list[AlphaMetrics],
    dry_run: bool = True,
) -> list[dict[str, Any]]:
    """批量检查候选 Alpha，并在需要时提交（兼容 Phase 2 接口）。"""
    policy = SubmissionPolicy(dry_run=dry_run)
    queue = SubmissionQueue(session, policy=policy)
    records = queue.enqueue(candidates)
    return [r.to_dict() for r in records]
