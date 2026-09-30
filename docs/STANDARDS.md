# STANDARDS.md — 标准登记册（约束性）

本项目受下列真实行业标准约束。**任何代码变更（含 AI 生成）必须符合本册**；
与标准冲突的实现按违规处理。每项标准注明执行机制——没有执行机制的标准只是愿望。

## 1. 语言与代码风格

| 标准 | 适用 | 执行机制 |
|---|---|---|
| PEP 8 / PEP 484（类型注解） | 服务端 | ruff（`E,F,I,UP,B` 规则集，零违规）+ mypy `disallow_untyped_defs` |
| ruff format（black 兼容格式） | 服务端 | CI `ruff format --check` |
| Kotlin 官方编码约定 | 设备端 | detekt 2.0（Kotlin Analysis API 架构）+ ktlint 规则集（autoCorrect），本地与 CI 同执行 |
| snake_case 数据库字段、`_at` 时间后缀、单数表名 | 服务端 | 评审约束 + repository 单一来源 |
| 一文件一顶层声明（Kotlin） | 设备端 | 评审约束 |

## 2. API 与协议

| 标准 | 适用 | 执行机制 |
|---|---|---|
| RFC 9457 Problem Details（problem+json） | 服务端 HTTP 错误 | `core/errors.py` 统一装配，测试覆盖 |
| OpenAPI 3.1 | 服务端接口 | FastAPI 自动生成，`/docs` 可视 |
| JSON Schema draft 2020-12 | 全部协议消息 | `protocol/schema/` + `test_protocol_schemas.py` 样例校验 |
| REST 命名（复数名词、层级路径、语义化状态码） | 服务端路由 | 评审约束 |
| 协议变更三处同步 | 全部 | schema、`docs/PROTOCOL.md`、双端实现，评审检查单 |

## 3. 安全

对照 [OWASP API Security Top 10 (2023)](https://owasp.org/API-Security) 逐条处置：

| 项 | 处置 |
|---|---|
| API1 对象级授权 | 无用户自有资源对象（设备与门是全局资源），user 角色守卫覆盖写操作 |
| API2 认证失效 | 管理面 X-Admin-Token；用户面操作匿名（凭证由浴室系统验证，ADR-0034）；调用方身份体系（小程序/APP）接入时走 provider 化令牌签发 |
| API3 对象属性级授权 | 请求体 pydantic 严格模型；配置键白名单（`CONFIG_KEYS`） |
| API4 资源消耗 | **按真实功能设计**：每设备同时至多一条在途命令（409 device.busy，设备自动化本就串行）；OTA 上传 `max_ota_bytes`（默认 50MB）上限 |
| API5 功能级授权 | 路由级角色依赖（`require_user` / `require_admin`） |
| API6 敏感业务流 | 分配/结算有门态预检（409），设备端自动化遮盖层防人为干扰 |
| API7 SSRF | 服务端不代取外部 URL |
| API8 安全配置 | 密钥全走环境变量（`.env`，gitignored）；问题 type URI 待正式域名后更新 |
| API9 清单管理 | 协议版本常量单一来源（`v=1`），破坏性变更升版 |
| API10 API 消费安全 | 设备端仅消费本服务端（内置 DNS + 固定域名 + sha256 校验 OTA） |

## 4. 测试与质量门

**策略：全面覆盖 + 赦免制。** 目标是能测尽测；确实无法在自动化环境测试的部分，
逐项登记于下方赦免清单（含理由），覆盖率门槛只统计可测部分。

### 服务端（pytest + pytest-cov）

- 门槛：`fail_under = 95`（当前 97%），CI 强制。
- 103 个测试覆盖：全部 API 面、协议校验、命令往返、配置下发、鉴权、审计、边界分支。

### 设备端（JUnit + JaCoCo）

- 门槛：`jacocoUnitTestCoverageVerification` ≥ 80%（按纯逻辑类统计，本地与 CI 同执行）。
- 当前 87.5%，21 个 JVM 单测。

### 赦免清单（唯一合法的不测理由）

| 范围 | 理由 | 替代验证手段 |
|---|---|---|
| `service/`、`receiver/`、`ShowerApp`、`features/automation/`（设备端） | 依赖 android.jar 桩、无障碍服务、Looper 线程——JVM 上无意义 | 真机 E2E（分配/结算/拍照/自更新均已实测） |
| `transport/GatewayClient`、`core/net`、`core/tls`、`core/shell`、`core/identity`（设备端） | 依赖真实 TLS 栈/root shell/系统服务 | 探针 APK 三轮真机实测（DNS/TLS/套件） |
| `core/config/AppConfig`（设备端） | 纯常量，无可执行逻辑 | 编译期保障 |
| `gateway/routes.py` 连接拆除分支（服务端） | WebSocket 断连竞态在测试进程内不可复现 | 真机重连观测（历次部署验证） |
| 常量键名外的 gradle/构建脚本 | 声明式配置 | 构建成功即验证 |

**赦免新增规则**：新增赦免必须先证明"不可测"（说明尝试过的手段），
并在本表登记理由与替代验证手段。禁止用赦免掩盖"懒得搭测试脚手架"。

## 5. 版本与发布

- [Semantic Versioning 2.0](https://semver.org)：`MAJOR.MINOR.PATCH`；0.x 阶段 MINOR=功能、PATCH=修复。
- [Keep a Changelog 1.1](https://keepachangelog.com)：`CHANGELOG.md` 逐版本记录，人写不贴 git log。
- [Conventional Commits 1.0](https://www.conventionalcommits.org)：**git 仓库建立后启用**（`feat:`/`fix:`/`docs:`/`refactor:`/`chore:`，破坏性变更用 `!` + `BREAKING CHANGE` footer）。
- APK `versionCode` 单调递增、`versionName` 与服务端发布记录一致（OTA 强制）。

## 6. 文档

- 架构决策：[MADR v3](https://adr.github.io)（`docs/decisions/NNNN-slug.md`，只追加不重写）。
- 协议：`docs/PROTOCOL.md`；架构：`docs/ARCHITECTURE.md`；本册：质量与安全约束。
- 文档与代码冲突时：以代码为准，**当场**修文档。

## 7. 环境值

- 禁止硬编码任何环境值（域名、路径、密钥、超时）。
- 服务端：`server/.env`（模板 `.env.example`）；设备端构建期唯一锚点：`android/gradle.properties` 的 `serverDomain`。
- 运行时可变参数全部走远程配置（`modules/config`，14 键白名单）。
