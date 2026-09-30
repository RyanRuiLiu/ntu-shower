"""喷头状态派生与落库：t_doorlist 解析规则的单一来源（docs/PROTOCOL.md §3）。

派生优先级：repair > disabled > bathing > paused > waiting > selected > free。
判定"使用中"的真据是 cardno/accno 与 inTime/closeTime 组合，status 字段不可靠。
"""

from sqlite3 import Row

from app.core import events
from app.core.clock import now_rfc3339
from app.core.device_clock import correct_rfc3339
from app.core.protocol import JsonObject
from app.modules.doors import repository

# t_doorlist.status 原始值（设备上报的 int，不可靠，仅作辅助判据）
RAW_STATUS_REPAIR = 1
RAW_STATUS_SELECTED = 2
RAW_STATUS_DISABLED = 3

# 派生状态（对外字符串，与 door_live.state 的存储值一致）
STATE_FREE = "free"
STATE_WAITING = "waiting"
STATE_BATHING = "bathing"
STATE_PAUSED = "paused"
STATE_SELECTED = "selected"
STATE_REPAIR = "repair"
STATE_DISABLED = "disabled"

# 占用中的状态：判据见 derive_state
OCCUPIED_STATES = frozenset({STATE_WAITING, STATE_BATHING, STATE_PAUSED})

# 会话结束原因推断：达到浴室 App 默认最长洗浴时长视为自动关阀
MAX_BATH_TIME_DEFAULT_S = 1800


def _safe_int(value: object, default: int = 0) -> int:
    """把设备上报的 JSON 标量转成 int；类型不符或不可转换时取默认值。"""
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    return default


def derive_state(door: JsonObject) -> str:
    status = _safe_int(door.get("status_raw"))
    card_no = _safe_int(door.get("card_no"))
    acc_no = str(door.get("acc_no") or "")
    in_time = door.get("in_time")
    close_time = door.get("close_time")
    bath_time = _safe_int(door.get("bath_time_s"))

    if status == RAW_STATUS_REPAIR:
        return STATE_REPAIR
    if status == RAW_STATUS_DISABLED:
        return STATE_DISABLED

    occupied = card_no != 0 or acc_no not in ("", "0")
    if occupied:
        if in_time:
            return STATE_BATHING
        if bath_time > 0 and close_time:
            return STATE_PAUSED
        return STATE_WAITING
    if status == RAW_STATUS_SELECTED:
        return STATE_SELECTED
    return STATE_FREE


def process_snapshot(device_id: str, payload: JsonObject) -> JsonObject:
    """处理 door.snapshot：派生状态 → diff 记事件 → 更新现态 → 维护洗浴会话。"""
    doors = payload.get("doors")
    if not isinstance(doors, list) or not doors:
        return {"status": "error", "error": {"code": "door.invalid_snapshot", "message": "missing or invalid doors"}}

    occurred_at = now_rfc3339()
    prev_live = repository.get_all_live(device_id)
    processed = 0
    changed: list[JsonObject] = []

    for raw_door in doors:
        if not isinstance(raw_door, dict):
            continue
        door_no = _safe_int(raw_door.get("door_no"))
        if door_no <= 0:
            continue
        state = derive_state(raw_door)
        old = prev_live.get(door_no)
        old_state: str = str(old["state"]) if old is not None else ""

        # 设备时间字段按时钟偏差校准为服务器时钟
        door = dict(raw_door)
        door["open_time"] = correct_rfc3339(_as_text(door.get("open_time")), device_id)
        door["in_time"] = correct_rfc3339(_as_text(door.get("in_time")), device_id)
        door["close_time"] = correct_rfc3339(_as_text(door.get("close_time")), device_id)

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


def _as_text(value: object) -> str | None:
    """设备字段取文本形态；空值统一为 None（correct_rfc3339 对空值原样返回）。"""
    return None if value is None else str(value)


def _maintain_session(
    device_id: str,
    door_no: int,
    state: str,
    door: JsonObject,
    old: Row | None,
    now: str,
) -> None:
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


def _close(open_session: Row, door: JsonObject, old: Row | None, now: str) -> None:
    """会话收尾：释放时浴室 App 会把 bathTime 清零，故取上一现态的累计值。"""
    duration = _safe_int(door.get("bath_time_s")) or (_safe_int(old["bath_time_s"]) if old is not None else 0)
    end_reason = "auto_close" if duration >= MAX_BATH_TIME_DEFAULT_S else "checkout"
    repository.close_session(int(open_session["id"]), now, duration, end_reason)
