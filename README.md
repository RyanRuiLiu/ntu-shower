# ntu-shower

寄生在浴室中控设备上的控制系统。设备端只读浴室 App 的 `t_doorlist` 一张表，通过无障碍服务操作浴室 App 界面，并维护浴室 App 启动时读取的免费名单；服务端汇聚多台设备，提供 HTTP API、WebSocket 推送与网页管理台。

## 系统组成

| 部分 | 职责 |
|---|---|
| 设备端 | Kotlin APK，运行在 Android 4.4.4 中控设备上；采集喷头状态、执行分配与结算、维持自身存活 |
| 服务端 | FastAPI 模块化单体；设备网关、领域模块、网页端静态文件与运维接口 |
| 协议 | WSS 上的 JSON 信封；消息结构与校验规则在 `protocol/schema/` |
| 部署 | nginx、Docker Compose、备份脚本与发布脚本；唯一事实来源在 `deploy/` 与顶层 `compose.yaml` |

## 项目结构

| 路径 | 内容 |
|---|---|
| `README.md` | 项目入口、快速开始与文档导航 |
| `CONTRIBUTING.md` | 开发环境、质量门、提交、审阅与发布准备 |
| `CHANGELOG.md` | 发布历史与用户可见变更 |
| `AGENTS.md` | AI 协作者的阅读顺序与停止条件 |
| `.github/workflows/` | 持续集成 |
| `.githooks/` | 提交信息校验钩子 |
| `android/` | 设备端 APK |
| `server/` | 服务端、测试、工具与网页端 |
| `protocol/schema/` | 协议消息的 JSON Schema |
| `docs/` | 架构、参考、运行手册、安全与决策记录 |
| `deploy/` | 部署资产唯一事实来源 |
| `server/tools/` | 仓库级工具：契约生成与排查脚本 |
| `backups/` | 本机数据备份与校验和；备份不入库 |
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

生产运行时为 Python 3.13；本机可以使用同一版本，合并前必须在 Python 3.13 下通过质量门。

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
| [`docs/architecture.md`](docs/architecture.md) | 系统边界、组件、数据流、部署拓扑与时间轴定义 |
| [`docs/reference.md`](docs/reference.md) | 状态派生规则、易混字段对照、取数口径与不变量 |
| [`docs/runbook.md`](docs/runbook.md) | 部署、回滚、备份、恢复、故障处置与日常运维 |
| [`docs/security.md`](docs/security.md) | 资产、数据分级、信任边界、安全控制与事件响应 |
| [`docs/adr/`](docs/adr/) | 约束代码的决策、取舍与后果 |

接口契约以 `protocol/schema/` 与代码路由为准；文档只做索引，不复制字段表。

## 协作者入口

AI 协作者先读 [`AGENTS.md`](AGENTS.md)，再按其中的阅读顺序进入 `CONTRIBUTING.md` 与 `docs/`。人类协作者从 `CONTRIBUTING.md` 开始。
