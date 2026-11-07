# 交接：v2 数据层重写分支

> 写给接下这个分支的人。状态截至 2026-11-07 夜，HEAD `ae02c12`，`1055 passed` 全绿。

## 这条分支在做什么

把 ntu-shower 整仓往行业主流重做：SQLAlchemy 2.0 + Alembic、标准认证（Bearer 凭据 + 签名会话）、容器化编排（Caddy + compose）、停机窗口零兼容切换。

## 已完成

| 层 | 内容 | 验证 |
|---|---|---|
| 模型 | 22 张表 ORM（`server/app/models/`） | `alembic check` 无差异 |
| 持久化 | 8 个域（`server/app/persistence/`） | 各域测试 |
| 认证 | `principals` 单主体表、Bearer 凭据、会话 Cookie、设备令牌 | `test_auth_endpoints.py` |
| access 域 | 已完整切到新层（依赖注入） | 全套测试 |
| 转换工具 | `server/tools/convert_v1_to_v2.py` 21 张表 + `--token-output` 给现有设备补凭据 | 生产副本 48 万行逐表 ok，6 台设备各一枚摘要 |
| 编排 | `deploy/stack/`（compose/Caddyfile/三个脚本） | `docker compose config` + `caddy fmt` |
| 调度 | `server/app/core/background.py` 统一三个间隔任务 | 测试 |

## 剩余工作（按顺序）

### 五域同批切换（唯一阻塞）

`devices + gateway + voice + sessions + extend_hours` 必须一次切完——已实测：devices 服务加 session 参数后，调用链横跨这五个域，切一个编译就过不了。

方法（已探明）：
- `devices/service.py` 每个查库函数加 `session: Session` 首参，调 `app.persistence.devices`
- 路由用 `Depends(get_db)`；网关 WS 用 `ws.app.state.database.new_session()` 短会话（本轮已写好 `_call` helper 后因未完成而撤销，可从 git reflog 找回或重写）
- 后台任务由 `core/background` 的循环每轮开短会话注入
- 时间/枚举在 service 层转换（RFC 3339 ↔ datetime、str ↔ LinkEvent/DeviceLogKind）
- 删 `modules/devices/repository.py`、`init_schema()` 里对应 `init_tables()`、测试里旧 `device*` 表裸 SQL 改新表

### 其余域切换

`doors + stats + tasks + config + ota + audit`。config/ota/audit 读取方少，相对独立。

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
