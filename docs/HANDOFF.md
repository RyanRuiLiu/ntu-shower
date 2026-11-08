# 交接：v2 数据层重写分支

> 写给接下这个分支的人。状态截至 2026-11-08，HEAD 之后见 `git log`，`1065 passed` 全绿、覆盖率 96%。
> **数据层切换已全部完成**：22 张表的新层是唯一数据通路，旧建表与旧仓储已删除（全新库 23 张表 = 22 张 v2 表 + alembic 版本表）。

## 这条分支在做什么

把 ntu-shower 整仓往行业主流重做：SQLAlchemy 2.0 + Alembic、标准认证（Bearer 凭据 + 签名会话）、容器化编排（Caddy + compose）、停机窗口零兼容切换。

## 已完成

| 层 | 内容 | 验证 |
|---|---|---|
| 模型 | 22 张表 ORM（`server/app/models/`） | `alembic check` 无差异 |
| 持久化 | 全部域（`server/app/persistence/`：principals/devices/audit/ota/voice/config/extend_hours/doors/device_credentials） | 各域测试 |
| 认证 | `principals` 单主体表、Bearer 凭据、会话 Cookie、设备令牌 | `test_auth_endpoints.py` |
| **全部域切换** | access、devices（+12 域穿透）、ota、audit 全部、voice 全部、**config store、extend_hours、doors（977 行状态机）、stats** —— 服务层显式 session 注入，路由 `Depends(get_db)` / WS `_call` 短会话 / 后台任务 `session_scope()` 三入口定稿 | `1065 passed` / `mypy --strict` |
| 转换工具 | `server/tools/convert_v1_to_v2.py` 21 张表（`--token-output` 为可选运维通道） | 真实生产副本 53 万行逐表 ok，全链路演练绿 |
| 编排 | `deploy/stack/`（compose/Caddyfile/三个脚本） | `docker compose config` + `caddy fmt` |
| 旧层退役 | `init_schema` 与 8 处 `init_tables` 删除；6 个旧 repository 残壳删除；`rebuild_derived.py` 随旧层退场；表结构唯一归 Alembic | 全新库 23 张表，`alembic check` 无差异 |
| 调度 | `server/app/core/background.py` 统一三个间隔任务；审计行 `session_id` 恢复传值（doors 已在新层，外键成立） | 测试 |

## 切换定稿的语义（后续改动的依据）

- **审计/投递类表留设备文本键** `device_key`（api_calls/command_audits/device_logs/device_link_events/background_task_runs/device_configs）：事实性不依赖设备在册；其余状态表（clock_shift、door 系）行随设备外键 CASCADE
- **原始帧的 source 列是 TEXT**：原始层不解释，协议外取值照记照 ack；枚举词汇只属派生层
- **RFC 3339 文本与 datetime 的转换收敛在持久化边界**：服务层全程持有文本，`_dt`（可空）/ `_moment`（必填）各一处
- **重复投递的原始帧按「已落库」回真**；设备不在册的帧抛错扣回执（ack ⇔ 已落库）

## 切换演练（2026-11-09 已完成，全链路绿）

合成 v1 库（21 张源表 42 行，含孤儿行/空帧/开放会话边界）跑通整条切换链：

1. **转换**：`convert_v1_to_v2.py` 21/21 行数 ok（孤儿行按表计数跳过）→ 盖 `alembic_version` 到 head → `alembic upgrade head` no-op
2. **服务**：真实 uvicorn 起在转换库上，13 项检查全过——转换数据可见（doors 现态/设备清单/配置覆盖生效）、problem+json 边界、设备令牌握手（`link.hello_ack` 带 trusted 与转换来的配置）、坏令牌 close 1008
3. **容器**：镜像构建成功；生产形态容器（`--read-only --tmpfs /tmp --cap-drop ALL`）起在转换库上正常服务

演练抓出并已修复的缺陷（都在线上才会炸）：
- 转换工具：`device_configs` 缺 `device_key`（unique 必撞）、审计四表没带文本键、没盖 alembic 戳（migrate 必失败）、`usage_segment` 孤儿跳过没进核验扣减表
- 启动：`create_app` 的 `db.init_db()` 按默认相对路径建连接，read_only 容器必崩——已改为 lifespan 探活真实连接

**生产切换前仍需（演练覆盖不到的）**：用**真实生产库副本**再跑一遍转换与核对（合成库只验证形状，行数、时长边界与真实数据分布要在副本上复核）；Caddy 层只在服务器上实测（`network_mode: host` 是 Linux 特性，本地 Windows 无法排练）。

## 剩余工作（分支外，用户执行）

1. **设备接入凭据：claim 自注册已实现**（决策 2026-11-09）：APK 构建期内置共享 claim 令牌，设备首次连接调 `POST /api/v1/devices/enroll` 自动换每台自己的凭据；服务端 `DEVICE_ENROLL_TOKEN` 留空即通道关闭。`--token-output` 退为可选运维通道。管理面凭据轮换端点未接入（`list_for_device`/`revoke_all` 已备好）
2. **待定夺**：主页托管（独立容器 vs Caddy 直接托管 `/srv/www`）；两份 compose.yaml 合并（顶层单服务版 vs `deploy/stack` 全栈版）

## 切换执行清单（停机窗口，用户执行）

1. 构建并经旧服务端 OTA 发布带自注册能力的新 APK（gradle `-PdeviceEnrollToken=<共享claim>` 与服务器 .env 的 `DEVICE_ENROLL_TOKEN` 同值），等 6 台设备升级完成——它们继续在旧服务上正常运行
2. 无人使用时停旧服务
3. `deploy/stack/scripts/backup.sh` 备份
4. `PYTHONPATH=. python tools/convert_v1_to_v2.py --source 旧库 --target 新库`
5. 起新服务（migrate 服务跑 `alembic upgrade head`，no-op）+ Caddy 接管 80/443
6. 设备陆续重连：每台自动自注册换凭据，管理台确认 6 台在线与信任位

## 工作流约定

- 改代码后先跑相关测试文件（`pytest tests/test_gateway.py -q`），收尾才全量（65 秒）
- 静态检查：`ruff check --fix app tests && mypy app --strict`
- 术语门禁全仓扫描（含 md/sh），禁用词见 `tests/test_language_rules.py`；新增中文文案前先查它
- 测试读数据一律走持久化视图（`tests/door_link.py`、`device_link.py`），不写裸 SQL——存储格式是持久化层的内部事务

## 红线

- 不做任何兼容层、适配层、过渡开关——切换策略是零兼容
- `main` 分支不触碰
- 每个决策先查官方文档，不凭记忆
