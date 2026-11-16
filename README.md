# ntu-shower

[![CI](https://github.com/RyanRuiLiu/ntu-shower/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanRuiLiu/ntu-shower/actions/workflows/ci.yml)

无人值守的校园浴室自动化控制系统：设备端寄生在浴室中控上，用无障碍服务代替人工操作浴室 App；服务端集中管理多台设备，提供 HTTP API、WebSocket 推送和网页管理台。

## 背景

浴室 App 只能现场操作：开阀要人站在机器前点界面，断电时间一到全体强制释放。设备没有 NTP，时钟漂移和人为拨动都无法自愈。

本系统不改浴室 App 的代码，用无障碍服务驱动它完成全部操作；缺的能力（计时、自动化、集中管理）由服务端补上。设备端自带布防与自愈：服务端消失后按最后一次配置断电，浴室 App 卡死自动重启，掉电来电后恢复全部状态。

## 工作原理

```text
浴室中控设备（Android 4.4，Kotlin）
 │  无障碍服务驱动浴室 App、直控现场硬件、采集喷头状态
 │  WSS · JSON 信封（protocol/schema/）
 ▼
服务端（FastAPI 模块化单体）
 ├─ 设备网关：凭据握手、命令下发与回执、幂等接收
 ├─ HTTP API（scope 权限化）+ WebSocket 推送 → 网页管理台（React）
 └─ 后台自动化：会话与喷头生命周期的判据、调度与执行
```

服务端与设备端共享一套机器契约：动作名、原因码、配置键由生成器从服务端登记表同步到两端，不一致会在 CI 直接报错。

## 使用

### 服务端

```bash
cd server
python3.13 -m venv .venv
python3.13 -m pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/alembic upgrade head                      # 首次运行先建 schema（Windows: .venv\Scripts\alembic.exe）
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

| 文档 | 内容 |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 开发环境、质量门、提交与审阅 |
| [`docs/architecture.md`](docs/architecture.md) | 系统边界、组件、数据流、部署拓扑与时间轴定义 |
| [`docs/reference.md`](docs/reference.md) | 状态派生规则、字段对照、取数口径与不变量 |
| [`docs/runbook.md`](docs/runbook.md) | 部署、回滚、备份、恢复、故障处置 |
| [`docs/security.md`](docs/security.md) | 资产、数据分级、信任边界、安全控制 |
| [`SECURITY.md`](SECURITY.md) | 漏洞报告渠道与支持范围 |
| [`docs/adr/`](docs/adr/) | 决策记录 |
| [`docs/device-poweroff.md`](docs/device-poweroff.md) · [`docs/water-board.md`](docs/water-board.md) | 断电布防与水控板直控的现场依据 |
| [`CHANGELOG.md`](CHANGELOG.md) | 发布历史 |
| [`AGENTS.md`](AGENTS.md) | AI 协作者的阅读顺序与停止条件 |

接口契约以 `protocol/schema/` 与代码路由为准；文档只做索引，不复制字段表。

## 项目结构

```text
android/          设备端 APK（Kotlin，API 19）
server/           服务端、测试、网页端与仓库级工具
protocol/schema/  协议消息的 JSON Schema
docs/             架构、参考、运行手册、安全与决策记录
deploy/           部署资产唯一事实来源（nginx、备份、发布）
```

## License

[MIT](LICENSE)
