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
| RFC 9457 Problem Details（`application/problem+json`） | `core/errors.py` 统一装配 + 测试覆盖 |
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
| 认证 | 管理面 X-Admin-Token；用户面操作匿名（凭证由浴室系统校验，ADR-0034） |
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

| 范围 | 理由 | 替代验证 |
|---|---|---|
| `service/`、`receiver/`、`ShowerApp`、`features/automation/`、`features/remote/`（设备端） | 依赖 android.jar 桩、无障碍服务、Looper 线程、MediaCodec | 真机 E2E（分配/结算/拍照/截图/自更新均已实测） |
| `transport/GatewayClient`、`core/net`、`core/tls`、`core/shell`、`core/identity`（设备端） | 依赖真实 TLS 栈 / root shell / 系统服务 | 探针 APK 真机实测 |
| `core/config/AppConfig`（设备端） | 纯常量 | 编译期保障 |
| 端口不可用等内核竞态分支（服务端） | 需真实占用端口，进程内不可稳定复现 | 已用真实占用端口的测试覆盖，见 `test_edge_branches.py` |

**新增豁免必须先证明"不可测"**（列出尝试过的手段），并在本表登记。
禁止用豁免掩盖"懒得搭测试脚手架"。

## 6. 版本与发布

- [Semantic Versioning 2.0](https://semver.org)：`MAJOR.MINOR.PATCH`；0.x 阶段 MINOR=功能、PATCH=修复。
- [Keep a Changelog 1.1](https://keepachangelog.com)：`CHANGELOG.md` 逐版本记录，人写不贴 git log。
- **版本号单一来源**：`server/app/core/version.py` 的 `APP_VERSION`；
  与 `server/pyproject.toml`、`android/app/build.gradle.kts` 的三处一致性由
  `tests/test_version_consistency.py` 强制校验，非人工约定。
- APK `versionCode` 单调递增（OTA 强制）。
- [Conventional Commits 1.0](https://www.conventionalcommits.org)：`feat:`/`fix:`/`refactor:`/`docs:`/`chore:`，
  破坏性变更用 `!` 并在正文说明。

## 7. 文档

- 架构决策：[MADR v3](https://adr.github.io)（`docs/decisions/NNNN-slug.md`，只追加不重写）。
- 协议：`docs/PROTOCOL.md`；架构：`docs/ARCHITECTURE.md`；本册：工程与安全约束。
- **文档与代码冲突时以代码为准，当场修文档**——不允许把过期文档留在仓库。

## 8. 环境值

禁止硬编码任何环境值（域名、路径、密钥、超时、端口）：

- 服务端：`server/.env`（模板 `server/.env.example`），经 `core/config.py` 的类型化 `Settings` 读取；
- 设备端：构建期唯一锚点是 `android/gradle.properties` 的 `serverDomain`；
- 运行时可变参数全部走远程配置（`modules/config` 的键白名单）。

例外：JSON Schema 的 `$id` 按规范必须是 URI（draft 2020-12 §9.1），
使用域名形式是规范要求而非环境耦合。

## 9. 扩展方式

新增能力一律沿以下接缝，不新造模式：

| 要加什么 | 接缝 | 需要的改动 |
|---|---|---|
| 一个远程配置键 | `config/store.py` 的 `DEFAULT_CONFIG` | 加一行 + 设备端 `RuntimeConfig.appliers` 加一行（键名只写一次） |
| 一个新协议消息 | `protocol/schema/` | 加 schema + `tests/test_protocol_schemas.py` 的 `KNOWN_TYPES` 与样例 + `docs/PROTOCOL.md` + 双端实现 |
| 一个新设备命令 | 设备端 `AgentService.dispatchCommand` | 加 action 分支 + 服务端下发点 + 锁定动作名的测试 |
| 一个新领域模块 | `server/app/modules/<domain>/` | `routes.py`（挂载于 `main.create_app`）+ `service.py` + `repository.py` |
| 一个新管理端点 | 对应领域的 `admin_router` | 路由 + 守卫 + 测试；依赖守卫放 `dependencies.py` |
