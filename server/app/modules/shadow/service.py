"""设备影子读模型：统一聚合 reported 与 desired，供 API 与扩展功能读写。

- reported：设备最后上报的状态（devices 档案 + door_live 现态）；
- desired：服务端期望的设备运行配置（合并后的有效配置）；
- metadata：影子文档本身的说明信息。
"""

from app.core.clock import now_rfc3339
from app.core.db import get_conn
from app.modules.config import store as config_store


def _device_row(device_id: str) -> dict | None:
    row = (
        get_conn()
        .execute(
            "SELECT device_id, name, trusted, app_version, version_code, os_version,"
            " online, last_seen_at FROM devices WHERE device_id = ?",
            (device_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def _doors_reported(device_id: str) -> list[dict]:
    rows = (
        get_conn()
        .execute(
            "SELECT door_no, state, card_no, cert_no, acc_no, open_time, in_time,"
            " close_time, bath_time_s, status_raw, reported_at"
            " FROM door_live WHERE device_id = ? ORDER BY door_no",
            (device_id,),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def get_reported(device_id: str) -> dict | None:
    """reported：设备最后上报的状态（含在线标志）。"""
    device = _device_row(device_id)
    if device is None:
        return None
    return {"device": device, "doors": _doors_reported(device_id)}


def get_desired(device_id: str) -> dict:
    """desired：服务端期望的设备运行配置。"""
    return config_store.effective(device_id)


def get_shadow(device_id: str) -> dict | None:
    """完整影子文档（管理面）：reported + desired + metadata。"""
    reported = get_reported(device_id)
    if reported is None:
        return None
    global_revision, _global_config = config_store.get_global()
    override = config_store.get_device_override(device_id)
    desired_revision = override[0] if override else global_revision
    return {
        "reported": reported,
        "desired": {"revision": desired_revision, "config": get_desired(device_id)},
        "metadata": {"at": now_rfc3339(), "source": "door_live + device_config"},
    }
