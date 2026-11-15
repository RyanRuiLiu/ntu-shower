"""配置变更推送到设备：config.notifier 的订阅者（组合根注册）。

推送是设备平面的行为（下发 config.update 并等确认），归属设备域；config 写侧
只发通知、不知道谁在听（见 config/notifier.py 的依赖倒置）。与 sessions 的
定时器重载是两个订阅者、两个职责：删掉其中任何一个模块，另一个行为照常工作。
"""

import asyncio
from collections.abc import Sequence

from app.db.session import session_scope
from app.modules.config import store as config_store
from app.modules.config.schemas import ConfigPushNotice

from . import actions as device_actions


async def on_config_change(revision: int, device_ids: Sequence[str]) -> list[ConfigPushNotice]:
    """配置保存后把新的有效配置推给 device_ids 里的在线设备，逐台并发。

    device_ids 为空表示这次改动不涉及设备域的键（纯服务端键），无需惊动设备。
    单台慢设备不拖慢整批；每台各自回报 notified/applied/reason。
    """
    if not device_ids:
        return []

    async def _push(device_id: str) -> ConfigPushNotice:
        with session_scope() as session:
            values = config_store.device_effective(session, device_id)
        result = await device_actions.push_config_update(device_id, revision, values)
        notice: ConfigPushNotice = {
            "device_id": device_id,
            "notified": bool(result.get("notified", False)),
            "applied": bool(result.get("applied", False)),
        }
        reason = result.get("reason")
        if reason:
            notice["reason"] = str(reason)
        return notice

    return list(await asyncio.gather(*(_push(device_id) for device_id in device_ids)))


__all__ = ["on_config_change"]
