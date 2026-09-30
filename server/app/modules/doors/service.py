"""喷头状态派生与落库：t_doorlist 解析规则的单一来源（docs/PROTOCOL.md §3）。

派生优先级：repair > disabled > bathing > paused > waiting > selected > free。
判定"使用中"的真据是 cardno/accno 与 inTime/closeTime 组合，status 字段不可靠。
"""

from typing import Any

from app.core import events
from app.core.clock import now_rfc3339
from app.core.device_clock import correct_rfc3339
from app.modules.doors import repository

# t_doorlist.status 原始值
STATUS_REPAIR = 1
STATUS_SELECTED = 2
STATUS_DISABLED = 3

# 派生态
FREE = "free"
WAITING = "waiting"
BATHING = "bathing"
PAUSED = "paused"
SELECTED = "selected"
REPAIR = "repair"
DISABLED = "disabled"
UNKNOWN = "unknown"

OCCUPIED_STATES = {WAITING, BATHING, PAUSED}

# 会话结束原因推断：达到浴室 App 默认最长洗浴时长视为自动关阀
MAX_BATH_TIME_DEFAULT_S = 1800


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def derive_state(door: dict) -> str:
    status = _safe_int(door.get("status_raw"))
    card_no = _safe_int(door.get("card_no"))
    acc_no = str(door.get("acc_no") or "")
    in_time = door.get("in_time")
    close_time = door.get("close_time")
    bath_time = _safe_int(door.get("bath_time_s"))

    if status == STATUS_REPAIR:
        return REPAIR
    if status == STATUS_DISABLED:
        return DISABLED

    occupied = card_no != 0 or acc_no not in ("", "0")
    if occupied:
        if in_time:
            return BATHING
        if bath_time > 0 and close_time:
            return PAUSED
        return WAITING
    if status == STATUS_SELECTED:
        return SELECTED
    return FREE


def process_snapshot(device_id: str, payload: dict) -> dict:
    """处理 door.snapshot：派生状态 → diff 记事件 → 更新现态 → 维护洗浴会话。"""
    doors = payload.get("doors")
    if not isinstance(doors, list) or not doors:
        return {"status": "error", "error": {"code": "door.invalid_snapshot", "message": "missing or invalid doors"}}

    occurred_at = now_rfc3339()
    prev_live = repository.get_all_live(device_id)
    processed = 0
    changed: list[dict] = []

    for door in doors:
        door_no = _safe_int(door.get("door_no"))
        if door_no <= 0:
            continue
        state = derive_state(door)
        old = prev_live.get(door_no)
        old_state: str = str(old["state"]) if old is not None else ""

        # 设备时间字段按时钟偏差校准为服务器时钟
        door = dict(door)
        door["open_time"] = correct_rfc3339(door.get("open_time"), device_id)
        door["in_time"] = correct_rfc3339(door.get("in_time"), device_id)
        door["close_time"] = correct_rfc3339(door.get("close_time"), device_id)

        if old is not None and old_state != state:
            repository.insert_event(
                occurred_at,
                device_id,
                door_no,
                old_state,
                state,
                {"card_no": _safe_int(door.get("card_no"))},
            )

        repository.upsert_door_live(device_id, door_no, state, door, occurred_at)
        _maintain_session(device_id, door_no, state, door, old, occurred_at)
        changed.append({"door_no": door_no, "state": state, "card_no": _safe_int(door.get("card_no"))})
        processed += 1

    events.publish("door_update", {"device_id": device_id, "doors": changed, "at": occurred_at})
    return {"status": "ok", "processed": processed}


def _maintain_session(device_id: str, door_no: int, state: str, door: dict, old: Any | None, now: str) -> None:
    open_session = repository.get_open_session(device_id, door_no)

    if state in OCCUPIED_STATES:
        card_no = _safe_int(door.get("card_no"))
        if open_session is None:
            started_at = str(door.get("open_time") or "") or now
            repository.open_session(device_id, door_no, card_no, str(door.get("cert_no") or ""), started_at)
        elif _safe_int(open_session["card_no"]) != card_no:
            # 占用者中途变化：先结旧账再开新账
            _close(open_session, door, old, now)
            repository.open_session(device_id, door_no, card_no, str(door.get("cert_no") or ""), now)
        return

    # 空闲/报修/禁用：会话收尾
    if open_session is not None:
        _close(open_session, door, old, now)


def _close(open_session: Any, door: dict, old: Any | None, now: str) -> None:
    """会话收尾：释放时浴室 App 会把 bathTime 清零，故取上一现态的累计值。"""
    duration = _safe_int(door.get("bath_time_s")) or (_safe_int(old["bath_time_s"]) if old is not None else 0)
    end_reason = "auto_close" if duration >= MAX_BATH_TIME_DEFAULT_S else "checkout"
    repository.close_session(int(open_session["id"]), now, duration, end_reason)
