"""自动结算：占用超过阈值（且该学号在结算名单内）时自动下发结算。

对齐旧项目语义（AUTO_SETTLE_THRESHOLD + AUTO_SETTLE_STUDENTS）：
- 名单外学员不自动结算（避免误操作他人）；
- 结算凭证使用名单中记录的密码（旧项目以 env sid:pwd 提供，
  本系统由管理面远程配置 auto_settle_students 提供，键为 cert_no）；
- 单次扫描至多下发一条结算（设备单在途命令约束下的保守节奏）。
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.core.clock import parse_rfc3339
from app.gateway.manager import manager
from app.modules.config import store as config_store
from app.modules.doors import repository as doors_repository

logger = logging.getLogger("ntu-shower.auto-settle")

_task: asyncio.Task | None = None
SCAN_INTERVAL_S = 30


def _duration_s(started_at: str) -> int:
    """会话已持续秒数；时间不可解析时返回 0（不参与自动结算）。"""
    moment = parse_rfc3339(started_at)
    if moment is None:
        return 0
    return int((datetime.now(timezone.utc) - moment).total_seconds())


def pick_auto_settle_candidate(config: dict) -> tuple[str, str, int] | None:
    """从活跃会话中选出（device_id, cert_no, duration_s）超时结算候选；无则 None。

    纯函数，便于单元测试。
    """
    if not config.get("auto_settle_enabled"):
        return None
    threshold_s = int(config.get("auto_settle_threshold_s", 1140))
    students = config.get("auto_settle_students") or {}
    if not isinstance(students, dict):
        return None

    for session in doors_repository.list_open_sessions():
        cert_no = str(session["cert_no"] or "")
        if cert_no not in students:
            continue
        duration = _duration_s(str(session["started_at"] or ""))
        if duration < threshold_s:
            continue
        return str(session["device_id"]), cert_no, duration
    return None


async def _settle(device_id: str, cert_no: str, password: str) -> bool:
    from app.modules.sessions import service as sessions_service

    result = await sessions_service.send_command_and_wait(
        device_id,
        "session.settle",
        {"cert_no": cert_no, "password": password},
        timeout_s=60.0,
    )
    ok = result.get("status") == "ok"
    logger.info("auto-settle %s/%s: %s", device_id, cert_no, result.get("status"))
    return ok


async def scan_once() -> bool:
    """单次扫描；命中并成功结算返回 True。"""
    config = config_store.effective("__global__")  # 全局默认（无设备覆盖场景）
    candidate = pick_auto_settle_candidate(config)
    if candidate is None:
        return False
    device_id, cert_no, _duration = candidate
    password = str((config.get("auto_settle_students") or {})[cert_no])
    if not manager.is_online(device_id):
        return False
    return await _settle(device_id, cert_no, password)


async def _loop() -> None:
    while True:
        try:
            await scan_once()
        except Exception as exc:  # noqa: BLE001 —— 扫描循环必须存活：单次失败不得终止后续扫描，且已记录日志
            logger.warning("auto-settle scan failed: %s", exc)
        await asyncio.sleep(SCAN_INTERVAL_S)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
