# ntu-shower

寄生在浴室中控设备上的控制系统。设备端只读浴室 App 的 `t_doorlist` 一张表，通过无障碍服务操作浴室 App 界面，并维护浴室 App 启动时读取的免费名单；服务端汇聚多台设备，提供 HTTP API、WebSocket 推送与网页管理台。旧系统 `ntu-remote-shower` 仍在部分楼层并行运行，两套系统使用独立的库、服务与端口。

## 系统组成

| 部分 | 职责 |
|---|---|
| 设备端 | Kotlin APK，运行在 Android 4.4.4 中控设备上；采集喷头状态、执行分配与结算、维持自身存活 |
| 服务端 | FastAPI 模块化单体；设备网关、领域模块、网页端静态文件与运维接口 |
| 协议 | WSS 上的 JSON 信封；消息结构与校验规则在 `protocol/schema/` |
| 部署 | nginx、systemd、备份脚本与发布脚本；唯一事实来源在 `deploy/` |

## 项目结构

| 路径 | 内容 |
|---|---|
| `README.md` | 项目入口、快速开始与文档导航 |
| `CONTRIBUTING.md` | 开发环境、质量门、提交、审阅与发布准备 |
| `STANDARDS.md` | 全生命周期工程标准 |
| `CHANGELOG.md` | 发布历史与用户可见变更 |
| `AGENTS.md` | AI 协作者的阅读顺序与停止条件 |
| `android/` | 设备端 APK |
| `server/` | 服务端、测试、工具与网页端 |
| `protocol/schema/` | 协议消息的 JSON Schema |
| `docs/` | 架构、参考、运行手册、安全、测试与决策记录 |
| `deploy/` | 部署资产唯一事实来源 |
| `config/keystore/` | APK 签名密钥目录；内容不入库 |

## 快速开始

### 服务端

```bash
cd server
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
cp .env.example .env
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

生产运行时为 Python 3.10；本机可以使用更高版本，但合并前必须在 Python 3.10 下通过质量门。

### 设备端

```bash
cd android
cp gradle.properties.example gradle.properties
./gradlew :app:assembleRelease
```

`gradle.properties` 内含签名配置，不入库；签名口令由密钥保管方提供。

## 质量门

完整命令与本地执行顺序见 [`CONTRIBUTING.md`](CONTRIBUTING.md) 的「质量门」小节。该小节与 `.github/workflows/ci.yml` 逐条对应；本地跑绿即 CI 跑绿。其他文档只链接该小节，不复制命令。

## 文档

| 文档 | 回答的问题 |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 开发环境、质量门、提交、审阅与发布准备 |
| [`AGENTS.md`](AGENTS.md) | AI 协作者的阅读顺序与停止条件 |
| [`CHANGELOG.md`](CHANGELOG.md) | 发布历史与用户可见变更 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 系统边界、组件、数据流、部署拓扑与时间轴定义 |
| [`docs/REFERENCE.md`](docs/REFERENCE.md) | 数据模型、字段语义、状态迁移、接口索引与取数口径 |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | 部署、回滚、备份、恢复、故障处置与日常运维 |
| [`docs/SECURITY.md`](docs/SECURITY.md) | 资产、数据分级、信任边界、安全控制与事件响应 |
| [`docs/TESTING.md`](docs/TESTING.md) | 测试策略、层级、环境、数据、覆盖率政策与证据 |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 约束代码的决策、取舍与后果 |
| [`STANDARDS.md`](STANDARDS.md) | 生命周期、文档、代码、文本、审阅与质量门规则 |

接口契约以 `protocol/schema/` 与代码路由为准；文档只做索引，不复制字段表。

## 运行模型

- 入口：`https://<public-domain>`，nginx 443 统一入口。
- 设备身份：`device_id = ANDROID_ID`；服务端信任后才接受设备通信。
- 访问控制：统一身份制。查看类接口公开；用户操作凭 `X-Identity` 与能力位；管理面要求 `admin` 能力位，并按设备作用域判定。
- 配置：设备运行参数由服务端远程下发；配置键登记在服务端配置登记表中，设备端不提供本地配置入口。
- 远程调试：`device.exec`、`screen.capture` 与 ADB 桥；调试通道按需开启并审计。

## 协作者入口

AI 协作者先读 [`AGENTS.md`](AGENTS.md)，再按其中的阅读顺序进入 `CONTRIBUTING.md`、`STANDARDS.md` 与 `docs/`。人类协作者从 `CONTRIBUTING.md` 开始。
