"""设备领域服务：档案查询、在线状态并入记录、device.update 事件的唯一发布点。

在线只有 core/connections 的连接表一个来源，档案表里没有这个列；每条返回给
调用方的记录都在这里合并一次，online 必定反映此刻的真实连接。

device.update 的 payload 是精简档案（DeviceSummary）：版本、来源 IP、时钟偏差
等运维诊断字段不进公开推送，管理台要详情走 REST。发布只经 [publish_device_update]
一处，网关生命周期与档案修改共用。

会话来源（全库同一约定）：路由层 `Depends(get_db)`，WS 与后台任务由调用方开
短生命周期会话传入；本层不自己开库。
"""

from sqlalchemy.orm import Session

from app.contracts import DeviceLogKind, LinkEvent
from app.core import device_clock, events
from app.core.clock import now_rfc3339, parse_rfc3339
from app.core.connections import manager
from app.core.problems import DEVICE_NOT_FOUND, ApiProblemError
from app.db.base import utcnow

from . import crud
from .schemas import DeviceRecord, DeviceSummary


def get_device(session: Session, device_id: str) -> DeviceRecord | None:
    record = crud.get(session, device_id)
    if record is None:
        return None
    record["online"] = manager.is_online(device_id)
    return record


def list_devices(session: Session) -> list[DeviceRecord]:
    records = crud.list_all(session)
    for record in records:
        record["online"] = manager.is_online(record["device_id"])
    return records


def set_order(session: Session, device_ids: list[str]) -> None:
    """按给定顺序重排设备（管理员手动调整，全部设备清单的唯一顺序来源）。

    清单里出现未知设备（已拆除的、拼错的）整体拒绝：半排序比不排序更难看。
    """
    known = {str(record["device_id"]) for record in crud.list_all(session)}
    unknown = [item for item in device_ids if item not in known]
    if unknown:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {unknown[0]}")
    crud.set_order(session, device_ids)


def to_summary(record: DeviceRecord) -> DeviceSummary:
    """完整档案裁剪为精简视图（字段收口于此，用户面与推送共用）。"""
    return {
        "device_id": record["device_id"],
        "name": record["name"],
        "online": record["online"],
        "retired": record["retired"],
    }


def publish_device_update(session: Session, device_id: str) -> None:
    """发布一次 device.update（精简载荷）；档案不存在的设备推最小占位。"""
    record = get_device(session, device_id)
    if record is not None:
        payload: dict[str, object] = dict(to_summary(record))
    else:
        payload = {"device_id": device_id, "name": "", "online": False, "retired": True}
    payload["at"] = now_rfc3339()
    events.publish(events.DEVICE_UPDATE, payload)


def register_device(
    session: Session,
    device_id: str,
    app_version: str,
    version_code: int,
    os_version: str,
    capabilities: list[str],
    ip: str,
    booted_at: str,
) -> None:
    crud.register(
        session,
        device_id=device_id,
        app_version=app_version,
        version_code=version_code,
        os_version=os_version,
        capabilities=capabilities,
        ip=ip,
        booted_at=parse_rfc3339(booted_at) or utcnow(),
        seen_at=utcnow(),
    )


def touch_seen(session: Session, device_id: str) -> None:
    """记下刚收到该设备的一帧（心跳粒度，见 crud.touch_seen）。"""
    crud.touch_seen(session, device_id, utcnow())


def set_device_offline(session: Session, device_id: str) -> None:
    """断开收尾：刷新"最后见到"时刻（列的语义是"最后见到"，不分心跳还是断开）。"""
    crud.touch_seen(session, device_id, utcnow())


def record_link_event(session: Session, device_id: str, event: str, reason: str = "") -> None:
    """记一次上下线（event 取 schemas 的 LINK_ONLINE / LINK_OFFLINE）。

    与 [set_device_offline] 分工不同：那个刷新档案上的"最后见到"一列，只记得住最后一次；
    这个留下一串历史，回答"这台设备什么时候在线过"——它是设备利用率的分母，也是
    "这段时间为什么没有数据"唯一能给出的答案。
    """
    crud.insert_link_event(session, device_id, LinkEvent(event), utcnow(), reason)


def record_log_upload(session: Session, device_id: str, kind: str, content: str) -> None:
    """落一份设备日志上送（见 crud.append_device_log 的保留规则）。"""
    crud.append_device_log(session, device_id, DeviceLogKind(kind), content, utcnow())


def record_status_report(
    session: Session,
    device_id: str,
    auto_close_time: str,
    auto_open_time: str,
    zone_offset_minutes: int,
) -> bool:
    """落一份 device.status 上报事实（开关机计划与时区）。设备不在册返回 False。"""
    return crud.set_power_facts(session, device_id, auto_close_time, auto_open_time, zone_offset_minutes)


def power_facts(session: Session, device_id: str) -> tuple[str, str, int] | None:
    """上报事实的唯一消费入口：hold 对账、voice 当地时刻、设备页展示共用。"""
    return crud.power_facts(session, device_id)


def latest_device_log(session: Session, device_id: str, kind: str) -> dict[str, str] | None:
    """最新的日志上送（设备页查看器）；没有则 None。"""
    return crud.latest_device_log(session, device_id, DeviceLogKind(kind))


def load_clock_skew(session: Session, device_id: str) -> None:
    """把持久化的钟偏差载入内存。设备上线时调一次。

    不载入的话，从上线到首次 pong 之间偏差读出来是 0，等于这段时间不做平移——而
    "我们刚接手、那边还被占着"恰恰最常发生在这个窗口里。
    """
    device_clock.load(device_id, crud.get_clock_skew(session, device_id))


#: 设备钟跳变阈值（秒）：单次观测与当前值的差超过它，说明设备钟被动了（重启归零、
#: 被手动改钟），直接采信新值而不平滑——继续平滑会让之后几次映射都按错误的偏差算。
#: 曾是配置键（运维从未改过，2026-11-17 精简收编为常量）。
CLOCK_JUMP_THRESHOLD_S = 60


def update_clock_skew(session: Session, device_id: str, device_time: object, received_at: str) -> None:
    """用设备回 pong 时带的墙上时刻更新钟偏差，并落库。"""
    threshold = CLOCK_JUMP_THRESHOLD_S
    skew = device_clock.update(device_id, device_time, received_at, threshold)
    crud.set_clock_skew(session, device_id, skew)


def rename_device(session: Session, device_id: str, name: str) -> bool:
    return crud.set_name(session, device_id, name)


def set_trusted(session: Session, device_id: str, trusted: bool) -> bool:
    """改信任位（只落库）。

    **信任只在那条连接的 hello 握手时判定一次**：库里这个位变了，已经建立的连接不会
    跟着变——新标记的设备若恰好在线，它的业务帧与命令结果会被当作未信任设备静默丢弃，
    表现是「设备在线、功能全坏」（2026-10-06 首台新设备入网实测）。所以调用方（信任
    路由）改完必须**断开现有连接**让设备重新握手，见 `routes.patch_device`。
    """
    return crud.set_trusted(session, device_id, trusted)


def set_retired(session: Session, device_id: str, retired: bool) -> bool:
    """停用/恢复（只落库）。

    执行语义在网关与自注册：握手与 enroll 即刻拒绝。调用方改完必须断开现有
    连接——在线设备的业务帧不会因这个位自动停下（与信任位同一处置）。
    """
    return crud.set_retired(session, device_id, retired)


def is_online(device_id: str) -> bool:
    return manager.is_online(device_id)
