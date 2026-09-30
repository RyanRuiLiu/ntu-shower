# Changelog

本项目所有显著变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
  改为边界正则校验 + `release_path` 唯一构造点（纵深防御）。
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
