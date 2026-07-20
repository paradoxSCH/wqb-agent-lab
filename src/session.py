"""基于仓库自有传输层的 BRAIN API 会话封装。"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from requests import Response

from wqb_agent_lab.runtime.config import Config
from wqb_agent_lab.platform import WQBSession
from wqb_agent_lab.runtime import OperationJournal


logger = logging.getLogger(__name__)


def _relocate_root_wqb_logs(repo_root: Path, log_dir: Path) -> None:
    """Move legacy wqb*.log files from the repo root into the configured log directory."""
    for root_log in repo_root.glob("wqb*.log"):
        if not root_log.is_file():
            continue
        target = log_dir / root_log.name
        try:
            if target.exists():
                root_log.unlink()
            else:
                root_log.replace(target)
        except OSError:
            logger.debug("Unable to relocate WQB root log %s", root_log, exc_info=True)


def _configure_wqb_logger(config: Config) -> logging.Logger:
    """将 WQB 文件日志固定输出到独立目录。"""
    wqb_logger = logging.getLogger("wqb_agent_lab.platform.session")
    wqb_logger.setLevel(getattr(logging, config.log_level, logging.INFO))

    repo_root = Path(__file__).resolve().parent.parent
    log_dir = config.log_dir if config.log_dir.is_absolute() else repo_root / config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"wqb-{datetime.now():%Y%m%d-%H%M%S}.log"
    formatter = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")

    for handler in list(wqb_logger.handlers):
        if isinstance(handler, logging.FileHandler):
            wqb_logger.removeHandler(handler)
            handler.close()

    _relocate_root_wqb_logs(repo_root, log_dir)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    wqb_logger.addHandler(file_handler)
    wqb_logger.propagate = False
    return wqb_logger


@dataclass(slots=True)
class RetryPolicy:
    """同步请求失败时的重试策略。"""

    max_attempts: int = 3
    backoff_seconds: float = 1.0


class BrainAPIError(RuntimeError):
    """BRAIN API 调用失败时抛出的统一异常。"""


class BrainSession:
    """对仓库自有 ``WQBSession`` 的同步业务封装。"""

    def __init__(
        self,
        session: WQBSession,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.session = session
        self.retry_policy = retry_policy or RetryPolicy()

    def authenticate(self) -> dict[str, Any]:
        """获取当前认证信息。"""
        return self._run_json_request("获取认证信息", self.session.get_authentication)

    def validate_session(self) -> bool:
        """检查当前会话是否可用。"""
        try:
            response = self._run_request(
                "验证会话",
                self.session.head_authentication,
                require_ok=False,
            )
        except BrainAPIError:
            return False
        return response.ok

    def close(self) -> bool:
        """关闭当前认证会话。"""
        response = self._run_request(
            "关闭会话",
            self.session.delete_authentication,
            require_ok=False,
        )
        return response.ok

    def search_operators(self) -> list[dict[str, Any]]:
        """获取全部算子定义。"""
        data = self._run_json_request("搜索算子", self.session.search_operators)
        return data if isinstance(data, list) else []

    def locate_dataset(self, dataset_id: str) -> dict[str, Any]:
        """按数据集 ID 获取数据集详情。"""
        return self._run_json_request("获取数据集详情", self.session.locate_dataset, dataset_id)

    def search_datasets_page(
        self,
        region: str,
        delay: int,
        universe: str,
        *args,
        **kwargs,
    ) -> dict[str, Any]:
        """获取一页数据集搜索结果。"""
        return self._run_json_request(
            "搜索数据集分页结果",
            self.session.search_datasets_limited,
            region,
            delay,
            universe,
            *args,
            **kwargs,
        )

    def search_datasets(
        self,
        region: str,
        delay: int,
        universe: str,
        *args,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """拉取全部数据集搜索结果并展平返回。"""
        responses = self._run_generator(
            "搜索数据集",
            self.session.search_datasets,
            region,
            delay,
            universe,
            *args,
            **kwargs,
        )
        return self._collect_results("搜索数据集", responses)

    def locate_field(self, field_id: str) -> dict[str, Any]:
        """按字段 ID 获取字段详情。"""
        return self._run_json_request("获取字段详情", self.session.locate_field, field_id)

    def search_fields_page(
        self,
        region: str,
        delay: int,
        universe: str,
        *args,
        **kwargs,
    ) -> dict[str, Any]:
        """获取一页字段搜索结果。"""
        return self._run_json_request(
            "搜索字段分页结果",
            self.session.search_fields_limited,
            region,
            delay,
            universe,
            *args,
            **kwargs,
        )

    def search_fields(
        self,
        region: str,
        delay: int,
        universe: str,
        *args,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """拉取全部字段搜索结果并展平返回。"""
        responses = self._run_generator(
            "搜索字段",
            self.session.search_fields,
            region,
            delay,
            universe,
            *args,
            **kwargs,
        )
        return self._collect_results("搜索字段", responses)

    def locate_alpha(self, alpha_id: str) -> dict[str, Any]:
        """按 Alpha ID 获取详情。"""
        return self._run_json_request("获取 Alpha 详情", self.session.locate_alpha, alpha_id)

    def filter_alphas_page(self, *args, **kwargs) -> dict[str, Any]:
        """获取一页 Alpha 过滤结果。"""
        return self._run_json_request(
            "过滤 Alpha 分页结果",
            self.session.filter_alphas_limited,
            *args,
            **kwargs,
        )

    def filter_alphas(self, *args, **kwargs) -> list[dict[str, Any]]:
        """拉取全部 Alpha 过滤结果并展平返回。"""
        responses = self._run_generator(
            "过滤 Alpha",
            self.session.filter_alphas,
            *args,
            **kwargs,
        )
        return self._collect_results("过滤 Alpha", responses)

    def _run_request(
        self,
        action: str,
        operation: Callable[..., Response | None],
        *args,
        require_ok: bool = True,
        **kwargs,
    ) -> Response:
        """执行一次同步请求，并在失败时重试。"""
        last_error: Exception | None = None
        max_attempts = max(1, self.retry_policy.max_attempts)

        for attempt in range(1, max_attempts + 1):
            try:
                response = operation(*args, **kwargs)
                if response is None:
                    raise BrainAPIError(f"{action} 未返回响应对象")
                if require_ok and not response.ok:
                    raise BrainAPIError(self._format_response_error(action, response))
                return response
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    break
                logger.warning(
                    "%s失败，准备进行第 %d/%d 次重试：%s",
                    action,
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                if self.retry_policy.backoff_seconds > 0:
                    time.sleep(self.retry_policy.backoff_seconds)

        raise BrainAPIError(f"{action}失败：{last_error}") from last_error

    def _run_json_request(
        self,
        action: str,
        operation: Callable[..., Response | None],
        *args,
        **kwargs,
    ) -> dict[str, Any] | list[Any]:
        """执行请求并解析 JSON。"""
        response = self._run_request(action, operation, *args, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise BrainAPIError(f"{action}返回了无法解析的 JSON") from exc

    def _run_generator(
        self,
        action: str,
        operation: Callable[..., Iterable[Response]],
        *args,
        **kwargs,
    ) -> list[Response]:
        """执行分页请求生成器，并保证每页都成功。"""
        last_error: Exception | None = None
        max_attempts = max(1, self.retry_policy.max_attempts)

        for attempt in range(1, max_attempts + 1):
            try:
                responses = list(operation(*args, **kwargs))
                for index, response in enumerate(responses, start=1):
                    if not response.ok:
                        raise BrainAPIError(self._format_response_error(f"{action} 第{index}页", response))
                return responses
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    break
                logger.warning(
                    "%s失败，准备进行第 %d/%d 次重试：%s",
                    action,
                    attempt + 1,
                    max_attempts,
                    exc,
                )
                if self.retry_policy.backoff_seconds > 0:
                    time.sleep(self.retry_policy.backoff_seconds)

        raise BrainAPIError(f"{action}失败：{last_error}") from last_error

    @staticmethod
    def _collect_results(action: str, responses: Iterable[Response]) -> list[dict[str, Any]]:
        """从分页响应中提取 ``results`` 列表。"""
        items: list[dict[str, Any]] = []
        for response in responses:
            try:
                payload = response.json()
            except ValueError as exc:
                raise BrainAPIError(f"{action}返回了无法解析的 JSON") from exc
            if isinstance(payload, dict):
                results = payload.get("results", [])
                if isinstance(results, list):
                    items.extend(results)
            elif isinstance(payload, list):
                items.extend(payload)
        return items

    @staticmethod
    def _format_response_error(action: str, response: Response) -> str:
        """格式化 HTTP 错误信息。"""
        return (
            f"{action}失败，HTTP {response.status_code}"
            f"，原因：{response.reason or '未知'}"
            f"，内容：{response.text}"
        )


def create_session(config: Config) -> WQBSession:
    """创建带自动认证能力的 ``WQBSession``。"""
    if not config.email or not config.password:
        raise ValueError("必须在 .env 中设置 WQB_EMAIL 和 WQB_PASSWORD")

    wqb_logger = _configure_wqb_logger(config)

    repo_root = Path(__file__).resolve().parent.parent
    journal_path = Path(
        os.getenv(
            "WQB_OPERATION_JOURNAL",
            str(repo_root / ".local" / "data" / "runtime" / "operations.db"),
        )
    )
    session = WQBSession(
        (config.email, config.password),
        logger=wqb_logger,
        auth_max_tries=10,
        auth_delay_unexpected=15.0,
        operation_journal=OperationJournal(journal_path),
        run_id=str(os.getenv("WQB_RUN_ID") or ""),
    )

    logger.info("已为 %s 创建 BRAIN 会话", config.email)
    return session


def create_brain_session(config: Config) -> BrainSession:
    """创建带重试与结果聚合能力的业务会话封装。"""
    raw_session = create_session(config)
    retry_policy = RetryPolicy(
        max_attempts=config.request_max_attempts,
        backoff_seconds=config.request_backoff_seconds,
    )
    return BrainSession(raw_session, retry_policy=retry_policy)
