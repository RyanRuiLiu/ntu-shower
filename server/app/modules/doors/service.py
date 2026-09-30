"""喷头状态派生与落库：t_doorlist 解析规则的单一来源（protocol/schema/）。

派生优先级：repair > disabled > bathing > paused > waiting > selected > free。
判定"使用中"的真据是 cardno/accno 与 inTime/closeTime 组合，status 字段不可靠。

## 会话是怎么记下来的

设备上报的是**快照**（每扇门此刻的字段），不是事件流——浴室 App 也不会通知"某人洗完了"。
所以洗浴会话由服务端从快照序列里推导，两条规矩：

**边界取自状态的进出**：门进入占用态即开会话，离开即结束。占用态见 `OCCUPIED_STATES`，
与旧系统 `t_doorlist` 里 `openTime <> ''` 的语义等价。

**里程碑取自字段**：分配时刻、开阀时刻、累计出水时长都直接由协议字段算出，不依赖
"观察到某个状态转移"。设备端有上报去抖，会合并短时间内的多次变化，漏一帧转移就永久
丢一次记录；而字段是幂等的——任何一帧带着 `inTime` 都能把 `opened_at` 补回来。

两个字段的实时语义容易看错（据旧系统逆向确认）：

- `inTime` 是**本次**开阀时刻，暂停时被清空、恢复时重写，不是"首次开阀"；
- `bathTime` **只在暂停那一刻**累加本次出水，出水期间不动，结束时整体归零。

所以"这次洗了多久"必须实时算（见 `_elapsed_flow_s`），直接取 `bathTime` 会在
"正在洗浴时直接出门"那一帧拿到 0。
"""

from sqlite3 import Row

from app.core import events, reason_codes
from app.core.clock import now_rfc3339, seconds_between
from app.core.device_clock import correct_rfc3339
from app.core.protocol import JsonObject
from app.modules.doors import repository
from app.modules.doors.schemas import OCCUPIED_STATES, DoorState

# t_doorlist.status 原始值（设备上报的 int，不可靠，仅作辅助判据）
RAW_STATUS_REPAIR = 1
RAW_STATUS_SELECTED = 2
RAW_STATUS_DISABLED = 3


def _safe_int(value: object, default: int = 0) -> int:
    """把设备上报的 JSON 标量转成 int；类型不符或不可转换时取默认值。"""
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    return default


def derive_state(door: JsonObject) -> DoorState:
    status = _safe_int(door.get("status_raw"))
    card_no = _safe_int(door.get("card_no"))
    acc_no = str(door.get("acc_no") or "")
    in_time = door.get("in_time")
    close_time = door.get("close_time")
    bath_time = _safe_int(door.get("bath_time_s"))

    if status == RAW_STATUS_REPAIR:
        return DoorState.REPAIR
    if status == RAW_STATUS_DISABLED:
        return DoorState.DISABLED

    occupied = card_no != 0 or acc_no not in ("", "0")
    if occupied:
        if in_time:
            return DoorState.BATHING
        if bath_time > 0 and close_time:
            return DoorState.PAUSED
        return DoorState.WAITING
    if status == RAW_STATUS_SELECTED:
        return DoorState.SELECTED
    return DoorState.FREE


def process_snapshot(device_id: str, payload: JsonObject) -> JsonObject:
    """处理 door.snapshot：派生状态 → diff 记事件 → 更新现态 → 维护洗浴会话。"""
    doors = payload.get("doors")
    if not isinstance(doors, list) or not doors:
        return {
            "status": "error",
            "error": {
                "code": reason_codes.DOOR_INVALID_SNAPSHOT.code,
                "message": reason_codes.DOOR_INVALID_SNAPSHOT.message,
            },
        }

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

        repository.upsert_door_live(device_id, door_no, state.value, door, occurred_at)
        _maintain_session(device_id, door_no, state, door, occurred_at)
        changed.append({"door_no": door_no, "state": state, "card_no": _safe_int(door.get("card_no"))})
        processed += 1

    events.publish("door_update", {"device_id": device_id, "doors": changed, "at": occurred_at})
    return {"status": "ok", "processed": processed}


def _as_text(value: object) -> str | None:
    """设备字段取文本形态；空值统一为 None（correct_rfc3339 对空值原样返回）。"""
    return None if value is None else str(value)


def _elapsed_flow_s(door: JsonObject, now: str) -> int:
    """截至此刻的累计出水秒数。

    设备的 `bathTime` **只在暂停那一刻**把本次出水累加进去：出水期间它保持不动，
    会话结束（用户出门或四种自动关阀）时又整体归零。所以必须实时算：

    - 正在出水（`inTime` 非空）→ `bathTime + (now - inTime)`
    - 暂停中（`inTime` 为空）  → `bathTime`

    与旧系统 `BathMainActivity` 的 `bathtimetmp = |inTime - nowTime| + bathTime`
    是同一个公式。不这么算，「正在洗浴时直接刷卡出门」那一帧取到的会是 0——那次
    真实出水就被记成 0 秒了。
    """
    accumulated = _safe_int(door.get("bath_time_s"))
    in_time = str(door.get("in_time") or "")
    if not in_time:
        return accumulated
    return accumulated + seconds_between(in_time, now)


def _maintain_session(device_id: str, door_no: int, state: DoorState, door: JsonObject, now: str) -> None:
    """维护洗浴会话：**边界取自状态的进出，里程碑取自字段**。

    四类情形在这里合流：

    1. 进入占用态 → 开会话（`assigned_at` 取设备的 `openTime`，即刷卡入门时刻）；
    2. 占用态内部迁移 → 推进里程碑，`opened_at` / `flow_s` 都只增不减；
    3. 离开占用态 → 结束会话并判定原因；
    4. 不涉及会话的迁移 → 门只是在可用性之间切换，什么都不做。

    里程碑之所以从字段取而不从"观察到的转移"取：设备端有上报去抖，会合并短时间内的
    多次变化，漏掉一帧转移就永久丢一次记录。字段是幂等的——任何一帧带着 `inTime`，
    `touch_session` 都能把 `opened_at` 补上。
    """
    open_session = repository.get_open_session(device_id, door_no)

    if state not in OCCUPIED_STATES:
        if open_session is not None:
            _close(open_session, door, now)
        return

    card_no = _safe_int(door.get("card_no"))
    if open_session is None:
        assigned_at = str(door.get("open_time") or "") or now
        session_id = repository.open_session(device_id, door_no, card_no, str(door.get("cert_no") or ""), assigned_at)
    elif _safe_int(open_session["card_no"]) != card_no:
        # 占用者中途变化：先把旧账结掉再开新账，否则两个人的时长会并进同一条记录。
        _close(open_session, door, now)
        session_id = repository.open_session(device_id, door_no, card_no, str(door.get("cert_no") or ""), now)
    else:
        session_id = int(open_session["id"])

    repository.touch_session(session_id, str(door.get("in_time") or ""), _elapsed_flow_s(door, now))


def _close(open_session: Row, door: JsonObject, now: str) -> None:
    """结束一次会话。

    **先推进里程碑再收尾**：结束这一帧本身可能带着新的 `inTime`（例如刚开阀就出门），
    不补这一下会让 `opened_at` 丢掉，那次使用随即被判成"从没出过水"。

    不记结束原因——浴室 App 在"用户主动出门"和三种超时释放下写的是同一套清零语句，
    服务端没有依据区分（详见 `repository.close_session`）。
    """
    session_id = int(open_session["id"])
    repository.touch_session(session_id, str(door.get("in_time") or ""), _elapsed_flow_s(door, now))
    repository.close_session(session_id, now)
