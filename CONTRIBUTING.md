# CONTRIBUTING.md — 开发手册

面向第一次接触本仓库的人。目标是：**不提问也能完成一次改动并让 CI 变绿**。

标准与约束见 [STANDARDS.md](STANDARDS.md)；部署与故障处置见 [RUNBOOK.md](RUNBOOK.md)。

## 1. 环境准备

```bash
# 服务端（生产运行时是 Python 3.10；本地用 3.10~3.13 均可，但本地版本高于 3.10 时
# 无法发现解释器差异问题——这类问题由 CI 的 3.10 与部署前预检兜住）
cd server
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
cp .env.example .env        # 必须把 ADMIN_TOKEN 改成非占位值，否则启动自检会拒绝启动

# 设备端
cd android
cp gradle.properties.example gradle.properties
# 向密钥保管方索取签名口令填入 keystorePassword（仓库里只有占位符）
```

## 2. 一次改动的标准流程

```bash
# ① 改代码
# ② 跑门禁（CI 跑的是同一组命令；本地 format 用 . 直接改文件，CI 用 --check 只校验）
cd server
ruff format .
ruff check . --fix
mypy app --strict
pytest -q --cov=app --cov-report=term-missing

cd ../android
./gradlew detekt :app:lintDebug :app:testDebugUnitTest :app:jacocoUnitTestCoverageVerification

# ③ 提交（Conventional Commits，正文说明"为什么"）
git commit -m "fix: 修复 OTA force 发布重复推送" -m "mark_installed_upto 定义了却从未被调用……"
```

**门禁不绿就不算完成。** 覆盖率门槛只升不降；确实无法测试的分支，
必须写清"为什么不可测"与"替代验证"，并登记到 STANDARDS 的赦免清单。

## 3. 代码约定速查

完整的命名表见 [STANDARDS.md §2](STANDARDS.md)。三条最容易踩的：

- **时间**：一律经 `core/clock.py`，不要自己 `isoformat`/`fromisoformat`（那是 RFC 3339 语义的唯一来源）；
- **JSON 边界**：设备报文是外部输入，取值一律用 `core/protocol.py` 的 `payload_of` / `text_of` /
  `list_of` / `as_int`，不要散落的 `isinstance` 判断或 `int(...)`；
- **异常**：清理路径用 `contextlib.suppress(...)`，业务路径精确捕获；
  禁止 `except Exception: pass`（后台循环确需宽捕获时写 `# noqa: BLE001` 并注明理由）。

## 4. 常见任务

| 任务 | 改哪里 |
|---|---|
| 加一个远程配置键 | `server/app/modules/config/spec.py` 的 `CONFIG_SPECS` 加一条（类型/范围/单位/归属/说明）+ 设备端 `core/config/RuntimeConfig.kt` 的 `appliers` 加一行；两端键集合由 `test_config_spec.py` 对账 |
| 加一个协议消息 | `protocol/schema/<type>.json` + `tests/test_protocol_schemas.py` 的 `KNOWN_TYPES` 与样例 + `docs/PROTOCOL.md` 总表 + 双端实现（`PROTOCOL.md` 缺登记会被测试拦下） |
| 加一个设备命令 | 设备端 `AgentService.dispatchCommand` 加分支 + 服务端下发点 + 锁定动作名的测试（见 `tests/test_admin_debug.py`） |
| 加一个领域模块 | `server/app/modules/<domain>/`：`routes.py` + `service.py` + `repository.py`，在 `main.create_app` 挂载 |
| 加一个数据库表 | 该领域 `repository.py` 的 `init_tables`；表名单数、时间列 RFC 3339、时间字段 `_at` 后缀 |
| 升级版本号 | 只改 `server/app/core/version.py` 的 `APP_VERSION`，另两处由 `tests/test_version_consistency.py` 校验一致性 |

## 5. 目录速览

```
android/app/src/main/java/com/ryanliu/ntushower/
  core/            基础设施：clock / config / dns / identity / net / shell / tls
  transport/       网关客户端与协议编解码（与服务端 core/protocol.py 对应）
  features/        doors 采集 · automation 自动化 · remote 远程调试 · selfmanage 自更新
  service/         常驻服务（AgentService）与无障碍服务
  receiver/        看门狗与开机广播

server/app/
  core/            协议 · 时钟 · 配置 · 数据库 · 事件总线 · 鉴权 · 错误 · 版本
  gateway/         设备 WS 网关与连接注册表
  modules/<领域>/   routes（HTTP 编排）/ service（领域逻辑）/ repository（持久化）/ dependencies（守卫）
  web/             网页端：零构建静态文件，挂在根路径

protocol/schema/   全部协议消息的 JSON Schema（单一事实源）
docs/              ARCHITECTURE · PROTOCOL · STANDARDS · CONTENT · DECISIONS · RUNBOOK · 本册
deploy/            nginx / systemd / backup 的唯一事实来源
```

## 6. 提交前自检清单

- [ ] `ruff format` + `ruff check` 无输出
- [ ] `mypy app --strict` 通过
- [ ] `pytest` 全绿且覆盖率未下降
- [ ] 设备端 `gradlew detekt` 与 JaCoCo 校验通过
- [ ] 新增协议消息已三处同步（schema / PROTOCOL.md / 双端）
- [ ] 改了版本号则三处一致
- [ ] 提交信息符合 Conventional Commits，正文写清动机而非复述 diff
