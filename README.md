# ntu-shower

[![CI](https://github.com/RyanRuiLiu/ntu-shower/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanRuiLiu/ntu-shower/actions/workflows/ci.yml)

一套运行在校园浴室里的无人值守自动化控制系统：设备端寄生在浴室中控上代替人工操作浴室 App，服务端把多台浴室的运行操作与状态集中到一块面板，并提供权限化的对外 API。

## 为什么存在

浴室 App 本身只支持现场操作：有人在机器前点击界面才能开阀，到了断电时间全员强制释放，设备没有 NTP、时钟漂移也无法自愈。本系统在不修改浴室 App 的前提下，用无障碍服务驱动它完成全部操作，并在服务端补上它缺失的能力：

- **无人值守**——后台任务按判据自动完成会话的收尾、延续与恢复；设备崩溃、掉电、浴室 App 卡死均有布防与自动恢复。
- **集中可见**——全部楼层的空闲状态、每次命令的审计记录、每一条配置变更，汇总在一块网页管理台。
- **双时间轴**——设备无 NTP 且时钟会被拨动，服务端以服务端时间轴锚定全部事件，统计与判定不依赖设备钟。

## 工作原理

```text
浴室中控设备（Android 4.4，Kotlin APK）
 │  无障碍服务驱动浴室 App、直控现场硬件、采集喷头状态
 │  WSS · JSON 信封（protocol/schema/）
 ▼
服务端（FastAPI 模块化单体）
 ├─ 设备网关：凭据握手、命令下发与回执、幂等接收
 ├─ HTTP API（scope 权限化）+ WebSocket 推送 → 网页管理台（React）
 └─ 后台自动化：会话与喷头生命周期的判据、调度与执行
```

## 核心特性

- **寄生式自动化**——不改浴室 App 一行代码：无障碍服务读它的界面、替它点按，装上即用。
- **权限化对外 API**——`resource:action` scope 字典 + 设备作用域，密钥长期有效、逐枚轮换（GitHub PAT 同型）。
- **审计闭环**——HTTP 调用、设备命令、后台任务三路审计统一保留期；命令幂等可重发。
- **失效安全**——服务端消失时设备按最后一次布防断电；拨钟、校时、重启都有防护窗口。
- **机器契约**——三端（服务端/设备端/网页）共享的动作名、原因码、配置键由生成器逐字节同步，漂移即红。

## 快速开始

### 服务端

```bash
cd server
python3.13 -m venv .venv
python3.13 -m pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload --port 8000   # Windows: .venv\Scripts\uvicorn.exe
```

生产运行时为 Python 3.13；合并前必须在 Python 3.13 下通过质量门。

### 设备端

```bash
cd android
cp gradle.properties.example gradle.properties
./gradlew :app:assembleRelease
```

`gradle.properties`（gitignore）持有签名与构建期注入配置：服务域名、设备自注册 claim 令牌——留空即关闭注册通道。

## 文档

| 文档 | 回答的问题 |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 开发环境、质量门、提交、审阅与发布准备 |
| [`docs/architecture.md`](docs/architecture.md) | 系统边界、组件、数据流、部署拓扑与时间轴定义 |
| [`docs/reference.md`](docs/reference.md) | 状态派生规则、易混字段对照、取数口径与不变量 |
| [`docs/runbook.md`](docs/runbook.md) | 部署、回滚、备份、恢复、故障处置与日常运维 |
| [`docs/security.md`](docs/security.md) | 资产、数据分级、信任边界、安全控制与事件响应 |
| [`docs/adr/`](docs/adr/) | 约束代码的决策、取舍与后果 |
| [`AGENTS.md`](AGENTS.md) | AI 协作者的阅读顺序与停止条件 |
| [`CHANGELOG.md`](CHANGELOG.md) | 发布历史与用户可见变更 |

接口契约以 `protocol/schema/` 与代码路由为准；文档只做索引，不复制字段表。

## 项目结构

```text
android/          设备端 APK（Kotlin，API 19）
server/           服务端、测试、网页端与仓库级工具
protocol/schema/  协议消息的 JSON Schema
docs/             架构、参考、运行手册、安全与决策记录
deploy/           部署资产唯一事实来源（nginx、备份、发布）
```

## 许可

[MIT](LICENSE)
