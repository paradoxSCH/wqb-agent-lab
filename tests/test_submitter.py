"""submitter 模块单元测试。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.evaluator import AlphaMetrics
from src.submitter import (
    SubmissionPolicy,
    SubmissionQueue,
    SubmissionRecord,
    SubmissionTracker,
    check_eligibility,
    format_progress_text,
    generate_progress_report,
    _estimate_stage,
)


class TestCheckEligibility(unittest.TestCase):
    """资格校验测试。"""

    def test_passes_good_candidate(self):
        m = AlphaMetrics(expression="x", sharpe=1.5, fitness=1.2, turnover=0.5, alpha_id="a1")
        ok, _ = check_eligibility(m)
        self.assertTrue(ok)

    def test_rejects_missing_alpha_id(self):
        m = AlphaMetrics(expression="x", sharpe=2.0, fitness=1.5, turnover=0.3)
        ok, reason = check_eligibility(m)
        self.assertFalse(ok)
        self.assertIn("alpha_id", reason)

    def test_rejects_low_sharpe(self):
        m = AlphaMetrics(expression="x", sharpe=0.5, fitness=1.5, turnover=0.3, alpha_id="a1")
        ok, reason = check_eligibility(m)
        self.assertFalse(ok)
        self.assertIn("Sharpe", reason)

    def test_rejects_high_turnover(self):
        m = AlphaMetrics(expression="x", sharpe=2.0, fitness=1.5, turnover=0.9, alpha_id="a1")
        ok, reason = check_eligibility(m)
        self.assertFalse(ok)
        self.assertIn("Turnover", reason)


class TestSubmissionTracker(unittest.TestCase):
    """提交追踪器测试。"""

    def test_add_and_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            tracker = SubmissionTracker(history_path=path)

            tracker.add(SubmissionRecord(alpha_id="a1", expression="x", status="submitted"))
            tracker.add(SubmissionRecord(alpha_id="a2", expression="y", status="failed"))
            tracker.add(SubmissionRecord(alpha_id="a3", expression="z", status="submitted"))

            self.assertEqual(tracker.total, 3)
            counts = tracker.count_by_status()
            self.assertEqual(counts["submitted"], 2)
            self.assertEqual(counts["failed"], 1)

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.json"
            tracker = SubmissionTracker(history_path=path)
            tracker.add(SubmissionRecord(alpha_id="a1", expression="x", status="submitted"))

            tracker2 = SubmissionTracker(history_path=path)
            self.assertEqual(tracker2.total, 1)
            self.assertEqual(tracker2.records[0].alpha_id, "a1")


class TestSubmissionQueue(unittest.TestCase):
    """提交队列测试。"""

    def test_dry_run_does_not_submit(self):
        mock_session = MagicMock()
        policy = SubmissionPolicy(dry_run=True, interval_seconds=0)

        with tempfile.TemporaryDirectory() as tmp:
            tracker = SubmissionTracker(history_path=Path(tmp) / "h.json")
            queue = SubmissionQueue(mock_session, policy=policy, tracker=tracker)

            mock_check_resp = MagicMock()
            mock_check_resp.status_code = 200
            mock_check_resp.ok = True
            mock_check_resp.json.return_value = {}
            mock_check_resp.text = ""

            with patch("src.submitter.asyncio.run", return_value=mock_check_resp):
                candidates = [
                    AlphaMetrics(expression="a", sharpe=1.5, fitness=1.2, turnover=0.4, alpha_id="a1"),
                ]
                records = queue.enqueue(candidates)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "checked")

    def test_skips_ineligible(self):
        mock_session = MagicMock()
        policy = SubmissionPolicy(dry_run=True, interval_seconds=0)

        with tempfile.TemporaryDirectory() as tmp:
            tracker = SubmissionTracker(history_path=Path(tmp) / "h.json")
            queue = SubmissionQueue(mock_session, policy=policy, tracker=tracker)

            candidates = [
                AlphaMetrics(expression="bad", sharpe=0.5, fitness=0.3, turnover=0.9, alpha_id="b1"),
            ]
            records = queue.enqueue(candidates)

            self.assertEqual(records[0].status, "skipped")

    def test_daily_limit(self):
        mock_session = MagicMock()
        policy = SubmissionPolicy(dry_run=False, max_per_day=0, interval_seconds=0)

        with tempfile.TemporaryDirectory() as tmp:
            tracker = SubmissionTracker(history_path=Path(tmp) / "h.json")
            queue = SubmissionQueue(mock_session, policy=policy, tracker=tracker)

            candidates = [
                AlphaMetrics(expression="a", sharpe=1.5, fitness=1.2, turnover=0.4, alpha_id="a1"),
            ]
            records = queue.enqueue(candidates)

            self.assertEqual(records[0].status, "skipped")
            self.assertIn("上限", records[0].error)


class TestProgressReport(unittest.TestCase):
    """进展报告测试。"""

    def test_report_with_submissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = SubmissionTracker(history_path=Path(tmp) / "h.json")
            tracker.add(SubmissionRecord(
                alpha_id="a1", expression="rank(close)", status="submitted",
                sharpe=1.8, composite_score=0.7,
            ))
            tracker.add(SubmissionRecord(
                alpha_id="a2", expression="rank(open)", status="submitted",
                sharpe=2.0, composite_score=0.8,
            ))
            tracker.add(SubmissionRecord(
                alpha_id="a3", expression="bad", status="failed",
            ))

            report = generate_progress_report(tracker)

            self.assertEqual(report["summary"]["submitted"], 2)
            self.assertEqual(report["summary"]["failed"], 1)
            self.assertAlmostEqual(report["metrics"]["avg_sharpe_submitted"], 1.9, places=2)
            self.assertEqual(report["metrics"]["best_expression"], "rank(open)")

    def test_empty_tracker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = SubmissionTracker(history_path=Path(tmp) / "h.json")
            report = generate_progress_report(tracker)
            self.assertEqual(report["summary"]["total_records"], 0)

    def test_format_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = SubmissionTracker(history_path=Path(tmp) / "h.json")
            tracker.add(SubmissionRecord(
                alpha_id="a1", expression="rank(close)", status="submitted",
                sharpe=1.8, composite_score=0.7,
            ))
            report = generate_progress_report(tracker)
            text = format_progress_text(report)
            self.assertIn("提交进展报告", text)
            self.assertIn("Bronze", text)


class TestEstimateStage(unittest.TestCase):
    """阶段估算测试。"""

    def test_stages(self):
        self.assertEqual(_estimate_stage(0)["current_stage"], "Bronze")
        self.assertEqual(_estimate_stage(10)["current_stage"], "Silver")
        self.assertEqual(_estimate_stage(50)["current_stage"], "Gold")
        self.assertEqual(_estimate_stage(200)["current_stage"], "Platinum")
        self.assertEqual(_estimate_stage(500)["current_stage"], "Diamond")


if __name__ == "__main__":
    unittest.main()
