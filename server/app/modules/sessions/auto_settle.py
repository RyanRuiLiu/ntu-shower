"""自动结算：占用超过阈值（且该学号在结算名单内）时自动下发结算。

对齐旧项目语义（AUTO_SETTLE_THRESHOLD + AUTO_SETTLE_STUDENTS）：
- 名单外学员不自动结算（避免误操作他人）；
- 结算凭证使用名单中记录的密码（旧项目以 env sid:pwd 提供，
  本系统由管理面远程配置 auto_settle_students 提供，键为 cert_no）；
- 单次扫描至多下发一条结算（设备单在途命令约束下的保守节奏）。
"""

import asyncio
from datetime import datetime, timezone

from app.core import actions, outcomes
from app.core.clock import parse_rfc3339
from app.core.logging import log_event
from app.core.protocol import JsonObject, as_int
from app.gateway.manager import manager
from app.modules.config import store as config_store
from app.modules.doors import repository as doors_repository
from app.modules.sessions import service as sessions_service

_task: asyncio.Task[None] | None = None
SCAN_INTERVAL_S = 30


def _duration_s(assigned_at: str) -> int:
    """会话从分配到现在的秒数；时间不可解析时返回 0（不参与自动结算）。

    以**分配时刻**起算而不是开阀时刻：占用是从刷卡入门那刻开始的，旧系统释放喷头的
    第一个条件（`maxinspacetime`，入门后 5 分钟未开水）也是从这一刻算。
    """
    moment = parse_rfc3339(assigned_at)
    if moment is None:
        return 0
    return int((datetime.now(timezone.utc) - moment).total_seconds())


def pick_auto_settle_candidate(config: JsonObject) -> tuple[str, str, str, int] | None:
    """选出 (device_id, cert_no, password, duration_s) 超时结算候选；无则 None。

    纯函数，便于单元测试。密码随候选一并取出：调用方无需再解析一次配置，
    也就不会因为重复解析而引入不可达的防御分支。

    `config` 取自 `config_store.effective()`，它保证含登记表里的每一个键，
    因此这里直接取值——缺键即 KeyError，好过用兜底阈值静默结算错人。
    """
    if not config.get("auto_settle_enabled"):
        return None
    threshold_s = as_int(config["auto_settle_threshold_s"])
    students = config.get("auto_settle_students")
    if not isinstance(students, dict):
        return None

    for session in doors_repository.list_open_sessions():
        cert_no = str(session["cert_no"] or "")
        if cert_no not in students:
            continue
        duration = _duration_s(str(session["assigned_at"] or ""))
        if duration < threshold_s:
            continue
        return str(session["device_id"]), cert_no, str(students[cert_no]), duration
    return None


async def _settle(device_id: str, cert_no: str, password: str) -> bool:
    result = await sessions_service.send_command_and_wait(
        device_id,
        actions.SESSION_SETTLE,
        {"cert_no": cert_no, "password": password},
        timeout_s=60.0,
    )
    ok = result.get("status") == outcomes.OK.value
    log_event(
        "INFO",
        "auto-settle.settled",
        "自动结算执行完成",
        device_id=device_id,
        cert_no=cert_no,
        status=str(result.get("status")),
    )
    return ok


async def scan_once() -> bool:
    """单次扫描；命中并成功结算返回 True。"""
    config = config_store.effective("__global__")  # 全局默认（无设备覆盖场景）
    candidate = pick_auto_settle_candidate(config)
    if candidate is None:
        return False
    device_id, cert_no, password, _duration = candidate
    if not manager.is_online(device_id):
        return False
    return await _settle(device_id, cert_no, password)


async def _loop() -> None:
    while True:
        try:
            await scan_once()
        except Exception as exc:  # noqa: BLE001 —— 扫描循环必须存活：单次失败不得终止后续扫描，且已记录日志
            log_event("WARNING", "auto-settle.scan-failed", "自动结算扫描失败", error=str(exc))
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
