# 交接：v2 数据层重写分支

> 写给接下这个分支的人。状态截至 2026-11-08，HEAD 之后见 `git log`，`1066 passed` 全绿、覆盖率 96%。

## 这条分支在做什么

把 ntu-shower 整仓往行业主流重做：SQLAlchemy 2.0 + Alembic、标准认证（Bearer 凭据 + 签名会话）、容器化编排（Caddy + compose）、停机窗口零兼容切换。

## 已完成

| 层 | 内容 | 验证 |
|---|---|---|
| 模型 | 22 张表 ORM（`server/app/models/`） | `alembic check` 无差异 |
| 持久化 | 8 个域（`server/app/persistence/`） | 各域测试 |
| 认证 | `principals` 单主体表、Bearer 凭据、会话 Cookie、设备令牌 | `test_auth_endpoints.py` |
| access 域 | 已完整切到新层（依赖注入） | 全套测试 |
| **devices 域 + 全部调用方** | 服务层显式 session 注入；12 个域的调用点（gateway/voice/sessions/extend_hours/status_line/access/stats/freelist/water/bridge/config）全批切完；WS 走 `_call` 短会话、后台任务由 Background 每轮 `session_scope()` 注入 | `1066 passed` / `mypy --strict` |
| 转换工具 | `server/tools/convert_v1_to_v2.py` 21 张表 + `--token-output` 给现有设备补凭据 | 生产副本 48 万行逐表 ok，6 台设备各一枚摘要 |
| 编排 | `deploy/stack/`（compose/Caddyfile/三个脚本） | `docker compose config` + `caddy fmt` |
| 调度 | `server/app/core/background.py` 统一三个间隔任务 | 测试 |

## 剩余工作（按顺序）

### ~~五域同批切换~~（已完成，`724fa21`）

实际穿透面比预想大：`device_action` 与 `require_trusted_online_device` 把 freelist/water/bridge/config 也挂了进来——**12 个域一批**。经验：
- 会话三入口：路由 `Depends(get_db)`、WS `ws.app.state.database` 短会话（`gateway/routes.py` 的 `_call`）、后台任务 `core/background` 每轮 `session_scope()`、asyncio 任务入口自取 `session_scope()`
- 测试里 stub（fake_send/device_action/reject 等）全部要加 session 首参——量大但机械
- `as_record` 对非数组 capabilities 按未声明态处理（守卫链上不能因畸形数据崩）
- 旧 `modules/devices/repository.py` 只剩 `init_tables()`（第二批删）

### 其余域切换（下一步，已完成 4/8）

已切：access、devices、ota、**audit 全部**（HTTP 中间件写 api_calls、命令审计 command_audits、后台任务 background_task_runs、bridge 的审计调用；scheduler 的 insert_background_task_run 也切了）。审计三表都留**设备文本键**列（`device_key`）：审计的事实性不依赖设备是否在册（未在册设备的行不能丢）。

剩余（按建议顺序）：
1. **voice 存储**：service 50 处 `repository.` 直调，`persistence/voice.py` 函数已备好（含 claim/finish_delivery）；第一批已把 create/update/list/push/schedule 链加了 session
2. **config store**：5 个存储函数切 `persistence/config.py`，但 `effective()/device_effective()` 调用方遍布全域——又是一次小规模穿透（gateway ping loop/scheduler/extend_hours 等，大多已在 `_call`/session_scope 框架内）
3. **extend_hours 存储**（clock_shift 表）与其 service 内 `repository.get/record/clear/list_shifted`
4. **doors**（977 行状态机，最大）：`persistence/doors.py` 函数已备好；**注意** scheduler 审计行的 `session_id` 过渡期置 None（旧表会话 id 引用新表 usage_sessions 无对应行），doors 切完后恢复传 `subject.session_id` 并改回测试断言
5. **stats 存储**（door_live/usage_* 读路径）——依赖 doors 先切
6. 最终态：删各域旧 repository 与 `init_schema()` 的旧建表

第二批实测教训：
- 新表名复数：`api_calls`/`command_audits`/`device_logs`/`device_link_events`/`background_task_runs`
- BackgroundOutcome 枚举补齐了实际出现的全部终局值（含 rejected/failed/assign-expired/no-password/not-applicable）
- **迁移重生成**：模型改后必须 `rm migrations/versions/*.py && rm -rf __pycache__` 再 autogenerate（alembic 会在旧库上拒绝生成）；迁移文件名每次都变
- BackgroundTaskRun/CommandAudit 的 `session_id` 外键在 doors 未切前是旧表 id——传 None

### 设备令牌下发

转换工具的 `--token-output` 已在切换时给每台现有设备签发凭据，明文写进 600 权限文件、
库中只留摘要。剩下的是把这枚明文写进设备（管理员配置或首次连接引导）。`app/persistence/device_credentials.py`
另有 `list_for_device` / `revoke_all` 供管理面轮换凭据用，端点尚未接入。

### 待定夺的事项

- 主页托管：定稿是独立容器，compose 里还是 Caddy 直接托管 `/srv/www`
- 两份 compose.yaml：顶层单服务版 vs `deploy/stack` 全栈版，合并前需确认共享 CI workflow 是否依赖根 compose

## 切换执行清单（停机窗口，用户执行）

1. 发新版 APK（带设备令牌能力）
2. 无人使用时停旧服务
3. `deploy/stack/scripts/backup.sh` 备份
4. `PYTHONPATH=. python tools/convert_v1_to_v2.py --source 旧库 --target 新库 --token-output /root/device-tokens.txt`
5. 把 `device-tokens.txt` 里的明文逐台写进设备，用完删除该文件
6. 起新服务（migrate 服务跑 `alembic upgrade head`）

## 工作流约定

- 改代码后先跑相关测试文件（`pytest tests/test_gateway.py -q`），收尾才全量（65 秒）
- 静态检查：`ruff check --fix app tests && mypy app --strict`
- 术语门禁全仓扫描（含 md/sh），禁用词见 `tests/test_language_rules.py`；新增中文文案前先查它

## 红线

- 不做任何兼容层、适配层、过渡开关——切换策略是零兼容
- `main` 分支不触碰
- 每个决策先查官方文档，不凭记忆
