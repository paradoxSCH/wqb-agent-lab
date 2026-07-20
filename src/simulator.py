"""Alpha 单次与批量模拟编排。"""

import asyncio
import json
import logging
import time
from typing import Any

from requests import Response

from wqb_agent_lab.runtime import SideEffectUncertainError

from .alpha_generator import build_alpha_object
from wqb_agent_lab.runtime.config import Config
from .session import BrainSession
from wqb_agent_lab.governance.side_effects import require_side_effect_capability
from wqb_agent_lab.platform.session import (
    LOCATION,
    RETRY_AFTER,
    URL_ALPHAS_ALPHAID,
    WQBSession,
)


logger = logging.getLogger(__name__)

# BRAIN API simulation endpoint URL prefix
_ALPHAS_URL = URL_ALPHAS_ALPHAID


def _response_json_or_none(resp: Response | None) -> dict[str, Any] | list[Any] | None:
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def summarize_simulation_payload(payload: Any) -> str:
    """Summarize non-alpha simulation payloads for logging and persistence."""
    if payload is None:
        return "Simulation payload missing or not JSON"
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if value:
                return f"Simulation returned {key}: {value}"
        if "settings" in payload:
            return f"Simulation returned settings validation payload: {json.dumps(payload, ensure_ascii=False)}"
        if "progress" in payload:
            return f"Simulation polling ended without alpha; last progress={payload.get('progress')}"
        status = payload.get("status")
        if status is not None:
            return f"Simulation polling ended with status={status} and no alpha"
    return f"Simulation returned payload without alpha: {json.dumps(payload, ensure_ascii=False)}"


async def simulate_until_alpha_response(
    session: WQBSession | BrainSession,
    alpha_obj: dict[str, Any] | list[Any],
    *,
    max_polls: int = 600,
    default_poll_delay: float = 2.0,
) -> Response | None:
    """Poll the simulation endpoint until it yields an alpha id or a terminal payload.

    The upstream wqb retry helper stops polling as soon as ``Retry-After`` disappears,
    which can still happen on non-terminal payloads like ``{"progress": 0.1}``.
    This wrapper keeps polling until an ``alpha`` appears, an explicit terminal payload
    is returned, or the poll budget is exhausted.
    """
    require_side_effect_capability("simulation")
    raw_session = _unwrap_session(session)
    resp = raw_session.create_simulation(alpha_obj)
    if resp is None or not resp.ok:
        return resp

    poll_url = resp.headers.get(LOCATION)
    if not poll_url:
        return None

    last_resp: Response | None = None
    for _ in range(max_polls):
        last_resp = raw_session.get(poll_url)
        if not last_resp.ok:
            return last_resp

        payload = _response_json_or_none(last_resp)
        if isinstance(payload, dict) and payload.get("alpha"):
            return last_resp

        retry_after = last_resp.headers.get(RETRY_AFTER)
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = default_poll_delay
            await asyncio.sleep(delay)
            continue

        if isinstance(payload, dict):
            if "progress" in payload:
                await asyncio.sleep(default_poll_delay)
                continue
            status = str(payload.get("status", "")).upper()
            if status in {"PENDING", "RUNNING", "PROCESSING", "QUEUED"}:
                await asyncio.sleep(default_poll_delay)
                continue

        return last_resp

    return last_resp


def _unwrap_session(session: WQBSession | BrainSession) -> WQBSession:
    """兼容原始 ``WQBSession`` 和 ``BrainSession`` 封装。"""
    return session.session if isinstance(session, BrainSession) else session


async def simulate_single(
    session: WQBSession | BrainSession,
    expression: str,
    settings_dict: dict,
) -> dict[str, Any]:
    """模拟单个 Alpha，并返回标准化结果。

    BRAIN 模拟分两步：
    1. ``POST /simulations`` 轮询至 ``status=COMPLETE``，获得 ``alpha`` ID。
    2. ``GET /alphas/<alpha_id>`` 获取含 ``is`` 指标的完整 Alpha 对象。
    """
    require_side_effect_capability("simulation")
    alpha_obj = {
        "type": "REGULAR",
        "settings": settings_dict,
        "regular": expression,
    }
    raw_session = _unwrap_session(session)

    try:
        resp = await simulate_until_alpha_response(raw_session, alpha_obj)
        if resp is None:
            return {
                "expression": expression,
                "settings": settings_dict,
                "success": False,
                "error": "BRAIN 模拟接口未返回结果，通常表示轮询超时或响应缺少 Location 头。",
            }
        if not resp.ok:
            return {
                "expression": expression,
                "settings": settings_dict,
                "status_code": resp.status_code,
                "success": False,
                "error": resp.text,
            }

        sim_data = _response_json_or_none(resp) or {}
        alpha_id = sim_data.get("alpha", "")
        if not alpha_id:
            return {
                "expression": expression,
                "settings": settings_dict,
                "success": False,
                "error": summarize_simulation_payload(sim_data),
                "data": sim_data,
            }

        # 获取完整的 Alpha 对象（含 is/os 指标）
        alpha_data = sim_data  # 回退值
        if alpha_id:
            alpha_resp = raw_session.get(_ALPHAS_URL.format(alpha_id))
            if alpha_resp.ok:
                alpha_data = alpha_resp.json()
            else:
                logger.warning(
                    "获取 Alpha %s 详情失败：HTTP %s",
                    alpha_id, alpha_resp.status_code,
                )

        return {
            "expression": expression,
            "settings": settings_dict,
            "status_code": resp.status_code,
            "success": True,
            "data": alpha_data,
        }
    except SideEffectUncertainError as e:
        logger.error("模拟 Alpha '%s' 的远端提交结果未知: %s", expression, e)
        return {
            "expression": expression,
            "settings": settings_dict,
            "success": False,
            "diagnosis": "simulation_unknown_commit",
            "operation_id": e.record.operation_id,
            "operation_fingerprint": e.record.fingerprint,
            "error": str(e),
        }
    except Exception as e:
        logger.error("模拟 Alpha '%s' 失败：%s", expression, e)
        return {
            "expression": expression,
            "settings": settings_dict,
            "success": False,
            "error": str(e),
        }


def simulate_batch(
    session: WQBSession | BrainSession,
    expressions: list[str],
    config: Config,
    *,
    chunk_size: int = 20,
    chunk_delay: float = 15.0,
) -> list[dict[str, Any]]:
    """并发模拟一批 Alpha，返回与 expressions 等长、一一对应的结果列表。

    为避免 BRAIN API 认证端点的速率限制（5 次/分钟），将 Alpha 分为
    每组 *chunk_size* 个，组间等待 *chunk_delay* 秒。
    """
    require_side_effect_capability("simulation")
    raw_session = _unwrap_session(session)
    settings_dict = config.simulation.to_dict()
    concurrency = min(config.max_concurrency, 3)  # 硬上限 3 防止 auth 竞争

    logger.info(
        "开始模拟 %d 个 Alpha（并发=%d，分块=%d，间隔=%.0fs）",
        len(expressions),
        concurrency,
        chunk_size,
        chunk_delay,
    )

    alphas = [build_alpha_object(expr, config.simulation) for expr in expressions]
    all_results: list[dict[str, Any]] = []

    for start in range(0, len(alphas), chunk_size):
        end = min(start + chunk_size, len(alphas))
        chunk_alphas = alphas[start:end]
        chunk_exprs = expressions[start:end]

        logger.info(
            "模拟分块 %d-%d/%d …",
            start + 1,
            end,
            len(alphas),
        )

        resps = asyncio.run(
            raw_session.concurrent_simulate(
                chunk_alphas,
                concurrency,
                return_exceptions=True,
            )
        )

        for expr, resp in zip(chunk_exprs, resps, strict=False):
            if isinstance(resp, Exception):
                all_results.append({
                    "expression": expr,
                    "settings": settings_dict,
                    "success": False,
                    "error": str(resp),
                })
            elif resp is None:
                all_results.append({
                    "expression": expr,
                    "settings": settings_dict,
                    "success": False,
                    "error": "BRAIN 模拟接口未返回结果。",
                })
            else:
                result: dict[str, Any] = {
                    "expression": expr,
                    "settings": settings_dict,
                    "status_code": resp.status_code,
                    "success": resp.ok,
                }
                if resp.ok:
                    sim_data = resp.json()
                    alpha_id = sim_data.get("alpha", "")
                    alpha_data = sim_data
                    if alpha_id:
                        alpha_resp = raw_session.get(
                            _ALPHAS_URL.format(alpha_id)
                        )
                        if alpha_resp.ok:
                            alpha_data = alpha_resp.json()
                        else:
                            logger.warning(
                                "获取 Alpha %s 详情失败：HTTP %s",
                                alpha_id,
                                alpha_resp.status_code,
                            )
                    result["data"] = alpha_data
                else:
                    result["error"] = resp.text
                all_results.append(result)

        ok_so_far = sum(1 for r in all_results if r.get("success"))
        logger.info("分块完成：累计 %d/%d 成功", ok_so_far, len(all_results))

        # 分块间歇——让 auth 速率窗口重置
        if end < len(alphas):
            logger.info("等待 %.0f 秒避免触发速率限制…", chunk_delay)
            time.sleep(chunk_delay)

    logger.info(
        "批量模拟完成：%d/%d 成功",
        sum(1 for r in all_results if r.get("success")),
        len(all_results),
    )

    return all_results
