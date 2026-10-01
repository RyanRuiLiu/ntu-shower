# Changelog

本项目所有显著变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.4.8] - 2026-10-01

### 修复

- **设备列时有时无**：此前只从路径提取 device_id——设备动作（`/ops/devices/{id}/…`）
  有、列表类（`/ops/doors?device_id=`）与动作类（`/ops/sessions/assign` 的请求体）
  没有。定死取值规则（"本次调用作用于哪台设备就记哪台"）并按三种落点解析：
  路径段 → query 的 device_id → JSON 体的 device_id；都不针对设备（如清单本身）
  为空。三源各有测试锁定

### 新增

- **响应体入库**：JSON 响应全文随审计行落 `response_body` 列——审计有问也有答。
  全有或全无、绝不截断：超 256KB 的（截图/拍照 base64）记占位说明；UI 树 XML
  与媒体同一处理不落库（数据重型且可重取）

### 新增

- **响应体入库**：JSON 响应全文随审计行落 `response_body` 列——审计有问也有答。
  全有或全无、绝不截断：超 256KB 的（截图/拍照 base64）记占位说明；UI 树 XML
  与媒体同一处理不落库（数据重型且可重取）

## [0.4.7] - 2026-10-01

### 变更

- **审计如实记录、不脱敏、不截断**（用户决定，覆盖 0.4.4 的脱敏决定）：
  调用方令牌与身份原文落库；新增来源 IP（X-Real-IP）、User-Agent、JSON 请求体
  原文三列——发布上传的 APK 二进制除外（本体已另存）；`command_audit` 新增
  `params`（命令参数）与 `data`（`device.exec` 的输出——不可重现的运维事实）
  两列。媒体与可重取的 dump XML 仍不落库（体积与时效取舍，非脱敏）
- **保留策略定稿**：审计两表 90 天滚动清理；业务数据（喷头状态迁移、使用会话）
  永久保留
- 审计页：调用方下显示来源 IP；两个标签的职责写明（接口调用=谁从哪调了什么，
  命令流水=设备执行得怎么样）

## [0.4.6] - 2026-10-01

### 新增

- 审计行补齐"请求与响应"：`query` 记录请求查询串（令牌走请求头、永不进
  query），失败响应经 problem+json 体缓冲取回中文 `detail`——审计行从
  "type 代号"升级为"为什么失败"的中文答案
- 前端审计页重做：接口调用审计与命令流水两层同屏；设备显示名字（id 在
  展开区）；点行展开全部字段（request_id / trace_id 全值、失败说明），
  每行可整行复制 JSON 带出去比对；命令流水补 trace_id 与中文说明列

### 变更

- api_call 表增加 query / detail 两列（开发库直接重建）

## [0.4.5] - 2026-10-01

### 变更

- `api_call` 审计表增加 `kind` 列（http / gateway）：网关生命周期行的连接与
  断开此前把事件语义塞进 path 字段（"/gateway/device connect"），现在 kind
  归类、path 干净、status 约定 0=连接 1=断开；`GET /ops/audit` 支持 kind 过滤
- RUNBOOK 新增「痕迹取回」：三层痕迹的取回命令与保留期说明；小节交叉引用
  改为按名称（小节重编号不再破坏引用）

## [0.4.4] - 2026-10-01

### 新增

- **接口调用审计**：`api_call` 表一行一个 API 调用（/api、/ops、/ota），含脱敏
  调用方（令牌只落 sha256 指纹）、耗时、路径可识别的设备、RFC 9457 problem type；
  认证失败 / 授权失败 / 参数不合法 / 服务端错误天然落表（OWASP 必记事件类）。
  查询走 `GET /ops/audit`，本身也被审计。不记请求体与响应体
- **命令级与 HTTP 级审计互通**：W3C `traceparent`（沿用或生成 trace-id）、
  `X-Request-ID` 响应头、`command_audit.trace_id`——三层（HTTP 审计 / 命令审计 /
  设备端 audit.log）以 command_id 与 trace_id 贯通
- **stdout 结构化 JSON 日志**：字段照 OpenTelemetry Log Data Model，稳定事件
  代号（`event`）与中文消息分离，属性只收标量；全部 30 处调用迁移
- **设备端命令痕迹**：`/sdcard/ntu-shower/audit.log`（JSONL，256KB 封顶）记录
  命令受理 / 幂等重复 / 结果回报与网关生命周期，`device.exec` 直接 `cat` 取回
- **保留期执行**：`api_call` 按 `audit_retention_days`（默认 90 天）每日清理；
  OTA 发布按 `ota_release_keep`（默认 5 个）裁剪，`DELETE /ops/releases` 补上
  此前缺失的 APK 文件删除

### 修复

- 网关帧处理异常的留痕改走结构化事件（原 %-format 文案被测试锁定）

## [0.4.3] - 2026-10-01

### 新增

- 界面检视文本注入**双通道并存、由使用者选择**：`keys` 按键注入（仅英文/数字/符号，
  无空格问题）与 `clipboard` 剪贴板粘贴（支持中文）。通道是协议必填项——缺失或未知
  值在服务端（422）与设备端（`ui.unknown_channel`）都被明确拒绝，没有缺省回落
- 工具条方向键组 ▲▼◀▶ / OK 与 Enter/Del：设备端 `UiKey` 扩展七个键值，全部走
  `input keyevent`，原理照旧项目 viewer 按钮栏（焦点光标导航）
- UI 树右键菜单扩充：复制 resource-id / 文本 / 内容描述 / 类名 / 范围 / 中心坐标、
  长按元素（1 秒）
- UI 树面板可收起：点标题把右侧面板收起、画面占满整行，画面区右缘的把手恢复

## [0.4.2] - 2026-10-01

### 修复

- 界面检视"输入文本"报设备端内部错误：节点树查找的命中项是直接子节点自己时，
  循环里先 `recycle()` 再把它当返回值交出去，调用方随后在**已回收节点**上
  `performAction`（4.4 对已回收节点直接抛 IllegalStateException，落进命令
  catch-all）。命中自身时改为不当场回收，仍由调用方的 `finally` 统一回收一次，
  每个节点依旧恰好回收一次

## [0.4.1] - 2026-10-01

### 修复

- 设备端截图右侧内容缺失：编码器 16 像素对齐产生的 10 列填充落在编码面**左侧**
  （画面右对齐），JPEG 编码却从 x=0 起裁 1366 列——黑边留下、右侧真实内容被裁。
  改为从右侧锚定取内容列，输出即完整屏幕，网页端高亮框随之对位
- 界面检视坐标提示条错位且贴边被裁：定位基准从面板改为图片本身，贴缘向内翻转
- 界面检视十字线与滑动箭头只在左上角 300×150 内可见：SVG 替换元素不吃
  `inset:0` 拉伸，显式给满宽高
- 界面检视 "Tap Element" 按钮藏在属性面板滚动区之下：面板按内容撑开，树区让位
- 配置页布尔项的勾选框被 `flex: 1` 拉伸，方块悬在行中间、文字推到右边

### 变更

- 操作后自动刷新拆成"自动画面 / 自动UI树"两个独立开关，选择记在 localStorage；
  连续操作合并为一轮刷新，`failed`/`rejected` 结果不再触发重取

## [0.3.4] - 2026-09-30

### 修复

- 设备端 `AgentService.onDestroy` 未关闭 worker / otaScheduler 线程池与定时任务，
  服务销毁后这些线程继续存活并执行任务
- 设备端 `BathAppNavigator.findDoorInGrid` 匹配成功路径漏回收节点
  （同文件另两处都在 `finally` 里，只有这处漏了）
- 设备端 `OtaUpdater` 自行实现 deviceId 且与 `DeviceIdentity` 行为不一致
- `CoverOverlay` 的主线程等待无超时（主线程繁忙时调用方线程永久阻塞）

### 变更

- 抽出 `core/threading/MainThread`：`CoverOverlay` 与 `A11yService` 各写了一遍
  「投递主线程 + 同步等待」，且超时行为不一致
- `BathAppNavigator.detectPage` 返回具名 `PageSnapshot` 而非 `Pair`
- 移除 `matches()` 中永远不会成立的后缀匹配条件

## [未发布]

### 修复

- **API 问题类型不再用状态码充当**：`device-offline` 与 `device-busy` 此前同为 409 +
  同一 type，客户端只能解析 `detail` 文案。新增 `core/problems.py` 登记 11 个稳定 code。
- **未信任设备返回 403 而非 404**：设备存在却被报成"不存在"，把授权问题伪装成资源问题。
- **测试依赖真实 60 秒超时**：`TestClient` 的同步请求是串行的，"第二条命令在途时被拒"
  这类用例实际白等一分钟。命令超时改为可配置环境值，全量 66s → 7s。
- 桥接 `/adb/open` 的响应里混进未替换的 `root@<server>` 占位文本与人类操作指引。

### 变更

- **`capabilities` 从空转变为生效**：设备端此前恒发空数组、服务端只存不用。
  现由 `SupportedActions` 统一声明，服务端下发前比对；空数组表示"未声明"（旧版本）不拦截。
- 泛化名具名化：`repository.insert` → `insert_command` / `insert_release`，
  `problems.adhoc` → `adhoc_problem`。

### 修复

- **OTA force 无限重装循环（0.3.1 线上事故）**：`mark_installed_upto` 定义了却从未被调用，
  安装确认表恒为空，force 发布因此每次握手都判定"未安装"，设备每约 7 秒重装一次 APK。
  已接入 hello 流程（且早于 `latest_for`），并补回归测试锁定。
- **ADB 桥世代判定误杀首次连接**：`bridge.generation >= generation` 把本次 open 刚建立的桥
  判成"旧世代重放"，使任何桥接连接都被 4409 拒绝。改为严格大于。
- **OTA 发布路径目录穿越**：`version` 直接参与文件路径拼接，可读写任意路径。
  改为边界正则校验 + `release_path` 唯一构造点。
- **SQLite 连接跨线程共享**：进程内单连接被线程池并发使用，`execute → commit` 之间
  可被其他线程插入；测试隔离清理也只作用于调用线程。改为每线程独立连接 + 全局登记表。
- **事件总线跨事件循环投递**：工作线程直接操作订阅者队列；改为
  `loop.call_soon_threadsafe` 回到订阅者循环，并清理循环已关闭的订阅。
- **管理令牌**：改用 `secrets.compare_digest` 恒定时间比较；占位令牌在启动自检中被拒绝启动。
- 测试隔离缺陷：两个用例未请求 `fresh_db`，实际写入默认数据库。
- 假测试：自动结算"未超时 → 不结算"的断言恒为真（`or short is None`），等于没有测试。

### 变更

- 时间处理收敛为 `core/clock.py` 单一来源（此前格式化 3 处、解析 3 处各写一遍）。
- 命名统一：派生状态 `STATE_*`、原始值 `RAW_*`、时间单位统一 `_S` 后缀、
  表名去复数（`releases` → `ota_release`）、`release_install.release_version` → `version`。
- 依赖守卫移出 `routes.py` 到 `devices/dependencies.py`，消除循环导入（连带去掉函数内导入）。
- 盲捕获改为 `contextlib.suppress` 或精确异常类型；后台扫描循环保留宽捕获并注明理由。
- `mypy --strict` 全量启用；ruff 规则集扩展至含命名/类型/异常/pytest 风格等 22 组。
- 覆盖率门槛 95 → 96（门槛只升不降）。

### 新增

- `bridge.open` / `bridge.close` 协议 schema（此前代码在用，schema 缺失）。
- `screen.capture` 服务端入口（设备端此前已实现但服务端无调用方）。
- 设备端 `YuvConverter`：从 `ScreenCapture` 抽出的纯逻辑，纳入 JVM 单测覆盖。
- 版本号单一来源与跨端一致性测试。
- `docs/STANDARDS.md` 重写：每条标准绑定执行机制，新增「命名与标识符」「扩展方式」两节。

## [0.3.1] - 2026-09-30

### 变更

- 服务端与设备端 0.3.1 上线；设备端经 OTA 完成 0.2.5 → 0.3.1 升级（真机验证）

## [0.4.0] - 2026-10-01

### 新增
- 设备界面远程操作（/ops/devices/{id}/dump、touch、scroll、key、input）：
  节点树导出与 `uiautomator dump` 同格式；坐标语义走 root shell `input`，
  中文输入走剪贴板粘贴（shell 通道只支持 ASCII，实测确认）
- 全屏截图命令 screen.capture 与 /ops/devices/{id}/capture（每步经对照实验验证：
  screencap 在本机不可用、无需 --size 手算对齐、无黑边可裁、JPEG q50 足够）
- ADB 桥会话审计（adb.open/close/session_end 全量留痕，复用 command_audit）
- ADB 桥状态可观测（/ops/devices/{id}/bridge/status：世代/两端连接状态/剩余寿命）
- 设备身份与能力位体系（/api/v1/identity、/ops/identities；微信 openid 与网页标识，
  能力位 ops/photo，X-Identity 判定）
- ops 管理面整体启用 X-Ops-Token（占位令牌拒绝启动，OWASP ASVS V2.1）

### 修复
- ADB 桥死锁：设备 WS 断开与 TCP 泵收尾互相等待（泵退出不再回调清理，只关自己的写端）
- ADB 桥四条结束路径各自收尾导致的端口泄漏（收敛为 open/recycle 两个生命周期入口）
- OTA force 死循环（安装确认表 + rearm，见 ADR-0036）；重启不再清空发布表
- 网关存活探针：一个 ping 间隔收不到 pong 即断链，杜绝半开连接假在线
- 设备时钟偏差持久化与跳变检测（EMA 平滑 + 超限直接取新值）
- GatewayClient 销毁后的孤儿重连循环（停用标志）
- 握手超时（20 秒收不到 hello_ack 主动断链，消除"链路连着会话没建立"的停滞）

### 变更
- 管理面 /admin → /ops，鉴权 X-Admin-Token → X-Ops-Token
- 命令按通道排队（同通道串行、队满拒绝），通道划分见 core/actions.py
- 命令超时改传相对时长（设备无 NTP，绝对时刻会被时钟偏差判死）
- 网页端挂根路径，静态资源 no-cache
- 设备配置缓存落盘：重启后先用上次配置，不再回落编译期默认
- BathAppGuard 阈值改运行时取值：远程配置改动即时生效

## [0.3.0] - 2026-09-30

### 新增
- 真实 ADB 桥接（ADR-0035）：管理面触发、经 443 网关二进制透传 adbd 字节流，零新端口；adb connect 后全功能可用
- 自动结算（ADR：占用超阈值且在名单内的会话自动下发结算，阈值/名单远程可配）
- 同一学号重复分配拦截（对齐旧系统 duplicate_session）
- 浴室 App 崩溃守护（ADR-0024 参数化：开关/窗口/阈值/冷却远程可配，纯逻辑类全单测）
- ADB 桥接/自动结算/崩溃守护全量测试；覆盖率门 95%（服务端）/80%（设备端）

### 修复
- **OTA force 发布死循环**（真机发现）：增加每设备安装确认（release_install），已确认不再推送；rearm 端点支持调试重推；移除 init_tables 的 DROP（重启不再清空发布）
- bridge.open 指令缺信封字段被设备端协议层丢弃

### 变更
- 用户面操作接口匿名化（ADR-0034，鉴权由浴室系统凭证完成，auth 模块整体删除）
- detekt 升级 2.0.0-alpha.6（Kotlin Analysis API，支持 JDK 25）
- 设备端拉起语义文档化与无冲突审计：本 ROM（DS83X）实测不投递开机广播，
  开机拉起锚点 = 系统重绑无障碍（ActivityManager 日志取证）；BOOT_COMPLETED
  分支保留以兼容标准 ROM；四条拉起路径经 onStartCommand 守卫收敛无冲突

## [0.2.4] - 2026-09-30

### 修复
- 设备端 config.update 不回写 RuntimeConfig：远程改心跳/超时等参数需等重连才生效
- /admin/devices/{id}/exec 端点在领域重组中漏迁（真机测试发现）
- 设备端 Clock 线程安全（SimpleDateFormat 共享实例并发损坏）
- AutomationFlow 收尾的 input tap 绕过 RootShell 单一来源

### 变更
- 服务端全面领域重组：admin 混合体拆归 devices/config/ota/sessions/shadow/auth 各模块
- 角色模型落地：anonymous 只读 / user 操作设备（JWT）/ admin 全功能
- 协议信封单一来源 core/protocol.py；link.error 载荷定稿为扁平 {code, message}
- HTTP 错误响应对齐 RFC 9457 problem+json
- 每设备单在途命令（409 device.busy）；OTA 上传体积上限
- config.ack 超时入配置（config_ack_timeout_s）
- DnsResolver 端口可注入（默认 53 语义不变）

### 新增
- /api/v1/auth/token 令牌签发与 /admin/users 账户管理（provider 抽象：internal 已实现）
- 用户面影子端点 /api/v1/devices/{id}/shadow

## [0.2.3] - 2026-09-30

### 新增
- 设备影子（Device Shadow）抽象：/admin/devices/{id}/shadow 完整文档
- 管理面命令审计查询、设备改名、/healthz 版本信息

### 修复
- FileObserver 掩码补 MODIFY（真机实测漏报）
- OkHttp 显式放行 ECDHE-ECDSA-CBC 套件（4.4 设备对 ECDSA 证书握手的必要条件）

## [0.2.0] - 2026-09-30

### 新增
- OTA 全自动自更新（轮询 manifest → sha256 校验 → pm install -r → MY_PACKAGE_REPLACED 自启），真机验证 0.1.0→0.2.0
- 发布即推送 ota.upgrade_available；force 定向调试发布
- 远程配置 14 键（全局默认 + 设备覆盖）
- 服务端周期 ping/pong 时钟偏差 EMA，快照落库前校准

## [0.1.0] - 2026-09-29

### 新增
- 项目奠基：FastAPI 服务端（网关/API/管理/OTA）+ Android 4.4.4 寄生代理 APK
- 设备身份（ANDROID_ID）+ 后台信任机制
- 内置 DNS 解析器、TLS 1.2 最小配置（真机实测）
- doors 采集（FileObserver + 发件箱）、命令调度（assign/settle/photo）、看门狗、崩溃自愈
