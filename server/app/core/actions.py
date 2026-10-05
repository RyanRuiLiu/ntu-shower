"""设备命令动作名与执行通道的唯一登记处。

动作命名规则为 `<对象>.<动作>`：对象是被操作的东西（session 会话、screen 屏幕、
device 设备、bridge 调试桥、ui 界面、freelist 免费名单），不是代码模块或用途。
HTTP 路径遵循同一套词：集合用复数无动词，对单个资源施加动作用 `<资源>/<动词>`，
改档案用 PATCH 带字段。
动作名与设备端 transport/SupportedActions.kt 由 tests/test_action_names.py 对账。
"""

# ── 淋浴会话 ──
# 无障碍服务驱动浴室 APP 完成流程，经设备端串行队列执行：界面操作并发会让节点
# 定位互相错位。分配与结算要求能力位 session，拍照要求 photo。

SESSION_ASSIGN = "session.assign"
"""分配：把学号凭证分配到某个喷头。喷头须空闲，同一学号不能有进行中的会话。"""

SESSION_SETTLE = "session.settle"
"""结算：结束该学号在该设备上的进行中会话。"""

CAMERA_CAPTURE = "camera.capture"
"""拍照：抓取一次现场画面，结果在 data.image_base64。

动作对象是摄像头硬件而非会话，因此不挂在 session 名下。
"""

# ── 设备自身 ──
# 即时执行，不经调度队列：重启与卸载会停掉 agent 进程，排队没有意义。
# 三者都是管理面动作，设备须已信任在线，结果记入 command_audit。

SCREEN_CAPTURE = "screen.capture"
"""截屏：抓取设备屏幕（含浴室 APP 界面），结果在 data.image_base64。

走 screenrecord 加 MediaCodec 解码首帧：本机 framebuffer 与 screencap 取不到
画面（硬件合成器直出）。
"""

DEVICE_EXEC = "device.exec"
"""执行：在设备上运行一条 root shell 命令，结果在 data.output 与 data.exit_code。"""

DEVICE_REBOOT = "device.reboot"
"""重启整机。设备重启后 agent 随之重来，会话与喷头现态由重连后的全貌帧重建。

先回报再动作：与 [APP_RESTART] 同理，命令本身会掐断回报通路。
"""

# ── 本程序 ──
# [APP_RESTART] 重启的是**我们这个程序**（agent 进程），不是设备也不是浴室 APP，
# 所以对象是 app 而不是 device——`device.reboot` 才是整机重启，两者不能混用一个对象。

APP_RESTART = "app.restart"
"""重启 NTU Shower 进程（不重启整机，也不动浴室 APP）。先回报结果再动作：命令会杀死本进程。"""

# ── 浴室 App ──
# 强停与冷启动由 root shell 完成，即时执行。过程会中断进行中的会话，网页端有确认。

BATH_RESTART = "bath.restart"
"""重启浴室 App（com.datalook.bath）：先强停再冷启动。开机时间校正后让它重读
每日定时开关机计划；它状态异常时排障用。"""

CLOCK_SET = "clock.set"
"""设置设备系统时钟。params.datetime 为设备本地墙钟时间（YYYY-MM-DD HH:MM:SS）。

浴室 App 按系统时钟装载每日定时开关机计划，而设备无 NTP：开机自动校正
（设备端 TimeSync）与网页端手动设置共用本动作。
"""

POWER_SCHEDULE = "power.schedule"
"""重布设备定时开关机（/dev/McuCom，ioctl 0xA1，见 docs/device-poweroff.md §11）。

params.off_at 与 params.on_at 是设备**表盘上**的目标时刻（HH:MM）：off_at 是断电
时刻，on_at 是断电之后到上电的时刻；设备端用自己的钟换算成相对延时（分钟粒度）
写入 MCU。三个行为要点，都来自实测：

- 延时是相对时长，**截止判定却用实时钟**——钟被拨到越过已布防的截止即当场断电，
  回拨则等量推迟。拨钟之前必须先重布（extend_hours 的顺序由此而来）。
- 一次布防覆盖上一次；enable 固定 3（关机+开机都使能），不作为参数暴露。
- MCU 只吃分钟，秒被截断；参数在 HTTP 边界即拦格式，不发给设备。
"""

# ── 免费名单（freelist）──
# 浴室 App 读的那份文件：一行一个学号，名单里的人不收费。对象名取 freelist 而不是
# free_list：**对象必须是单个全小写字母词**（`test_actions_follow_the_object_dot_action_form`），
# 而下划线只允许出现在动作那一半。
#
# **路径不在协议里**——它登记在设备端的 AppConfig，服务端只给内容。给得了路径就等于把
# "写哪个文件"交给对端，而这个动作的安全性正来自"只有那一个文件可写"。
#
# 读写都是即时完成的短操作，不动界面、不进自动化队列，所以归维护通道。

FREELIST_READ = "freelist.read"
"""读免费名单：取回文件原文，结果在 data.content。

原样进出，不在设备端解析：行尾与分行是服务端的事，两端各拆一次必然分叉。
"""

FREELIST_WRITE = "freelist.write"
"""写免费名单：params.content 是文件原文，整份替换。

**空内容是合法内容**——名单为空就等于没人免费，所以不因为空而拒绝它；缺这个参数
才是请求本身不成立。
"""

# ── 调试通道 ──
# 设备端启动 adbd，服务端在 443 上透传真实 ADB。开桥返回一次性 nonce 供设备
# 认证端口归属；每次开桥递增世代号，旧 TCP 客户端被断开。

BRIDGE_OPEN = "bridge.open"
"""开桥：在设备上启动 adbd，配合服务端在 443 上透传真实 ADB。"""

BRIDGE_CLOSE = "bridge.close"
"""关桥：停止 adbd 并回收端口。"""

# ── 设备界面 ──
# 各动作的后端按动作固定，不由调用方选路（ui.input 的 channel 除外）：
# touch/scroll/key 走 root shell（设备无坐标手势能力，见下方运行时约束）；
# dump 走无障碍（shell 侧 uiautomator dump 会接管系统无障碍会话，不采用）；
# input 由调用方在两条并列通路中显式选择。无障碍整体失效时，shell 组照常工作。

UI_DUMP = "ui.dump"
"""读界面树：导出当前窗口的节点树，结果在 data.xml，格式与 uiautomator dump 一致。

bounds 为 [left,top][right,bottom]，index 是同级中的序号而非全树序号；需要稳定
定位时用 resource-id 在调用方一侧重新匹配，不使用遍历序号（随界面变化错位）。
节点上限取配置项 ui_dump_max_nodes，达到即停止遍历。
"""

UI_TOUCH = "ui.touch"
"""触摸：点击给 x 与 y；滑动再给 to_x、to_y 与 duration_ms。

定位先经 ui.dump 读节点树，由元素的 bounds 求中心点，不使用遍历序号。
"""

UI_SCROLL = "ui.scroll"
"""滚动：direction 取 forward 或 backward，作用于可滚动容器。

与 ui.touch 的滑动分开：滑动的落点取决于起点，滚动只表达方向。闭集，路由层
以 422 挡下非法值，不发给设备。
"""

UI_KEY = "ui.key"
"""系统按键：back、home、recents、notifications、方向键组（dpad_*）、enter、del。

闭集，路由层以 422 挡下非法值，不发给设备。
"""

UI_INPUT = "ui.input"
"""输入文本到当前焦点控件：channel 二选一，无回落。

clipboard 走无障碍粘贴（支持中文；本 ROM 在同一字段第二次插入时会带出空格）；
keys 走 shell 注入（仅 ASCII），非 ASCII 会被如实拒绝而不是写坏。
"""

#: 全部动作。新增动作必须登记于此，否则对账会漏。
ALL_ACTIONS: tuple[str, ...] = (
    SESSION_ASSIGN,
    SESSION_SETTLE,
    CAMERA_CAPTURE,
    SCREEN_CAPTURE,
    DEVICE_EXEC,
    DEVICE_REBOOT,
    APP_RESTART,
    BATH_RESTART,
    CLOCK_SET,
    POWER_SCHEDULE,
    FREELIST_READ,
    FREELIST_WRITE,
    BRIDGE_OPEN,
    BRIDGE_CLOSE,
    UI_DUMP,
    UI_TOUCH,
    UI_SCROLL,
    UI_KEY,
    UI_INPUT,
)

# ── 执行通道 ──
# 设备端把命令分到三条互不阻塞的路径（自动化队列、采集执行器、维护执行器），
# 服务端按同一划分排队限流：同一通道内每台设备串行，跨通道并行。两侧划分必须
# 一致，否则服务端会拒掉设备明明做得了的事。

CHANNEL_AUTOMATION = "automation"
"""串行操作浴室 APP 界面：分配、结算与全部 ui.* 动作。"""

CHANNEL_CAPTURE = "capture"
"""采集：拍照与截屏。与自动化并行，一条慢截图不会堵住操作序列。"""

CHANNEL_MAINTENANCE = "maintenance"
"""设备自身维护：执行命令、重启本程序、重启整机、开关调试桥、读写免费名单。设备端即时执行，不进队列。"""

#: 动作 → 执行通道。每个动作恰好属于一个通道。
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
    DEVICE_REBOOT: CHANNEL_MAINTENANCE,
    APP_RESTART: CHANNEL_MAINTENANCE,
    BATH_RESTART: CHANNEL_MAINTENANCE,
    CLOCK_SET: CHANNEL_MAINTENANCE,
    POWER_SCHEDULE: CHANNEL_MAINTENANCE,
    FREELIST_READ: CHANNEL_MAINTENANCE,
    FREELIST_WRITE: CHANNEL_MAINTENANCE,
    BRIDGE_OPEN: CHANNEL_MAINTENANCE,
    BRIDGE_CLOSE: CHANNEL_MAINTENANCE,
}
