"""设备命令动作名 —— 服务端与"能力怎么用"的唯一来源。

改一个动作时，改的就是这个文件；参数与约束写在同一个地方，
不需要再去别处查一份可能已经过期的说明。

## 命名规则

`<对象>.<动作>`。**对象是"被操作的东西"，不是代码模块，也不是用途**：

- `session.*` —— 一次淋浴使用（分配 / 结算 / 拍照）
- `screen.*`  —— 设备的屏幕
- `device.*`  —— 设备自身（执行命令 / 重启 / 卸载）
- `bridge.*`  —— 调试通道
- `ui.*`      —— 设备的界面（读节点树 / 触摸 / 按键 / 输入）

反例（历史遗留，已改）：`debug.exec` 里的 `debug` 是**用途**不是对象，
同一个能力在 HTTP 侧却叫 `/devices/{id}/exec`（device 域）——一个能力两个名字。
`tests/test_action_names.py` 有专门一条挡住同类命名再次出现。

HTTP 路径遵循同一套词：集合用复数无动词（`/devices`），对单个资源施加动作用
`<资源>/<动词>`（`/devices/{id}/exec`）；改档案用 `PATCH /devices/{id}` 带字段。

## 四条通路，各自的判据

同样"能让设备做事"，但有四条路。**选错不会报错，只是效率与可靠性都差**。

| 通路 | 本质 | 用它的时候 |
|---|---|---|
| `ui.*` | 无障碍服务，Android 正规接口 | 操作界面：读节点树、触摸、按键、输入 |
| `session.*` | 无障碍服务驱动的固定流程 | 业务操作：分配、结算、拍照 |
| `device.exec` | 设备端常驻 root shell（走 WSS） | 常规单条命令 |
| `bridge.*` + ADB | 真实 ADB 协议（走 443 隧道） | 只有 ADB 客户端工具能做的事 |

**判据：这件事有没有正规接口？** 能 `ui.touch` 就别用 `exec input tap`——后者
每次 spawn 一个进程，且部分 ROM 对 shell 输入命令有限制。能 `bridge` 就别用
`exec`——`adb install`、`push/pull`、`logcat` 流需要 ADB 协议本身。
`ui.*` 与 `device.exec` 是两条同权通路：前者用无障碍 API，后者用 root shell，
调用方按 `via` 参数选（见下）。

## 两条通道怎么选（`ui.*`）

无障碍与 root shell 两条后端**各自完整**，由设备端按**无障碍是否可用**选路，
**不是请求参数**——调用方无需也无法指定：

| 动作 | 默认 | 无障碍不可用时 |
|---|---|---|
| `ui.dump` | 无障碍 | **无替代**：如实失败 |
| `ui.touch` 点击 | 无障碍 | `input tap` |
| `ui.touch` 滑动 | root shell | 同左 |
| `ui.scroll` | 无障碍 | 一次竖直滑动 |
| `ui.key` | 无障碍 | `input keyevent` |
| `ui.input` | 无障碍 | `input text`（仅 ASCII） |

`ui.touch` 的滑动一直走 shell：`dispatchGesture` 需 API 24，本设备是 19，
无障碍**做不到**合成任意坐标手势——这是运行时下限，不是取舍。

`ui.dump` 没有第二条通道：shell 侧的 `uiautomator dump` 会经 `UiAutomation`
接管系统无障碍会话，真机上出现过用它取过几次节点后整机无障碍失效的情况
（因果未坐实，但风险远大于便利），因此不采用。

无障碍**整体**失效是真机上发生过的事（总开关被系统置 0 且拒绝写回，恢复办法见
`android/.../features/selfmanage/A11yGuard.kt`）。那时若没有 shell 那条，`ui.*`
会全部失败，远程就完全失去了操作设备界面的手段。

## API 19 的硬约束

设备 ROM 冻结在 Android 4.4。这不是"可选支持"而是运行时下限：

| 能力 | 需要 API | 本设备 |
|---|---|---|
| `performAction` / `getBoundsInScreen` | 14 | 可用 |
| `performGlobalAction` | 16 | 可用 |
| `viewIdResourceName` | 18 | 可用（须开 `FLAG_REPORT_VIEW_IDS`） |
| `ACTION_PASTE` | 18 | 可用 |
| `ACTION_SET_TEXT` | 21 | 不可用 |
| `dispatchGesture` | 24 | 不可用 |

`compileSdk = 37` 意味着编译期能看到 API 24+ 的符号——**类型检查挡不住这类错误**，
只能靠这张表与真机验证。

## 与其他清单的关系

| 清单 | 位置 | 谁对账 |
|---|---|---|
| 动作名 | 本文件 ↔ `SupportedActions.kt` | `tests/test_action_names.py` |
| 协议消息类型 | `protocol/schema/` ↔ 双端实现 | `tests/test_protocol_schemas.py` |
| 配置键 | `modules/config/spec.py` ↔ `RuntimeConfig.kt` | `tests/test_config_spec.py` |
"""

# ── 淋浴会话 ──
# 无障碍服务驱动浴室 APP 完成流程。经 SerialCommandQueue 串行——界面操作并发
# 会让节点定位互相错位。需要能力位 ops（拍照是 photo）。

SESSION_ASSIGN = "session.assign"
"""分配：把学号凭证分配到某个喷头。喷头须空闲，同一学号不能有进行中的会话。"""

SESSION_SETTLE = "session.settle"
"""结算：结束该学号在该设备上的进行中会话。"""

CAMERA_CAPTURE = "camera.capture"
"""拍照：抓取一次现场画面。结果在 data.image_base64。

对象是**摄像头**这个硬件，不是会话——早先叫 `session.photo` 是沿用旧系统的说法，
按本项目的命名规则站不住：会话里能拍照、音频里能录放，那种拼凑感会越来越刺眼。
"""

# ── 设备自身 ──
# 即时执行，不经调度队列：重启与卸载会停掉 agent 进程，排队没有意义。
# 全部需要 X-Ops-Token，且设备须已信任在线。三者都记入 command_audit。

SCREEN_CAPTURE = "screen.capture"
"""截屏：抓取设备屏幕（含浴室 APP 界面）。

走 screenrecord + MediaCodec 解码首帧——本机 framebuffer/screencap 取不到画面
（硬件合成器直出）。结果在 data.image_base64。
"""

DEVICE_EXEC = "device.exec"
"""执行：在设备上运行一条 root shell 命令。结果在 data.output。

与 ui.* 同权：这条用 root shell，那条用无障碍 API。选哪条见模块注释的判据。
"""

DEVICE_RESTART = "device.restart"
"""重启：重启 agent 进程（不重启整机）。

先回报再动作——它会杀死本进程，不先回报的话命令方只能等超时。
"""

DEVICE_UNINSTALL = "device.uninstall"
"""卸载：卸载设备上的 agent（用于退役或排障）。同样先回报再动作。"""

# ── 调试通道 ──
# 设备端启动 adbd，服务端在 443 上透传真实 ADB。开桥返回一次性 nonce，
# 设备端用它向服务端认证端口归属；每次开桥递增世代号，旧 TCP 客户端被断开。

BRIDGE_OPEN = "bridge.open"
"""开桥：在设备上启动 adbd，配合服务端在 443 上透传真实 ADB。"""

BRIDGE_CLOSE = "bridge.close"
"""关桥：停止 adbd 并回收端口。"""

# ── 设备界面（无障碍服务或 root shell，由 via 选）──
# 与 screen.capture 的区别：那个给的是一张图，这里给的是**结构**与**动作**。
# 排查"设备卡在某个弹窗"时，节点树比截图更有用。

UI_DUMP = "ui.dump"
"""读界面树：导出当前窗口的节点树，结果在 `data.xml`。

格式与 `uiautomator dump` 完全一致：`<hierarchy>` 嵌套 `<node>`，`bounds` 写作
`[left,top][right,bottom]`，`index` 是**同级中的序号**而不是全树序号——层级由
嵌套表达，没有 `depth` 字段。
节点上限取配置项 `ui_dump_max_nodes`，达到上限即停止遍历。

界面在导出与使用之间若变了，同一个 `index` 会指向别的元素；需要稳定定位时，
用元素上的 `resource-id` 在自己这一侧重新匹配。
"""

UI_TOUCH = "ui.touch"
"""触摸：点击或滑动。参数给 `x`+`y`；滑动再给 `to_x`+`to_y`+`duration_ms`。

坐标由调用方给。要按元素定位，先用 `ui.dump` 读节点树，从元素的 `bounds`
算出中心点——**不要用"树的第 n 个节点"这种遍历序号**，它会随界面变化错位
（真机上已因此点错过地方）。
点击默认走无障碍（命中节点），无障碍不在时退到 shell 的 `input tap`。
"""

UI_SCROLL = "ui.scroll"
"""滚动：`direction` 取 `forward` 或 `backward`。

无障碍的 `ACTION_SCROLL_FORWARD` / `ACTION_SCROLL_BACKWARD`（API 16+）作用于
可滚动容器——这是 API 19 上唯一可用的滚动接口，`dispatchGesture` 要 API 24。
shell 通道用 `input swipe` 等效实现。

与 `ui.touch` 分开：滚动作用于**容器**而不是坐标，语义不同。
"""

UI_KEY = "ui.key"
"""按键：back / home / recents / notifications。

闭集。写错在路由层被 422 挡下，不发给设备。
"""

UI_INPUT = "ui.input"
"""输入文本：写入当前焦点控件。

无障碍在时用剪贴板 + `ACTION_PASTE`（API 18+，API 19 没有 `ACTION_SET_TEXT`），
能写入任意字符，代价是占用剪贴板。无障碍不在时走 shell 的 `input text`，
它只吃 ASCII——非 ASCII 会被如实拒绝，而不是写进去一串乱码。
"""

#: 供测试与文档校验的完整清单（新增动作必须登记于此，否则对账会漏）
ALL_ACTIONS: tuple[str, ...] = (
    SESSION_ASSIGN,
    SESSION_SETTLE,
    CAMERA_CAPTURE,
    SCREEN_CAPTURE,
    DEVICE_EXEC,
    DEVICE_RESTART,
    DEVICE_UNINSTALL,
    BRIDGE_OPEN,
    BRIDGE_CLOSE,
    UI_DUMP,
    UI_TOUCH,
    UI_SCROLL,
    UI_KEY,
    UI_INPUT,
)

# ── 执行通道 ──
# 设备端把命令分到四条互不阻塞的路径上跑（自动化队列 / 采集执行器 / 维护执行器 /
# 调试桥），服务端按同一划分限流：**同一通道内每台设备只放行一条**，不同通道
# 互不影响。
#
# 为什么不是"每台设备同时只允许一条命令"：那个粒度比设备更严，会让"分配"与
# "拍照"互相拒绝，而设备端本来就能同时做这两件事。真机上出现过前端按 3 秒轮询
# 截图，把人工发的 `device.exec` 挤成 device.busy 的情况——两侧的划分必须一致，
# 否则服务端会拒掉设备明明做得了的事。

CHANNEL_AUTOMATION = "automation"
"""串行操作浴室 APP 界面：分配、结算、界面注入。设备端走单线程串行队列，并发会让节点定位互相错位。"""

CHANNEL_CAPTURE = "capture"
"""采集：拍照、截屏。设备端走独立执行器，与自动化并行。

导出节点树**不在这里**：它读的是无障碍那棵 UI 树，与 `ui.touch` 同一套 API，
归在 automation——否则"看界面"与"读界面"会互相排队，而它们本就该串行在一条队列里。
"""

CHANNEL_MAINTENANCE = "maintenance"
"""设备自身维护：执行命令、重启、卸载、开关调试桥。设备端即时执行，不进任何队列。"""

#: 动作 → 执行通道。每个动作恰好属于一个通道，由 `tests/test_action_names.py` 对账。
ACTION_CHANNELS: dict[str, str] = {
    SESSION_ASSIGN: CHANNEL_AUTOMATION,
    SESSION_SETTLE: CHANNEL_AUTOMATION,
    UI_TOUCH: CHANNEL_AUTOMATION,
    UI_SCROLL: CHANNEL_AUTOMATION,
    UI_KEY: CHANNEL_AUTOMATION,
    UI_INPUT: CHANNEL_AUTOMATION,
    UI_DUMP: CHANNEL_AUTOMATION,
    CAMERA_CAPTURE: CHANNEL_CAPTURE,
    SCREEN_CAPTURE: CHANNEL_CAPTURE,
    DEVICE_EXEC: CHANNEL_MAINTENANCE,
    DEVICE_RESTART: CHANNEL_MAINTENANCE,
    DEVICE_UNINSTALL: CHANNEL_MAINTENANCE,
    BRIDGE_OPEN: CHANNEL_MAINTENANCE,
    BRIDGE_CLOSE: CHANNEL_MAINTENANCE,
}
