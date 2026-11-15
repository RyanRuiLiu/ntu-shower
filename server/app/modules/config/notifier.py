"""配置变更的下游通知槽：写侧只发通知，行为侧在组合根注册。

sessions 的定时器要读 config.store 取阈值与名单，若 config 写路径反过来直接
import sessions，就是包级循环（活不活全看 main.py 的导入顺序）。所以下游行为
（定时器重算、设备推送）实现为订阅者，由 main.py 装配时注册到这里——依赖倒置：
config 不知道谁在听，只知道「保存后要喊一声」。订阅者是多个、各司一职
（devices.push 推送设备、sessions.scheduler 重载定时器），删掉任何一个不影响
其余。
"""

from collections.abc import Sequence
from typing import Protocol

from app.modules.config.schemas import ConfigPushNotice


class ConfigChangeSubscriber(Protocol):
    """配置保存后的一个下游行为；device_ids 是这次改动涉及设备域键的目标清单。

    参数按位置约定（positional-only）：实现方自选形参名，协议只约束顺序与类型。
    """

    async def __call__(self, revision: int, device_ids: Sequence[str], /) -> list[ConfigPushNotice]: ...


_subscribers: list[ConfigChangeSubscriber] = []


def register(subscriber: ConfigChangeSubscriber) -> None:
    """组合根（main.py）装配时调用；同一订阅者重复注册只记一次。"""
    if subscriber not in _subscribers:
        _subscribers.append(subscriber)


async def notify(revision: int, device_ids: Sequence[str]) -> list[ConfigPushNotice]:
    """配置已保存：唤起全部订阅者。未注册（如单元测试直接构造 routes）时是空操作。"""
    notices: list[ConfigPushNotice] = []
    for subscriber in _subscribers:
        notices.extend(await subscriber(revision, device_ids))
    return notices
