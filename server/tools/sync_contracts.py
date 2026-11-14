"""把服务端的契约定义同步到另两端。

## 唯一来源

| 内容 | 定义处 |
|---|---|
| 动作名与执行通道 | `app/core/actions.py` |
| 喷头状态取值 | `app/modules/doors/schemas.py` 的 `DoorState` |
| 配置键规格 | `app/modules/config/spec.py` |
| 版本号形状 | `app/modules/ota/service.py` 的 `VERSION_PATTERN` |

Kotlin 与 JavaScript 都不能 import Python，所以另两端拿到的是**生成产物**。
在此之前两端各手写一份、靠测试事后对账——漂移是必然的，只是早晚。

## 生成物入库

不在构建期生成：设备端构建因此不依赖 Python 环境，代码评审也能看见 diff。
"忘了重新生成"由 `tests/test_contract_sync.py` 拦下——它断言磁盘上的文件与
重新生成的结果逐字节相同。

## 用法

    cd server
    python -m tools.sync_contracts          # 校验（不一致则退出码 1）
    python -m tools.sync_contracts --write  # 重新生成
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.actions import ALL_ACTIONS
from app.core.events import DEVICE_UPDATE, DOOR_UPDATE
from app.core.outcomes import ALL as OUTCOME_STATUSES
from app.core.reason_codes import REGISTRY as REASON_CODES
from app.modules.config.spec import (
    BOOL,
    CONFIG_SPECS,
    DEVICE,
    INT,
    TEXT,
    TIME,
    WEEKDAYS,
    ConfigKind,
    ConfigSpec,
)
from app.modules.doors.schemas import DoorState
from app.modules.ota.service import VERSION_PATTERN as OTA_VERSION_PATTERN

REPO_ROOT = Path(__file__).resolve().parents[2]

KOTLIN_ACTIONS = REPO_ROOT / "android/app/src/main/java/com/ryanliu/ntushower/transport/SupportedActions.kt"
KOTLIN_OUTCOMES = REPO_ROOT / "android/app/src/main/java/com/ryanliu/ntushower/transport/OutcomeStatus.kt"
KOTLIN_REASONS = REPO_ROOT / "android/app/src/main/java/com/ryanliu/ntushower/transport/ReasonCodes.kt"
KOTLIN_OTA = REPO_ROOT / "android/app/src/main/java/com/ryanliu/ntushower/transport/OtaContract.kt"
KOTLIN_DEFAULTS = REPO_ROOT / "android/app/src/main/java/com/ryanliu/ntushower/core/config/ConfigDefaults.kt"
WEB_CONTRACT = REPO_ROOT / "server/web/src/api/contract.ts"

#: 生成物都带这一行，避免有人手改后被下一轮生成悄悄覆盖。
GENERATED_MARK = "由 tools/sync_contracts.py 生成"

#: 动作对象的显示名——生成产物是给人读的，分组用中文。
OBJECT_LABELS = {
    "session": "淋浴会话",
    "screen": "设备屏幕",
    "device": "设备自身",
    "bridge": "调试通道",
    "ui": "设备界面（无障碍）",
    "freelist": "免费名单",
    "water": "水控板直控",
}


#: 动作名 → Kotlin 常量名。`session.assign` → `SESSION_ASSIGN`。
def constant_of(action: str) -> str:
    return action.replace(".", "_").upper()


def _kotlin() -> str:
    """设备端的动作清单。

    分组按动作名的对象前缀，顺序与原手写版本一致——生成产物也要能读。
    """
    groups: dict[str, list[str]] = {}
    for action in ALL_ACTIONS:
        groups.setdefault(action.split(".", 1)[0], []).append(action)

    lines = [
        "package com.ryanliu.ntushower.transport",
        "",
        "/**",
        " * 设备支持的远程 action 集合（协议 `capabilities` 字段的唯一来源）。",
        " *",
        " * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被覆盖，",
        " * 而 `tests/test_contract_sync.py` 也会立刻失败。要动动作清单，改服务端的",
        " * `app/core/actions.py` 后重新生成。",
        " *",
        " * 两处用途：",
        " * - `link.hello` 据此向服务端宣告自身能力，服务端在下发前比对（拒绝把设备",
        " *   不认识的命令发过去，而不是等它落到 `proto.unknown_action`）；",
        " * - `AgentService.dispatchCommand` 的分支用这些常量，避免字面量散落两处。",
        " *",
        " * 命名规则 `<对象>.<动作>`：对象是**被操作的东西**，不是代码模块，也不是用途。",
        " */",
        "object SupportedActions {",
    ]

    for obj, actions in groups.items():
        lines.append("")
        lines.append(f"    // ── {OBJECT_LABELS.get(obj, obj)} ──")
        for action in actions:
            lines.append(f'    const val {constant_of(action)} = "{action}"')

    lines.append("")
    lines.append("    /** 全部支持的 action（上报给服务端的 capabilities）。 */")
    lines.append("    val ALL: Set<String> = setOf(")
    for action in ALL_ACTIONS:
        lines.append(f"        {constant_of(action)},")
    lines.append("    )")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _kotlin_config_defaults() -> str:
    """下发给设备的每个配置键的默认值，以及把它们拼成一个对象的入口。

    设备端的 `RuntimeConfig` 需要**一个初值**才能编译——它在拿到服务端下发的配置之前
    得先有个能用的值。此前那些初值是手抄的，与 `spec.py` 的默认值是两份独立的东西：
    两边写得不一致时，设备的行为取决于"配置下发到了没有"，而在启动后的头几百毫秒里
    它一定是没到的那一份。

    只生成 `scope=DEVICE` 的键：SERVER 归属的键根本不下发，设备端也不需要它们。

    `asJson()` 供 `RuntimeConfig.resetToDefaults` 复位用。**它必须生成**：手写一份
    拼装就是第三个可能与登记表漂移的地方（`ConfigCache` 的类注释警告过同一件事）。
    """
    kotlin_type = {INT: "Long", BOOL: "Boolean", TEXT: "String", TIME: "String", WEEKDAYS: "String"}
    device_specs = [spec for spec in CONFIG_SPECS if spec.scope is DEVICE]

    lines = [
        "package com.ryanliu.ntushower.core.config",
        "",
        "import org.json.JSONObject",
        "",
        "/**",
        " * 远程配置的默认值。",
        " *",
        " * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被覆盖，",
        " * 而 `tests/test_contract_sync.py` 也会立刻失败。要改默认值，改服务端的",
        " * `app/modules/config/spec.py` 后重新生成。",
        " *",
        " * **默认值只有这一处**（它的上游是 spec.py）。设备端不另写一份：两份手写的默认值",
        " * 必然在某次改动后分叉，而分叉的表现是「行为取决于配置有没有下发到」——",
        " * 启动后的头几百毫秒里它一定没到，于是表现为偶发的、难以复现的行为差异。",
        " */",
        "object ConfigDefaults {",
    ]
    for index, spec in enumerate(device_specs):
        if index > 0:
            lines.append("")
        lines.append(f"    /** {spec.title} */")
        lines.append(f"    const val {_constant_of_key(spec.key)} = {_kotlin_literal(spec, kotlin_type)}")

    lines.append("")
    lines.append("    /** 全部默认值拼成一个对象，供 `RuntimeConfig.resetToDefaults` 复位用。 */")
    lines.append("    fun asJson(): JSONObject = JSONObject()")
    for spec in device_specs:
        lines.append(f'        .put("{spec.key}", {_constant_of_key(spec.key)})')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _constant_of_key(key: str) -> str:
    """配置键 → Kotlin 常量名。`overlay_message` → `OVERLAY_MESSAGE`。"""
    return key.upper()


def _kotlin_literal(spec: ConfigSpec, kotlin_type: dict[ConfigKind, str]) -> str:
    # 查表失败要说清"是哪个键、缺哪种类型"：裸 KeyError 不指向该补什么，而设备域出现
    # 新 kind（如 TEXT_MAP）时，正是要在这里补映射——下一条断言到不了这一步。
    kind_name = kotlin_type.get(spec.kind)
    if kind_name is None:
        message = f"配置键 {spec.key} 的类型 {spec.kind} 还没有对应的 Kotlin 类型"
        raise AssertionError(message)
    if spec.kind is BOOL:
        return "true" if spec.default else "false"
    if spec.kind is INT:
        return f"{spec.default}L"
    # 文本：转义反斜杠与引号，避免默认值里的字符破坏生成结果
    text = str(spec.default).replace("\\", "\\\\").replace('"', '\\"')
    if kind_name != "String":
        message = f"未预料的配置类型：{spec.kind}"
        raise AssertionError(message)
    return f'"{text}"'


def _kotlin_reason_codes() -> str:
    """设备端要回报的失败原因码。

    生成的是**全部**登记项，而不是只挑设备端用得到的那几个：挑挑拣拣意味着"哪些是
    设备端该用的"这件事又有了一个需要人工维护的清单，而它的失效方式是不报错——
    设备端想回一个码却发现常量不存在，于是随手写个字面量。
    """
    lines = [
        "package com.ryanliu.ntushower.transport",
        "",
        "/**",
        " * 失败原因码（协议 `session.command_result.reason_code` 的取值）。",
        " *",
        " * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被覆盖，",
        " * 而 `tests/test_contract_sync.py` 也会立刻失败。要动取值或文案，改服务端的",
        " * `app/core/reason_codes.py` 后重新生成。",
        " *",
        " * 设备端回报时**必须**用这里的常量：服务端的 `tests/test_reason_codes.py` 会解析",
        " * 本目录下的 Kotlin 源码，出现未登记的码就失败。",
        " */",
        "object ReasonCodes {",
    ]
    for index, reason in enumerate(REASON_CODES):
        if index > 0:
            lines.append("")
        lines.append(f"    /** {reason.message} */")
        lines.append(f'    const val {_constant_of_reason(reason.code)} = "{reason.code}"')
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _constant_of_reason(code: str) -> str:
    """原因码 → Kotlin 常量名。`command.deadline_exceeded` → `COMMAND_DEADLINE_EXCEEDED`。"""
    return code.replace(".", "_").upper()


def _kotlin_outcomes() -> str:
    """设备端的结果状态常量。

    设备端此前在 `CommandOutcome(id, "ok", ...)` 里写裸字面量，与服务端的登记表是
    两份独立的东西——协议 schema 写 `blocked`、服务端发 `rejected` 而设备端不知道
    这个值存在，三处对不上且没有测试会发现。生成之后它们在编译期就是同一组。
    """
    lines = [
        "package com.ryanliu.ntushower.transport",
        "",
        "/**",
        " * 命令结果状态（协议 `session.command_result.status` 的取值）。",
        " *",
        " * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被覆盖，",
        " * 而 `tests/test_contract_sync.py` 也会立刻失败。要动取值，改服务端的",
        " * `app/core/outcomes.py` 后重新生成。",
        " *",
        " * 四者的分界（判断该回哪个时照这个来）：",
        " * - [OK]        做完了",
        " * - [FAILED]    动手了但没成",
        " * - [TIMEOUT]   超过时限没有结论，**不知道成没成**",
        " * - [REJECTED]  根本没动手，没有副作用，**重试是安全的**",
        " */",
        "object OutcomeStatus {",
    ]
    for index, item in enumerate(OUTCOME_STATUSES):
        if index > 0:
            lines.append("")
        lines.append(f"    /** {item.label} */")
        lines.append(f'    const val {item.value.upper()} = "{item.value}"')
    lines.append("")
    lines.append("    /** 全部取值。 */")
    lines.append("    val ALL: Set<String> = setOf(")
    for item in OUTCOME_STATUSES:
        lines.append(f"        {item.value.upper()},")
    lines.append("    )")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _kotlin_ota() -> str:
    """设备端校验版本号用的形状。

    版本号由服务端的发布流程铸造、拼进文件路径（见 ota/service.py 顶部的安全注释）。
    设备端下载前按同一形状校验 manifest 的 `version` 字段——此前设备端手抄了一份正则、
    靠注释提醒两边保持一致，漂移是静默的；生成之后形状只有这一个来源。
    """
    # Kotlin 字符串字面量：转义反斜杠与引号（正则里的 \d 落进字面量是 \\d）
    pattern = OTA_VERSION_PATTERN.replace("\\", "\\\\").replace('"', '\\"')
    lines = [
        "package com.ryanliu.ntushower.transport",
        "",
        "/**",
        " * OTA 版本号的形状：服务端发布时铸造、拼进文件路径，设备端下载前按它校验",
        " * manifest 的 `version` 字段（版本号参与文件名构造，先校验形状）。",
        " *",
        " * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被",
        " * 覆盖，而 `tests/test_contract_sync.py` 也会立刻失败。要动版本号形状，改服务端的",
        " * `app/modules/ota/service.py` 后重新生成。",
        " */",
        "object OtaContract {",
        f'    const val VERSION_PATTERN = "{pattern}"',
        "}",
        "",
    ]
    return "\n".join(lines)


def _javascript() -> str:
    """网页端要用的契约常量。

    只暴露它**真的会用到**的：动作名网页端不消费（它走 HTTP 路径），状态取值要用——
    网页端的状态文案表以这些值为键，键写错就静默显示成原始英文，不报错。
    """
    states = [state.value for state in DoorState]
    # 设备输入能力的两组闭集，直接取服务端 Literal 的取值——网页端的按钮清单此前是手抄的：
    # 服务端删一个取值，按钮还在，点了得到 422 而页面看不出为什么。
    #
    # 只生成网页端**真用得上的**：闭集多的是，没被页面用到的（与 CHANNELS 同一个道理）
    # 不生成——没人 import 的副本只会各自漂移。
    from typing import get_args

    from app.modules.devices.schemas import DeviceUiKeyRequest, DeviceUiTextRequest

    key_names = get_args(DeviceUiKeyRequest.model_fields["key"].annotation)
    input_channels = get_args(DeviceUiTextRequest.model_fields["channel"].annotation)
    lines = [
        "/**",
        f" * 跨端契约常量。**本文件{GENERATED_MARK}，不要手改**。",
        " *",
        " * 来源：服务端的 `core/actions.py`、`doors/schemas.py`、`modules/config/spec.py`。",
        " * 重新生成：`cd server && python -m tools.sync_contracts --write`。",
        " */",
        "",
        "/** 喷头的七种状态取值（与服务端 `DoorState` 一致）。 */",
        "export const DOOR_STATES = Object.freeze([",
    ]
    lines.extend(f"  '{state}'," for state in states)
    lines.append("])")
    lines.append("")
    lines.append("/** 命令结果状态 → 中文名。取值与中文都取自服务端的 core/outcomes.py。 */")
    lines.append("export const OUTCOME_LABELS = Object.freeze({")
    for item in OUTCOME_STATUSES:
        lines.append(f"  '{item.value}': '{item.label}',")
    lines.append("})")
    lines.append("")
    lines.append("/** 推送事件类型。取值取自服务端的 core/events.py。 */")
    lines.append("export const EVENT_TYPES = Object.freeze({")
    lines.append(f"  DEVICE_UPDATE: '{DEVICE_UPDATE}',")
    lines.append(f"  DOOR_UPDATE: '{DOOR_UPDATE}',")
    lines.append("})")
    lines.append("")
    lines.append("/** 系统按键（与服务端 `DeviceUiKeyRequest.key` 一致）。 */")
    lines.append("export const UI_KEYS = Object.freeze([")
    lines.extend(f"  '{key}'," for key in key_names)
    lines.append("])")
    lines.append("")
    lines.append("/** 文本注入的两条通路（clipboard 支持中文；keys 仅 ASCII）。 */")
    lines.append("export const INPUT_CHANNELS = Object.freeze([")
    lines.extend(f"  '{channel}'," for channel in input_channels)
    lines.append("])")
    lines.append("")
    return "\n".join(lines)


def artifacts() -> dict[Path, str]:
    """全部生成产物：路径 → 内容。

    统一在这里收尾：去掉尾部空行再补一个换行。生成函数逐条 append 时会在最后一条
    后面也留一个空行，而 Kotlin 的 `}` 前不容空行（detekt 的 NoBlankLineBeforeRbrace）。
    """
    raw = {
        KOTLIN_ACTIONS: _kotlin(),
        KOTLIN_OUTCOMES: _kotlin_outcomes(),
        KOTLIN_REASONS: _kotlin_reason_codes(),
        KOTLIN_OTA: _kotlin_ota(),
        KOTLIN_DEFAULTS: _kotlin_config_defaults(),
        WEB_CONTRACT: _javascript(),
    }
    return {path: content.rstrip() + "\n" for path, content in raw.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="写入生成结果（默认只校验）")
    args = parser.parse_args()

    stale: list[Path] = []
    for path, content in artifacts().items():
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            print(f"written: {path.relative_to(REPO_ROOT)}")
        elif not path.is_file() or path.read_text(encoding="utf-8") != content:
            stale.append(path)

    if stale:
        for path in stale:
            print(f"stale: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        print("重新生成：cd server && python -m tools.sync_contracts --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
