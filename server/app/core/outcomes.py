"""命令结果的总体状态：session.command_result.status 的唯一来源。

| 取值 | 含义 |
|---|---|
| ok | 做完了 |
| failed | 动手了但没成 |
| timeout | 超过时限，没有结论：设备可能还在跑，也可能已卡死 |
| rejected | 前置条件不满足，未产生副作用，重试安全 |

timeout 与 failed 的分界是有没有结论；rejected 与 failed 的分界是有没有副作用。
取值是跨端契约，协议 schema 的 enum 与之对齐，由 tests/test_outcome_contract.py
对账。

terminal_message 是给人看的一句话：失败时服务端经 reason_codes.describe() 补齐，
保证非空；成功时设备有值得转达的内容才填（如浴室 App 界面显示的原文）。分支
判定用 status 与 reason_code，不用它。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OutcomeStatus:
    """一种命令结果状态：稳定取值与中文名。"""

    value: str
    label: str


OK = OutcomeStatus("ok", "成功")
FAILED = OutcomeStatus("failed", "失败")
TIMEOUT = OutcomeStatus("timeout", "超时")
REJECTED = OutcomeStatus("rejected", "被拒")

#: 全部取值，顺序即展示优先级。
ALL: tuple[OutcomeStatus, ...] = (OK, FAILED, TIMEOUT, REJECTED)

#: 取值集合，供校验与对账。
VALUES: frozenset[str] = frozenset(item.value for item in ALL)


#: 各动作在成功时返回的 data：动作 → {字段名: 含义}。
#:
#: 字段名是两端共同约定，由 tests/test_outcome_contract.py 解析设备端源码对账；
#: 写错一个键不报错，只会让前端取到空值。未登记的动作表示成功时不回 data。
ACTION_DATA: dict[str, dict[str, str]] = {
    "camera.capture": {"image_base64": "JPEG 图像的 Base64 编码"},
    "screen.capture": {"image_base64": "JPEG 图像的 Base64 编码"},
    "ui.dump": {"xml": "节点树，格式与 uiautomator dump 一致"},
    "device.exec": {
        "output": "命令的输出（stdout 与 stderr 合并）",
        "exit_code": "进程退出码；命令超时或 shell 不可用时为 null",
    },
    "freelist.read": {
        "content": "免费名单文件的原文，含行尾",
    },
}
