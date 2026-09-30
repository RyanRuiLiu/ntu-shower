# STANDARDS.md — 工程标准册（约束性）

本册登记本项目受约束的标准。**每条标准都必须注明执行机制**——没有执行机制的标准只是愿望，
写进来只会随时间失效。与标准冲突的实现按违规处理。

**数字不写进本册**：覆盖率、测试数量、性能值一律由 CI 实时产出；
写死在文档里的数字必然过期，过期数字本身就是违规来源。

## 1. 语言与代码风格

| 标准 | 适用范围 | 执行机制 |
|---|---|---|
| PEP 8 / PEP 484（类型注解） | 服务端 | `ruff check`（规则集见 `server/pyproject.toml` 的 `select`）+ `mypy app --strict` |
| ruff format（black 兼容） | 服务端 | `ruff format --check app tests` |
| Kotlin 官方编码约定 | 设备端 | detekt 2.0（Kotlin Analysis API）+ ktlint 规则集，`./gradlew detekt` |
| Kotlin 命名：类/接口 PascalCase，函数/属性 camelCase，常量 UPPER_SNAKE | 设备端 | detekt `NamingRules` |
| 一文件一顶层声明（Kotlin） | 设备端 | 评审约束（detekt 不覆盖，登记在案） |
| 文档字符串：中文，首行摘要，说明"为什么"而非复述代码 | 双端 | 评审约束 |
| 注释只解释非显然的决策与设备实测结论 | 双端 | 评审约束 |

## 2. 命名与标识符（统一约定）

命名不统一是维护成本的最大来源。以下为项目级约定，**新增代码一律照此**：

| 对象 | 约定 | 示例 |
|---|---|---|
| 数据库表 | 单数 snake_case，领域前缀避免歧义 | `door_live`、`usage_session`、`ota_release`、`device_config` |
| 数据库字段 | snake_case；时间字段统一 `_at` 后缀 | `reported_at`、`applied_at`、`last_seen_at` |
| 时间字段的**取值格式** | 一律 RFC 3339 UTC 毫秒（`core/clock.py` 是唯一来源） | `2026-09-30T12:00:00.000Z` |
| 时间字段的**命名** | 领域表用 `_at` 后缀；**协议映射表**（`door_live`）保留协议字段名 | `reported_at` / `open_time` |
| 单位后缀 | 秒 `_S`、毫秒 `_MS`、字节无后缀、比例无后缀 | `ASSIGN_TIMEOUT_S`、`reconnect_base_ms`、`PENDING_MAX` |
| 协议消息类型 | `<层>.<动作>`，应答加 `_ack` / `_result` 后缀 | `link.hello` / `link.hello_ack`、`door.snapshot` / `door.snapshot_ack` |
| 命令动作名 | `<域>.<动作>`，与服务端 action 字面量一致 | `session.assign`、`screen.capture` |
| 派生状态常量 | `STATE_*` 前缀；原始值常量加 `RAW_` 前缀以示区分 | `STATE_FREE`、`RAW_STATUS_REPAIR` |
| 路由模块 | 只做 HTTP 编排；跨模块复用的依赖守卫放 `dependencies.py` | `devices/dependencies.py` |
| 模块文件 | `routes.py`（HTTP 编排）/ `service.py`（领域逻辑）/ `repository.py`（持久化） | 各领域模块 |

**禁止**：在函数体内导入（除规避真实循环依赖并注明原因）、散落的字符串字面量代替已登记的常量、
为同一个概念造第二个名字。

## 3. API 与协议

| 标准 | 执行机制 |
|---|---|
| RFC 9457 Problem Details（`application/problem+json`） | `core/errors.py` 统一装配；问题类型登记于 `core/problems.py` |
| 问题类型（`type`）标识**问题种类**而非状态码 | 每个问题在 `core/problems.py` 登记稳定 code；测试断言 `type` 后缀，不解析 `detail` 文案 |
| **响应类型必须显式**：每个端点的 200 响应都要有具名模型，不得返回无类型的 `object` | 契约模型集中在各模块 `schemas.py`（请求用 Pydantic 承担校验，响应用 TypedDict 零开销）；客户端据此生成类型，而不是手抄字段 |
| **可选字段用 `total=False` 表达**，不得靠"有时带有时不带"的松散对象 | 例：`OtaManifest`（有更新时才带版本字段）、`BridgeStatus`（桥打开时才带端口） |
| OpenAPI 3.1 | FastAPI 自动生成，`/docs` 可视 |
| JSON Schema draft 2020-12 覆盖**全部**协议消息 | `protocol/schema/` + `tests/test_protocol_schemas.py`（含"无多余 schema"反向断言） |
| 跨端动作名一致（服务端下发 ↔ 设备端识别） | `tests/test_admin_debug.py` 逐条锁定 |
| 协议变更三处同步 | schema、`docs/PROTOCOL.md`、双端实现（评审检查单） |

## 4. 安全

对照 [OWASP API Security Top 10 (2023)](https://owasp.org/API-Security) 与
[OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) 逐条处置：

| 项 | 处置 |
|---|---|
| 默认凭证 | 占位令牌在启动自检中被拒绝（`core/security.assert_admin_token_configured`） |
| 令牌比较 | `secrets.compare_digest` 恒定时间比较，避免时序侧信道 |
| 路径构造 | 参与文件路径的外部输入先按白名单正则校验（`ota/service.release_path` 是唯一构造点） |
| 对象级授权 | 设备与门是全局资源；写操作经角色守卫 |
| 认证 | 管理面 `X-Admin-Token`；用户面操作需 `X-Identity` 标识 + 能力位（`ops`/`photo`），查看类接口无需凭据（ADR-0037）；浴室账号密码不落库，由调用方在操作时携带 |
| 资源消耗 | 每设备同时至多一条在途命令（409 device.busy）；OTA 上传体积上限 |
| SSRF | 服务端不代取外部 URL |
| 密钥 | 全部走环境变量（`server/.env`），模板只含占位值 |

## 5. 测试与覆盖率

**策略：能测尽测，不能测的逐条登记。** 覆盖率门槛只统计可测部分，
每一处豁免都必须写明"为什么不可测"与"替代验证手段"。

| 端 | 执行机制 |
|---|---|
| 服务端 | `pytest --cov=app --cov-report=term-missing`，`fail_under` 见 `server/pyproject.toml`（门槛只升不降） |
| 设备端 | `:app:jacocoUnitTestCoverageVerification`，门槛见 `android/app/build.gradle.kts`（只升不降） |

**提高覆盖率靠抽取，不靠放宽容忍**。框架耦合类里的业务决策必须抽成纯逻辑再测，
只有确实无法在 JVM 上执行的（节点树、MediaCodec、真实 socket 的进程管理）才登记豁免。
豁免清单越短，门槛越有意义——它是排除项，不是挡箭牌。

### 赦免清单（唯一合法的不测理由）

设备端的实现清单在 `android/app/build.gradle.kts` 的 `frameworkBoundClasses`，
**本文档不再重复列举**（同一份清单写两处必然分叉）。分组与理由如下：

| 分组 | 范围 | 为什么不可测 | 替代验证 |
|---|---|---|---|
| 入口与常驻服务 | `ShowerApp`、`service/`、`receiver/` | 依赖系统服务与 Looper | 真机 E2E（分配/结算/拍照/截图均已实测） |
| 主线程投递 | `core/threading/` | 两个"当前已是主线程"的短路分支必须真实 Looper 才走得到 | 真机运行；等待逻辑本身是 JDK 的 `CountDownLatch`，无自研分支 |
| 网络栈 | `transport/GatewayClient`、`core/net/HttpClientFactory`、`core/tls/`、`core/identity/` | 依赖真实 TLS/DNS 套接字 | 探针 APK 真机实测；可抽的部分已抽为 `ReconnectBackoff`/`DnsResolver` 并纳入覆盖 |
| root shell | `core/shell/RootShell`、`core/perf/`、`core/config/AppConfig` | 依赖真实 `su` 进程与系统服务 | 真机实测；输出解析已抽为 `ShellOutput` 并纳入覆盖 |
| 无障碍节点操作 | `features/automation/`（7 个类） | 依赖 `android.view.accessibility` 节点树与窗口管理器 | 真机 E2E；串行决策已抽为 `SerialCommandQueue` 并纳入覆盖 |
| 设备 IO | `features/remote/`、`features/doors/` | ADB 字节泵 / MediaCodec / FileObserver | 真机实测；事件过滤已抽为 `DoorEventFilter` 并纳入覆盖 |
| 自管理 | `features/selfmanage/` | 依赖 root shell 与包管理器 | 真机 OTA 实测；设置合并已抽为 `A11ySettingsMerge` 并纳入覆盖 |
| 纯数据载体 | `PageState`/`CommandOutcome`/`FlowError`/`Envelope` | 仅编译器生成的 `equals`/`hashCode`/`copy`，无手写逻辑 | 编译期保障（JaCoCo 官方亦建议排除生成代码） |

服务端无需豁免清单：`pyproject.toml` 的 `fail_under` 覆盖全部源码，
端口竞态等分支已用真实占用端口的测试覆盖（见 `test_edge_branches.py`）。

**新增豁免必须先证明"不可测"**（列出尝试过的手段）并同时更新两处：
`frameworkBoundClasses` 与本表的分组。禁止用豁免掩盖"懒得搭测试脚手架"。

## 6. 日志与可观测性

依据 [The Twelve-Factor App §XI Logs](https://12factor.net/logs)：**把日志当作事件流**，
进程无缓冲写 stdout，由运行环境（journald）负责收集与归档——
**应用自身不写日志文件、不做日志轮转**。

| 标准 | 执行机制 |
|---|---|
| logger 名用点分层级 `ntu-shower.<域>[.<模块>]` | 评审约束（命名与模块路径对应） |
| 用 `logger.info("event: key=%s", value)` 延迟格式化，不用 f-string | 评审约束（f-string 会无条件求值，且丢失结构化字段边界） |
| 敏感信息（密码、令牌、ANDROID_ID 全文）**不得进日志** | 评审约束；`session.command` 的参数只在设备端本地使用，不回显 |
| 关键路径必须留痕（见下表） | 评审约束 + RUNBOOK 的故障处置依赖它 |

**级别约定**（依 Python logging 惯例）：

- `DEBUG`：诊断细节，默认不输出（如心跳循环的正常退出）；
- `INFO`：**关键业务事件**——连接建立/断开、命令下发/结果、配置变更、发布上线；
- `WARNING`：**可恢复的异常情况**——设备离线、请求被拒、文件过大、端口占用；
- `ERROR`：需人工介入（未处理异常，由错误处理器记录）。

**必须留痕的关键路径**（缺任何一条，故障时就得靠猜）：

| 路径 | 记录内容 |
|---|---|
| 设备连接/断开 | `device_id`、`trusted`、版本、IP |
| 命令生命周期 | `command_id`、`device_id`、`action`、`status`、耗时——`command_id` 是贯穿服务端与设备端的关联标识（EIP: Correlation Identifier） |
| 配置变更 | 修订号、变更键数、推送到的设备数 |
| OTA 发布/下架 | 版本、体积、`force`/`target`、通知设备数 |
| ADB 桥开关 | `device_id`、端口、世代 |

## 7. 消息语义

设备命令通道按 [Enterprise Integration Patterns](https://www.enterpriseintegrationpatterns.com)
的成熟模式实现，不自创语义：

| 模式 | 本项目的落地 |
|---|---|
| **Correlation Identifier** | 服务端生成 `command_id` 作为请求标识，设备端在 `reply_to` 中原样回填；日志与审计表都用它关联两端 |
| **Idempotent Receiver** | 接收方必须能安全处理重复消息。服务端：`resolve(command_id)` 找不到挂起项即忽略；设备端：`CommandResultCache` 按 `command_id` 去重，命中即重发上次结果而不重复执行——分配/结算是**物理操作**，重复执行不是多算一次而是真的再操作一次 |
| **Request-Reply** | `session.command` ↔ `session.command_result`，配对靠 `reply_to` |
| **Message Expiration** | 命令带 `deadline_ts`；服务端超时后清理挂起项，设备端过时拒绝执行 |
| **Guaranteed Delivery** | 设备侧快照走本地发件箱：ack 才删、未 ack 按序重发（断网不丢数据） |

## 8. 版本与发布

- [Semantic Versioning 2.0](https://semver.org)：`MAJOR.MINOR.PATCH`；0.x 阶段 MINOR=功能、PATCH=修复。
- [Keep a Changelog 1.1](https://keepachangelog.com)：`CHANGELOG.md` 逐版本记录，人写不贴 git log。
- **版本号单一来源**：`server/app/core/version.py` 的 `APP_VERSION`；
  与 `server/pyproject.toml`、`android/app/build.gradle.kts` 的三处一致性由
  `tests/test_version_consistency.py` 强制校验，非人工约定。
- APK `versionCode` 单调递增（OTA 强制）。
- [Conventional Commits 1.0](https://www.conventionalcommits.org)：`feat:`/`fix:`/`refactor:`/`docs:`/`chore:`，
  破坏性变更用 `!` 并在正文说明。

## 9. 文档

读者与用途决定文档的边界，**同一件事不写两处**。仓库文档只有这些：

| 文档 | 读者 | 写什么 |
|---|---|---|
| `AGENTS.md` | AI 协作者 | **入口**：一屏读完知道读哪份、门禁命令、硬约束、当前状态 |
| `README.md` | 首次接触者 | 项目是什么、怎么跑起来 |
| `docs/ARCHITECTURE.md` | 需要理解全局的人 | 分层与数据流（"为什么这样分"） |
| `docs/PROTOCOL.md` | 改协议的人 | 跨端消息表与字段 |
| `docs/STANDARDS.md`（本册） | 所有人 | 受约束的标准与执行机制 |
| `docs/RUNBOOK.md` | 运维 | 部署、备份、故障处置步骤 |
| `docs/DECISIONS.md` + `decisions/` | 想知道"为什么这样决定"的人 | MADR v3 决策记录，**只追加不重写**；被取代的在索引里标注 |
| `CONTRIBUTING.md` | 要动手改的人 | 环境准备与"任务 → 改哪里"的映射 |
| `deploy/README.md` | 改部署配置的人 | nginx/systemd 的同步方式与易错点 |

- 决策记录：[MADR v3](https://adr.github.io)（`docs/decisions/NNNN-slug.md`）。过期的**不删**——
  它记录了当时为什么这么选；但在 `DECISIONS.md` 的"已取代的决策"表里标明被谁取代。
- **文档与代码冲突时以代码为准，当场修文档**——不允许把过期文档留在仓库。
  只做关键词检索不够：整段描述的前提可能已变，必须通读该节再判断。

## 10. 环境值

禁止硬编码任何环境值（域名、路径、密钥、超时、端口）：

- 服务端：`server/.env`（模板 `server/.env.example`），经 `core/config.py` 的类型化 `Settings` 读取；
- 设备端：构建期唯一锚点是 `android/gradle.properties` 的 `serverDomain`；
- 运行时可变参数全部走远程配置，**键集与范围由 `modules/config/spec.py` 的登记表定义**
  （类型、上下限、归属、说明都在那一处；服务端据此校验、客户端据此渲染控件）。

例外：JSON Schema 的 `$id` 按规范必须是 URI（draft 2020-12 §9.1），
使用域名形式是规范要求而非环境耦合。

## 11. 扩展方式

新增能力一律沿以下接缝，不新造模式：

| 要加什么 | 接缝 | 需要的改动 |
|---|---|---|
| 一个远程配置键 | `config/spec.py` 的 `CONFIG_SPECS` | 加一条 ConfigSpec（类型/范围/归属/说明）+ 设备端 `RuntimeConfig.appliers` 加一行（键集合由 `test_config_spec.py` 对账） |
| 一个新协议消息 | `protocol/schema/` | 加 schema + `tests/test_protocol_schemas.py` 的 `KNOWN_TYPES` 与样例 + `docs/PROTOCOL.md` + 双端实现 |
| 一个新设备命令 | 设备端 `AgentService.dispatchCommand` | 加 action 分支 + 服务端下发点 + 锁定动作名的测试 |
| 一个新领域模块 | `server/app/modules/<domain>/` | `routes.py`（挂载于 `main.create_app`）+ `service.py` + `repository.py` |
| 一个新管理端点 | 对应领域的 `admin_router` | 路由 + 守卫 + 测试；依赖守卫放 `dependencies.py` |
