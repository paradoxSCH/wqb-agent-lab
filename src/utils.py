"""共享工具函数：日志、结果持久化与缓存。"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO") -> logging.Logger:
    """配置项目根日志记录器。"""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("wqbrain")


# ---------------------------------------------------------------------------
# 结果持久化
# ---------------------------------------------------------------------------


def save_results(results: list[dict], filepath: str | Path) -> None:
    """将模拟结果保存为 JSON 文件。"""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "timestamp": datetime.now().isoformat(),
        "count": len(results),
        "results": results,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)


def load_results(filepath: str | Path) -> list[dict]:
    """从 JSON 文件读取模拟结果。"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("results", [])


# ---------------------------------------------------------------------------
# 结果缓存
# ---------------------------------------------------------------------------


def _expression_cache_key(expression: str, settings: dict[str, Any] | None = None) -> str:
    """为表达式 + 模拟参数生成稳定的缓存键。"""
    import re
    normalized = re.sub(r"\s+", "", expression).strip()
    raw = normalized
    if settings:
        raw += "|" + json.dumps(settings, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ResultCache:
    """基于本地 JSON 文件的模拟结果缓存。

    缓存格式：一个 JSON 对象，键为表达式哈希，值为 ``{expression, settings, result, ts}``。
    """

    def __init__(self, cache_dir: str | Path = ".local/data/cache") -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"
        self._index: dict[str, dict[str, Any]] = self._load_index()

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index_path.exists():
            with open(self._index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_index(self) -> None:
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2, default=str, ensure_ascii=False)

    def get(
        self,
        expression: str,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """查询缓存。命中返回结果字典，未命中返回 None。"""
        key = _expression_cache_key(expression, settings)
        entry = self._index.get(key)
        if entry:
            return entry.get("result")
        return None

    def put(
        self,
        expression: str,
        result: dict[str, Any],
        settings: dict[str, Any] | None = None,
    ) -> None:
        """写入缓存条目。"""
        key = _expression_cache_key(expression, settings)
        self._index[key] = {
            "expression": expression,
            "settings": settings,
            "result": result,
            "ts": datetime.now().isoformat(),
        }
        self._save_index()

    def put_batch(
        self,
        entries: list[tuple[str, dict[str, Any], dict[str, Any] | None]],
    ) -> None:
        """批量写入缓存条目（减少 IO）。"""
        for expression, result, settings in entries:
            key = _expression_cache_key(expression, settings)
            self._index[key] = {
                "expression": expression,
                "settings": settings,
                "result": result,
                "ts": datetime.now().isoformat(),
            }
        self._save_index()

    def has(self, expression: str, settings: dict[str, Any] | None = None) -> bool:
        """检查缓存是否命中。"""
        key = _expression_cache_key(expression, settings)
        return key in self._index

    def keys(self) -> list[str]:
        """返回缓存中所有表达式。"""
        return [entry["expression"] for entry in self._index.values()]

    @property
    def size(self) -> int:
        return len(self._index)

    def clear(self) -> None:
        """清空缓存。"""
        self._index.clear()
        self._save_index()


# ---------------------------------------------------------------------------
# 结果去重与合并
# ---------------------------------------------------------------------------


def merge_result_files(
    filepaths: list[str | Path],
    output_path: str | Path,
) -> int:
    """合并多个结果文件，按 expression 去重。"""
    seen_expressions: set[str] = set()
    merged: list[dict] = []
    for filepath in filepaths:
        for result in load_results(filepath):
            expr = result.get("expression", "")
            if expr and expr not in seen_expressions:
                seen_expressions.add(expr)
                merged.append(result)
    save_results(merged, output_path)
    return len(merged)
