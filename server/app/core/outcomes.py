"""命令结果的总体状态（`session.command_result.status` 的唯一来源）。

四个取值，服务端与设备端共用：

| 取值 | 含义 | 判据 |
|---|---|---|
| `ok` | 做完了 | 达到了目的 |
| `failed` | 动手了但没成 | 有明确的失败原因 |
| `timeout` | 超过时限，没有结论 | **不知道成没成**——设备可能还在跑，也可能已经卡死 |
| `rejected` | 根本没动手 | 前置条件不满足，什么都没发生 |

分界在哪：`timeout` 与 `failed` 的区别是**有没有动手**——前者是"没等到结论"，后者是
"有结论，结论是没成"。`rejected` 与 `failed` 的区别是**有没有产生副作用**——被拒的
命令不会改动任何状态，重试是安全的；`failed` 的命令可能已经改了一半。

## 为什么要有这张表

这四个词是跨端契约：服务端按它们决定审计口径与能否重试，设备端按它们回报。在此之前
它们只是散落的字符串字面量，于是协议 schema 写着 `blocked`、服务端实际发 `rejected`、
设备端只发 `ok` 和 `failed`——**三处对不上，而且没有任何测试会发现**。定义在没人用的
那一侧，用的那一侧没定义。

## `terminal_message` 的约定

它是**给用户看的那句话**，规则三条：

1. **失败时必须非空**。服务端在出口处用 `reason_codes.describe()` 补齐，所以客户端
   拿到的一定有内容可显示；
2. **成功时可以为空**。设备有值得转达的内容就填——`session.assign` 填的是浴室 App
   界面上显示的原文（"请使用1号喷头 / 当前卡余额：51.77元"），那是现场那块屏正在给
   用户看的东西，原样透传；没有可说的就留空，客户端自行显示"已完成"；
3. **它是给人看的，不是给机器分支的**。要分支请用 `status` + `reason_code`。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OutcomeStatus:
    """一种命令结果状态：稳定取值 + 中文名。"""

    value: str
    label: str


OK = OutcomeStatus("ok", "成功")
FAILED = OutcomeStatus("failed", "失败")
TIMEOUT = OutcomeStatus("timeout", "超时")
REJECTED = OutcomeStatus("rejected", "被拒")

#: 全部取值。顺序即展示优先级。
#:
#: `protocol/schema/session.command_result.json` 的 enum 与之一致，由
#: `tests/test_outcome_contract.py` 对账——那份 schema 曾经还留着无人使用的 `blocked`。
ALL: tuple[OutcomeStatus, ...] = (OK, FAILED, TIMEOUT, REJECTED)

#: 取值集合，供校验与对账。
VALUES: frozenset[str] = frozenset(item.value for item in ALL)


#: 各动作在成功时返回的 `data`：动作 → {字段名: 含义}。
#:
#: 登记而不生成。设备端那边要写的是 `JSONObject().put("...", value)`，从中生成 Kotlin
#: 的收益不抵复杂度；但**字段名是两端共同的约定**，由 `test_outcome_contract.py` 解析
#: 设备端源码来对账——写错一个键不会报错，只会让前端取到空值。
#:
#: 没有登记的动作表示成功时**不回 `data`**（`ui.touch`、`session.assign` 这类只回报
#: 成没成的）。
ACTION_DATA: dict[str, dict[str, str]] = {
    "camera.capture": {"image_base64": "JPEG 图像的 Base64 编码"},
    "screen.capture": {"image_base64": "JPEG 图像的 Base64 编码"},
    "ui.dump": {"xml": "节点树，格式与 uiautomator dump 一致"},
    "device.exec": {
        "output": "命令的输出（stdout 与 stderr 合并）",
        "exit_code": "进程退出码；命令超时或 shell 不可用时为 null",
    },
}
