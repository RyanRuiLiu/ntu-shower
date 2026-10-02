"""设备领域服务：档案查询、在线状态并入记录、device.update 事件的唯一发布点。

在线只有 core/connections 的连接表一个来源，档案表里没有这个列；每条返回给
调用方的记录都在这里合并一次，online 必定反映此刻的真实连接。

device.update 的 payload 是精简档案（DeviceSummary）：版本、来源 IP、时钟偏差
等运维诊断字段不进公开推送，管理台要详情走 REST。发布只经 [publish_device_update]
一处，网关生命周期与档案修改共用。
"""

from app.core import device_clock, events
from app.core.clock import now_rfc3339
from app.core.connections import manager
from app.core.protocol import as_int
from app.modules.config import store as config_store

from . import repository
from .schemas import DeviceRecord, DeviceSummary


def get_device(device_id: str) -> DeviceRecord | None:
    record = repository.get(device_id)
    if record is None:
        return None
    record["online"] = manager.is_online(device_id)
    return record


def list_devices() -> list[DeviceRecord]:
    records = repository.list_all()
    for record in records:
        record["online"] = manager.is_online(record["device_id"])
    return records


def to_summary(record: DeviceRecord) -> DeviceSummary:
    """完整档案裁剪为精简视图（字段收口于此，用户面与推送共用）。"""
    return {"device_id": record["device_id"], "name": record["name"], "online": record["online"]}


def publish_device_update(device_id: str) -> None:
    """发布一次 device.update（精简载荷）；档案不存在的设备推最小占位。"""
    record = get_device(device_id)
    if record is not None:
        payload: dict[str, object] = dict(to_summary(record))
    else:
        payload = {"device_id": device_id, "name": "", "online": False}
    payload["at"] = now_rfc3339()
    events.publish(events.DEVICE_UPDATE, payload)


def register_device(
    device_id: str,
    app_version: str,
    version_code: int,
    os_version: str,
    capabilities: list[str],
    ip: str,
    booted_at: str,
) -> None:
    repository.register(device_id, app_version, version_code, os_version, capabilities, ip, booted_at)


def touch_seen(device_id: str) -> None:
    """记下刚收到该设备的一帧（心跳粒度，见 repository.touch_seen）。"""
    repository.touch_seen(device_id)


def set_device_offline(device_id: str) -> None:
    """断开收尾：刷新"最后见到"时刻（列的语义是"最后见到"，不分心跳还是断开）。"""
    repository.touch_seen(device_id)


def record_link_event(device_id: str, event: str) -> None:
    """记一次上下线（event 取 schemas 的 LINK_ONLINE / LINK_OFFLINE）。

    与 [set_device_offline] 分工不同：那个刷新档案上的"最后见到"一列，只记得住最后一次；
    这个留下一串历史，回答"这台设备什么时候在线过"——它是设备利用率的分母，也是
    "这段时间为什么没有数据"唯一能给出的答案。
    """
    repository.insert_link_event(device_id, event, now_rfc3339())


def load_clock_skew(device_id: str) -> None:
    """把持久化的钟偏差载入内存。设备上线时调一次。

    不载入的话，从上线到首次 pong 之间偏差读出来是 0，等于这段时间不做平移——而
    "我们刚接手、那边已经在洗"恰恰最常发生在这个窗口里。
    """
    device_clock.load(device_id, repository.get_clock_skew(device_id))


def update_clock_skew(device_id: str, device_time: object, received_at: str) -> None:
    """用设备回 pong 时带的墙上时刻更新钟偏差，并落库。

    阈值取自配置：单次观测与当前值差得超过它，说明设备钟被动了（重启归零、被手动改
    钟），直接采信新值而不平滑——继续平滑会让之后几次映射都按错误的偏差算。
    """
    threshold = as_int(config_store.effective(device_id).get("clock_jump_threshold_s"), default=60)
    skew = device_clock.update(device_id, device_time, received_at, threshold)
    repository.set_clock_skew(device_id, skew)


def rename_device(device_id: str, name: str) -> bool:
    return repository.set_name(device_id, name)


def set_trusted(device_id: str, trusted: bool) -> bool:
    return repository.set_trusted(device_id, trusted)


def is_online(device_id: str) -> bool:
    return manager.is_online(device_id)
