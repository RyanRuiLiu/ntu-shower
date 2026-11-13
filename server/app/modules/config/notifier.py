"""配置变更的下游通知槽：写侧只发通知，行为侧在组合根注册。

sessions 的定时器要读 config.store 取阈值与名单，若 config 写路径反过来直接
import sessions，就是包级循环（活不活全看 main.py 的导入顺序）。所以下游行为
（定时器重算、设备推送）实现为订阅者，由 main.py 装配时注册到这里——依赖倒置：
config 不知道谁在听，只知道「保存后要喊一声」。
"""

from collections.abc import Sequence
from typing import Protocol

from app.modules.config.schemas import ConfigPushNotice


class ConfigChangeSubscriber(Protocol):
    """配置保存后的下游行为：重算定时器，并把新配置推给 device_ids 里的设备。

    device_ids 为空表示这次改动不涉及设备域（纯服务端键），只需重算定时器。
    """

    async def __call__(self, revision: int, device_ids: Sequence[str]) -> list[ConfigPushNotice]: ...


_subscriber: ConfigChangeSubscriber | None = None


def register(subscriber: ConfigChangeSubscriber) -> None:
    """组合根（main.py）装配时调用；重复注册以最后一次为准。"""
    global _subscriber
    _subscriber = subscriber


async def notify(revision: int, device_ids: Sequence[str]) -> list[ConfigPushNotice]:
    """配置已保存：唤起订阅者。未注册（如单元测试直接构造 routes）时是空操作。"""
    if _subscriber is None:
        return []
    return await _subscriber(revision, device_ids)
