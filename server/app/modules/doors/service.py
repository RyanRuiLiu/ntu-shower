"""喷头状态派生与洗浴会话维护：t_doorlist 解析规则的单一来源。

状态派生优先级：repair > disabled > bathing > paused > waiting > selected > free。
"使用中"的判据是 cardno 与 accno 的组合，status 字段不可靠（详见 derive_state）。

洗浴会话由快照序列推导，设备只上报快照、不报事件。两条规则：
- 边界取自状态的进出：进入占用态开会话，离开即结束（占用态见 OCCUPIED_STATES）；
- 里程碑取自字段：分配、开阀、出水时长直接由协议字段算出。字段是幂等的，任何
  一帧带着 inTime 都能把 opened_at 补回来，不依赖去抖后可能漏掉的转移帧。

两个字段的实时语义：inTime 是本次开阀时刻，暂停时清空、恢复时重写；bathTime
只在暂停那一刻累加本次出水，出水期间不动，结束时归零。
"""

from sqlite3 import Row

from app.core import events, reason_codes
from app.core.clock import now_rfc3339, seconds_between
from app.core.device_clock import correct_rfc3339
from app.core.protocol import JsonObject, as_int
from app.modules.devices import service as devices_service
from app.modules.doors import repository
from app.modules.doors.schemas import OCCUPIED_STATES, DoorState, DoorView

# t_doorlist.status 的原始值（设备上报的 int，仅作辅助判据）
RAW_STATUS_REPAIR = 1
RAW_STATUS_SELECTED = 2
RAW_STATUS_DISABLED = 3


def list_doors(device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表。两个列表端点与 shadow 都走这里；device_online 由
    core/connections 的连接表填入。"""
    doors = repository.list_doors(device_id)
    for door in doors:
        door["device_online"] = devices_service.is_online(door["device_id"])
    return doors


def derive_state(door: JsonObject) -> DoorState:
    """由一帧设备快照派生喷头状态（判据见模块注释）。"""
    status = as_int(door.get("status_raw"))
    card_no = as_int(door.get("card_no"))
    acc_no = str(door.get("acc_no") or "")
    in_time = door.get("in_time")
    close_time = door.get("close_time")
    bath_time = as_int(door.get("bath_time_s"))

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
    """处理 door.snapshot：派生状态、diff 记事件、更新现态、维护洗浴会话。"""
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

    for raw_door in doors:
        if not isinstance(raw_door, dict):
            continue
        door_no = as_int(raw_door.get("door_no"))
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
                {"card_no": as_int(door.get("card_no"))},
            )

        repository.upsert_door_live(device_id, door_no, state.value, door, occurred_at)
        _maintain_session(device_id, door_no, state, door, occurred_at)
        processed += 1

    # 载荷是该设备的完整现态行，与 GET /doors 同一 service 路径产出（构造上同源）。
    # WS 单连接有序投递，数据承载推送是行业标准（Supabase/Firebase 同型）：客户端
    # 收到后直接应用，请求量与事件量解耦；无变化的上报也照发，充当状态再同步。
    events.publish(events.DOOR_UPDATE, {"device_id": device_id, "at": occurred_at, "doors": list_doors(device_id)})
    return {"status": "ok", "processed": processed}


def _as_text(value: object) -> str | None:
    """设备字段取文本形态；空值统一为 None（correct_rfc3339 对空值原样返回）。"""
    return None if value is None else str(value)


def _elapsed_flow_s(door: JsonObject, now: str) -> int:
    """截至此刻的累计出水秒数。

    设备的 bathTime 只在暂停那一刻累加本次出水，结束时整体归零，本身不单调，
    必须实时计算：正在出水（inTime 非空）为 bathTime 加 now 减 inTime；暂停中
    就是 bathTime。不这样算，"正在洗浴时直接出门"那一帧取到的会是 0。
    """
    accumulated = as_int(door.get("bath_time_s"))
    in_time = str(door.get("in_time") or "")
    if not in_time:
        return accumulated
    return accumulated + seconds_between(in_time, now)


def _maintain_session(device_id: str, door_no: int, state: DoorState, door: JsonObject, now: str) -> None:
    """维护洗浴会话：边界取自状态的进出，里程碑取自字段。

    四类情形：进入占用态开会话（assigned_at 取设备的 openTime）；占用态内部迁移
    推进里程碑（opened_at 与 flow_s 只增不减）；离开占用态结束会话；与占用无关的
    迁移不涉及会话。
    """
    open_session = repository.get_open_session(device_id, door_no)

    if state not in OCCUPIED_STATES:
        if open_session is not None:
            _close(open_session, door, now)
        return

    card_no = as_int(door.get("card_no"))
    if open_session is None:
        assigned_at = str(door.get("open_time") or "") or now
        session_id = repository.open_session(device_id, door_no, card_no, str(door.get("cert_no") or ""), assigned_at)
    elif as_int(open_session["card_no"]) != card_no:
        # 占用者中途变化：先结旧账再开新账，避免两个人的时长并进同一条记录
        _close(open_session, door, now)
        session_id = repository.open_session(device_id, door_no, card_no, str(door.get("cert_no") or ""), now)
    else:
        session_id = int(open_session["id"])

    repository.touch_session(session_id, str(door.get("in_time") or ""), _elapsed_flow_s(door, now))


def _close(open_session: Row, door: JsonObject, now: str) -> None:
    """结束一次会话：先推进里程碑再收尾。

    结束这一帧本身可能带着新的 inTime（例如刚开阀就出门），不补这一下会让
    opened_at 丢失，那次使用被判成"从没出过水"。不记结束原因：浴室 App 在主动
    出门与超时释放下写同一套清零语句，服务端没有依据区分，硬分就是编造。
    """
    session_id = int(open_session["id"])
    repository.touch_session(session_id, str(door.get("in_time") or ""), _elapsed_flow_s(door, now))
    repository.close_session(session_id, now)
