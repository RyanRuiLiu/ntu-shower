"""把服务端的契约定义同步到另两端。

## 唯一来源

| 内容 | 定义处 |
|---|---|
| 动作名与执行通道 | `app/core/actions.py` |
| 喷头状态取值 | `app/modules/doors/schemas.py` 的 `DoorState` |
| 配置键规格 | `app/modules/config/spec.py` |

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

from app.core.actions import ACTION_CHANNELS, ALL_ACTIONS
from app.modules.doors.schemas import DoorState

REPO_ROOT = Path(__file__).resolve().parents[2]

KOTLIN_ACTIONS = REPO_ROOT / "android/app/src/main/java/com/ryanliu/ntushower/transport/SupportedActions.kt"
WEB_CONTRACT = REPO_ROOT / "server/web/core/contract.js"

#: 生成物都带这一行，避免有人手改后被下一轮生成悄悄覆盖。
GENERATED_MARK = "由 tools/sync_contracts.py 生成"

#: 动作对象的显示名——生成产物是给人读的，分组用中文。
OBJECT_LABELS = {
    "session": "淋浴会话",
    "screen": "设备屏幕",
    "device": "设备自身",
    "bridge": "调试通道",
    "ui": "设备界面（无障碍）",
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


def _javascript() -> str:
    """网页端要用的契约常量。

    只暴露它**真的会用到**的：动作名网页端不消费（它走 HTTP 路径），状态取值要用——
    网页端的状态文案表以这些值为键，键写错就静默显示成原始英文，不报错。
    """
    states = [state.value for state in DoorState]
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
    lines.append("/** 执行通道：同通道内命令串行，跨通道并行。 */")
    lines.append("export const CHANNELS = Object.freeze({")
    for action, channel in ACTION_CHANNELS.items():
        lines.append(f"  '{action}': '{channel}',")
    lines.append("})")
    lines.append("")
    return "\n".join(lines)


def artifacts() -> dict[Path, str]:
    """全部生成产物：路径 → 内容。"""
    return {
        KOTLIN_ACTIONS: _kotlin(),
        WEB_CONTRACT: _javascript(),
    }


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
