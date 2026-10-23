# ntu-shower

NTU 浴室系统二代：寄生在设备上的 NTU Shower（Android 4.4.4 老中控）+ 单服务端 + API 优先。
替代 `ntu-remote-shower`（旧系统并行运行至退役）。

设备端三件主功能：**读**（浴室 App 数据库状态采集）/ **操作**（无障碍自动化：分配·结算·拍照）/
**活**（自更新、看门狗、崩溃自愈等自生命周期）。其余功能均为该地基上的扩展。

## 项目结构

| 目录 | 职责 |
|---|---|
| `AGENTS.md` | **AI 协作者的入口**：一屏读完知道读哪份文档、门禁命令、硬约束 |
| `android/` | 设备端 APK（Kotlin，minSdk/targetSdk 19）：`core/` 基础设施 · `transport/` 网关客户端与协议编解码 · `features/` doors 采集 / automation 自动化 / notice 屏幕提示 / voice 语音播报 / remote 远程调试 / selfmanage 自更新 · `service/` 常驻服务 |
| `server/` | FastAPI 模块化单体：`gateway/` 设备 WS 网关 · `modules/` access/sessions/doors/devices/config/ota/bridge/audit 各领域 · `core/` 协议/时钟/审计/错误/事件总线 · `web/` 网页端（零构建静态文件） |
| `protocol/` | 协议单一事实源：全部消息类型的 JSON Schema（draft 2020-12），CI 样例校验 |
| `docs/` | `ARCHITECTURE` 架构 · `DECISIONS` 决策 · `RUNBOOK` 运维 |
| `deploy/` | 部署资产唯一事实来源：nginx 站点配置 + systemd 单元 + 备份脚本 |
| `config/keystore/` | APK 签名密钥（gitignored，**全系统唯一长期绑定物件**，须异地备份） |

## 运行模型

- 唯一入口：`https://ntu-shower.iseekdream.com`（443 统一入口，单张通配符证书，nginx 路径分流）
- 设备身份：`device_id = ANDROID_ID`，后台人工点"信任"后通信（见 protocol/schema/）
- 访问控制：**统一身份制**。查看类接口无需凭据；其余一切凭 `X-Identity` 头——
  用户能力位：`session`（分配/结算）、`photo`（拍照）；管理面（截图、远程执行、
  配置、身份名单、发布、审计）要求 `admin` 能力位，能力位 × 设备作用域双维判定
  （docs/DECISIONS.md）。**无注册流程**，授权经 `/api/v1/identities` 预置
- 配置：设备运行参数全部由服务端远程下发，键的类型与范围登记在 `config/spec.py`，设备零本地配置
- 远程调试：`device.exec`（root shell）、`screen.capture`（全屏截图）、调试通道（零新端口，见 docs/DECISIONS.md）

## 快速开始

```bash
# 服务端
cd server
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
cp .env.example .env          # 无需任何令牌配置：管理台身份在网页端身份页签发
.venv/Scripts/uvicorn app.main:app --reload --port 8000

# 设备端（构建需先复制 android/gradle.properties.example，并向密钥保管方索取签名口令）
cd android && ./gradlew :app:assembleRelease
```

## 质量门

与服务端 CI（`.github/workflows/ci.yml`）执行的命令完全一致——本地跑绿即 CI 跑绿：

```bash
# 服务端：格式 + 静态检查 + 严格类型 + 测试与覆盖率门
cd server
ruff format --check app tests && ruff check app tests
mypy app --strict
pytest -q --cov=app --cov-report=term-missing

# 设备端：静态分析 + 平台 lint + 单测 + 覆盖率门
cd android
./gradlew detekt :app:lintDebug :app:testDebugUnitTest :app:jacocoUnitTestCoverageVerification
```

标准与约束（含豁免清单及其理由）见 [AGENTS.md](AGENTS.md)。
协议变更必须三处同步：`protocol/schema/`、服务端实现、设备端实现。

**AI 协作者先读 [AGENTS.md](AGENTS.md)**——那里有一屏能读完的入口、门禁与硬约束。
