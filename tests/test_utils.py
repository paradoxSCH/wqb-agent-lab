"""utils 模块单元测试。"""

import tempfile
import unittest
from pathlib import Path

from src.utils import (
    ResultCache,
    load_results,
    merge_result_files,
    save_results,
)


class TestSaveLoadResults(unittest.TestCase):
    """结果保存与加载测试。"""

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            filepath = Path(tmp) / "results.json"
            original = [{"expression": "a", "sharpe": 1.5}, {"expression": "b", "sharpe": 2.0}]
            save_results(original, filepath)
            loaded = load_results(filepath)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["expression"], "a")


class TestResultCache(unittest.TestCase):
    """结果缓存测试。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cache = ResultCache(cache_dir=self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_put_and_get(self):
        self.cache.put("rank(close)", {"success": True, "data": {"sharpe": 1.5}})
        result = self.cache.get("rank(close)")
        self.assertIsNotNone(result)
        self.assertEqual(result["data"]["sharpe"], 1.5)

    def test_miss_returns_none(self):
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_has(self):
        self.cache.put("x", {"ok": True})
        self.assertTrue(self.cache.has("x"))
        self.assertFalse(self.cache.has("y"))

    def test_settings_differentiate(self):
        s1 = {"region": "USA"}
        s2 = {"region": "CHN"}
        self.cache.put("rank(close)", {"r": 1}, settings=s1)
        self.cache.put("rank(close)", {"r": 2}, settings=s2)
        self.assertEqual(self.cache.get("rank(close)", s1)["r"], 1)
        self.assertEqual(self.cache.get("rank(close)", s2)["r"], 2)

    def test_put_batch(self):
        entries = [
            ("a", {"ok": True}, None),
            ("b", {"ok": True}, None),
            ("c", {"ok": True}, None),
        ]
        self.cache.put_batch(entries)
        self.assertEqual(self.cache.size, 3)

    def test_clear(self):
        self.cache.put("x", {"ok": True})
        self.cache.clear()
        self.assertEqual(self.cache.size, 0)

    def test_persistence_across_instances(self):
        self.cache.put("alpha", {"val": 42})
        new_cache = ResultCache(cache_dir=self._tmp)
        self.assertEqual(new_cache.get("alpha")["val"], 42)


class TestMergeResults(unittest.TestCase):
    """结果合并去重测试。"""

    def test_merge_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            f1 = Path(tmp) / "a.json"
            f2 = Path(tmp) / "b.json"
            out = Path(tmp) / "merged.json"

            save_results([{"expression": "x"}, {"expression": "y"}], f1)
            save_results([{"expression": "y"}, {"expression": "z"}], f2)

            count = merge_result_files([f1, f2], out)
            self.assertEqual(count, 3)
            merged = load_results(out)
            exprs = [r["expression"] for r in merged]
            self.assertEqual(len(set(exprs)), 3)


if __name__ == "__main__":
    unittest.main()
