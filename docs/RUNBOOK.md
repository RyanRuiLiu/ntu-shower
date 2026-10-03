# RUNBOOK.md — 运维手册

本册只写**已实际执行验证过**的步骤。每步标注验证方式；未验证的步骤不得写入。

## 1. 拓扑

| 项 | 值 |
|---|---|
| 入口 | `https://<public-domain>`（nginx 443 → 127.0.0.1:8001） |
| 服务器 | `deploy@<deploy-host>`（SSH 密钥 `~/.ssh/<ssh-private-key>`） |
| 应用目录 | `/opt/ntu-shower/server`（`.venv`、`data/`、`.env` 均在此，均不入库） |
| 备份目录 | `/opt/ntu-shower-backup` |
| 运行时 | Python **3.10.12**（`.venv`）——与 CI 测试版本一致，见 §2 注意事项 |
| 服务单元 | `ntu-shower.service`（uvicorn，Type=simple，Restart=always） |
| 备份单元 | `ntu-shower-backup.timer`（每日 00:00，`Persistent=true`） |

## 2. 痕迹取回（出问题时按设备 + 时间段还原现场）

三层痕迹，以 `command_id` 与 `trace_id` 贯通（标准见 docs/DECISIONS.md「可观测性」）：

```bash
# ① HTTP/网关审计（谁、何时、调了什么、结果）：本机直连后端。
#    管理面凭 admin 身份（X-Identity 头），身份在网页端"身份"页签发
curl -s -H "X-Identity: <admin身份标识>"   "http://127.0.0.1:8001/api/v1/audit/calls?device_id=<设备>&limit=100"

# ② 命令级审计（trace_id 与 ① 互通）
curl -s -H "X-Identity: <admin身份标识>" "http://127.0.0.1:8001/api/v1/audit/commands?device_id=<设备>"

# ③ 设备端命令痕迹（与 ② 同一 command_id 呼应）
#    经 ops 页设备面板的命令执行，或 bridge shell：
tail -n 50 /sdcard/ntu-shower/audit.log      # JSONL：受理/重复/结果/网关生命周期
tail -n 50 /sdcard/ntu-shower/automation.log # 自动化性能采样
tail -n 50 /sdcard/ntu-shower/crash.log      # 崩溃现场

# ④ 应用 stdout（journald，JSON 事件流，含 trace_id 可与 ① 对上）
journalctl -u ntu-shower --since "-1h" | grep '"trace_id":"<trace-id>"'
```

保留期：`api_call` 90 天（`audit_retention_days`）每日自动清理；OTA 发布保留
最近 5 个（`ota_release_keep`）。设备端各落盘文件全部有界，无需人工清理。

## 3. 部署

```bash
# ① 本地：门禁必须全绿（命令见 README「质量门」，与 CI 逐条对应，此处不复述）

# ② 本地：打包（务必排除运行时数据与本地配置）
#
# `--exclude=.env` 是必须的，不是可选的清理项：生产的 `.env` 是 systemd 的
# EnvironmentFile，装着数据库路径等生产配置。本地那份若混进包里会覆盖生产配置
# （2026-10-01 实际发生过一次，表现是 systemd 反复重启、healthz 无响应）。
# 排除只匹配 `.env` 这个整名，`.env.example` 照常随包发布。
tar czf server_deploy.tar.gz -C server \
    --exclude=.venv --exclude=data --exclude=ota_releases --exclude=.env \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude=.mypy_cache \
    --exclude=.ruff_cache --exclude=.coverage --exclude='*.pyc' .
scp server_deploy.tar.gz <deploy-host>:/tmp/

# ②b 协议 schema 单独同步：它在 server/ 之外（服务器上是 /opt/ntu-shower/protocol），
#     而服务端与设备端都以它为唯一事实源。漏同步会让两端对字段的理解分叉——服务端
#     按新 schema 收、设备按旧 schema 发，而两边都不会报错。
scp protocol/schema/*.json <deploy-host>:/opt/ntu-shower/protocol/schema/

# ③ 服务器：备份现状（回滚点）
#
# 含 `.env`：它是 systemd 的 EnvironmentFile，装着数据库路径等生产配置，而它不在仓库里
# （gitignore），丢了就只能靠记忆重建。备份落在服务器本地 /tmp，不往外传。
ssh <deploy-host> 'cd /opt/ntu-shower/server && tar czf /tmp/ntu-shower-backup-$(date +%s).tar.gz \
    app tests pyproject.toml requirements.txt requirements-dev.txt web .env'

# ④ 服务器：预检——用生产解释器验证新代码能导入
#    这一步不可省：本地解释器版本可能与生产不同，只有在生产解释器上导入才作数
ssh <deploy-host> 'rm -rf /tmp/deploy_check && mkdir -p /tmp/deploy_check \
    && tar xzf /tmp/server_deploy.tar.gz -C /tmp/deploy_check \
    && cd /tmp/deploy_check \
    && PYTHONPATH=/tmp/deploy_check /opt/ntu-shower/server/.venv/bin/python \
       -c "from app.main import create_app; create_app(); print(\"IMPORT OK\")"'

# ⑤ 服务器：替换并重启
ssh <deploy-host> 'systemctl stop ntu-shower \
    && cd /opt/ntu-shower/server && rm -rf app tests \
    && tar xzf /tmp/server_deploy.tar.gz -C /opt/ntu-shower/server \
    && systemctl start ntu-shower && sleep 3 && systemctl is-active ntu-shower'

# ⑥ 验证三项
ssh <deploy-host> 'curl -s http://127.0.0.1:8001/healthz'          # 版本正确
ssh <deploy-host> 'journalctl -u ntu-shower -n 20 --no-pager'      # 无异常栈
# 设备是否重连：看日志出现 "WebSocket /gateway/device [accepted]"
```

**回滚**（第 ⑤ 步之后任一项验证失败即执行）：

```bash
ssh <deploy-host> 'systemctl stop ntu-shower \
    && cd /opt/ntu-shower/server && rm -rf app tests \
    && tar xzf /tmp/ntu-shower-backup-<时间戳>.tar.gz -C /opt/ntu-shower/server \
    && systemctl start ntu-shower && sleep 3 && systemctl is-active ntu-shower'
```

### 注意事项（均为踩过的坑）

- **必须用生产 Python 预检**：本地 3.13 通过不代表生产 3.10 通过。
  曾发生 `typing.TypedDict` 在 3.10 + pydantic 2.13 下被拒（要求 `typing_extensions.TypedDict`），
  本地全绿而线上启动即崩——第 ④ 步就是为此存在的。
- 打包排除 `.env`：服务器的 `.env` 是唯一副本，被覆盖将导致管理面令牌丢失。
- 改 `requirements.txt` 后需在服务器执行 `.venv/bin/pip install -r requirements.txt`；
  仅改业务代码则不需要。
- 配置（nginx / systemd / backup）的唯一事实来源是 `deploy/`，**不要在服务器上直接改**；
  改法与同步命令见 `deploy/README.md`。

### 表结构变过的那一次：把历史数据带进新结构

**只做一次**，且**已在生产备份副本上演练过**（2026-10-04：36168 帧 → 会话 5625 / 段 9676，
越界检查全 0，**帧数一帧不少**）。做完之后只走上面的常规流程，历史数据不再动。

顺序见 `deploy/README.md` 的「一次性」一节。三条要点：

- **原始层只加列**：`ALTER TABLE door_snapshot ADD COLUMN device_clock TEXT`——老帧就该留空，
  它们确实没有这一列，那不是"缺数据"；
- **派生层连表结构一起重建**：`tools.rebuild_derived` 会 `DROP` 那四张表再建、再由原始层
  重算。只 `ALTER` 原始层**不够**——`CREATE TABLE IF NOT EXISTS` 不会去改一张已存在的表，
  旧结构会让重算在第一帧就撞上"没有这一列"；
- **核对**：`tools.audit_timeline --db data/ntu-shower.db`，**帧数必须与合并前一致**。

## 4. 备份与恢复

### 自动备份（数据库）

`ntu-shower-backup.timer` 每日 00:00 执行 `VACUUM INTO` 在线快照，
滚动保留 30 份，落在 `/opt/ntu-shower-backup/ntu-shower-<时间戳>.db`。

手动触发与查看：

```bash
ssh <deploy-host> 'systemctl start ntu-shower-backup.service && journalctl -u ntu-shower-backup -n 5'
```

### keystore（不做自动滚动）

keystore 是**全系统唯一长期绑定物件**：丢失意味着所有在网设备都必须物理接触、卸载重装。
它体积小且不变，因此采用人工副本 + 指纹校验，而不是自动滚动（滚动反而增加误删风险）。

```bash
# 备份到服务器（异地于开发机）
scp config/keystore/ntu-shower.keystore <deploy-host>:/opt/ntu-shower-backup/
ssh <deploy-host> 'chmod 600 /opt/ntu-shower-backup/ntu-shower.keystore'

# 校验两侧一致（指纹必须相同）
Get-FileHash config/keystore/ntu-shower.keystore -Algorithm SHA256    # 本地
ssh <deploy-host> 'sha256sum /opt/ntu-shower-backup/ntu-shower.keystore'
```

### 恢复演练（**已执行，2026-09-30**）

数据库备份在临时位置实际还原过一次，验证脚本与结果如下（不碰生产）：

```bash
ssh <deploy-host> 'BAK=$(ls -1t /opt/ntu-shower-backup/ntu-shower-*.db | head -1)
  rm -rf /tmp/restore_test && mkdir -p /tmp/restore_test
  cp "$BAK" /tmp/restore_test/restored.db
  /opt/ntu-shower/server/.venv/bin/python - <<PY
import sqlite3
con = sqlite3.connect("/tmp/restore_test/restored.db")
print("integrity:", con.execute("PRAGMA integrity_check").fetchone()[0])
for t, in con.execute("SELECT name FROM sqlite_master WHERE type=? ORDER BY name", ("table",)).fetchall():
    print(f"  {t:24} {con.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]} 行")
PY
  rm -rf /tmp/restore_test'
```

**验证记录**：2026-09-30 执行，`integrity_check` 返回 `ok`，全部表可读、
行数与备份时一致。**该步骤此后按季度重复**；任一次失败都说明备份链路已坏，
须在下一轮之前修好。

keystore 的可用性验证：从备份副本执行 `gradlew assembleRelease` 能签名成功
即证明副本可用（签名不匹配的 APK 无法覆盖安装在网设备）。

## 5. 故障处置

### OTA 反复下载同一个版本（已发生过一次）

**症状**：日志每数秒一轮 `WebSocket accepted → GET /ota/manifest → GET /api/v1/ota/releases/<版本>/download`，
设备被反复重装。

**根因**：`force` 发布 + `ota_release_install` 表无该设备的安装确认 →
每次握手都判定"未安装"→ 永远推送。

```bash
# 诊断：发布记录与安装确认
ssh <deploy-host> '/opt/ntu-shower/server/.venv/bin/python - <<PY
import sqlite3
con = sqlite3.connect("/opt/ntu-shower/server/data/ntu-shower.db")
print("release:", con.execute("SELECT version, version_code, force, target FROM ota_release").fetchall())
print("install:", con.execute("SELECT * FROM ota_release_install").fetchall())
PY'

# 止损：下架该发布（最直接）
curl -X DELETE -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/ota/releases/<版本>
```

**预防**：`mark_installed_upto` 已在设备 hello 时调用（且早于 `latest_for`），
并有回归测试 `test_force_release_not_repushed_after_install_confirmation` 锁定。

### 服务起不来

```bash
ssh <deploy-host> 'systemctl is-active ntu-shower; journalctl -u ntu-shower -n 40 --no-pager'
```

- 日志出现 `PydanticUserError` / `ImportError` → 部署了与本机解释器版本不匹配的代码，
  按 §2 回滚，并补齐第 ④ 步预检。

### 设备能力集（capabilities）

规则（设备怎么声明、服务端怎么比对、空数组为什么放行）见 `docs/DECISIONS.md`「协议与契约」。
**排查**：若某命令被拒且 `reason_code=device.unsupported_action`，先看设备声明里有没有它：

```bash
ssh <deploy-host> 'curl -s -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/devices/runtime | python3 -m json.tool'
```

### 设备离线

```bash
ssh <deploy-host> 'curl -s -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/devices/runtime'
```

注意 `online` 字段是**最后一次上报值**，与内存中的活动连接可能不一致；
以日志中是否有该设备的 `WebSocket /gateway/device [accepted]` 为准。

### 调试通道（ADB 桥）

**按需开、到点自动关**：手动触发才开，空闲或到达寿命上限由服务端回收。

```bash
# 打开（设备侧会 root 开启 adbd）
curl -X POST -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/devices/<id>/bridge/open
# 本地隧道 + adb
ssh -N -L 15655:127.0.0.1:15655 deploy@<deploy-host> &
adb connect 127.0.0.1:15655 && adb devices

# 查看状态：含 expires_in_s（距寿命到点还有多久）
curl -s -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/devices/<id>/bridge
# 用毕关闭
curl -X POST -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/devices/<id>/bridge/close
```

两个上限都可配（`bridge_pair_timeout_s` 默认 30 秒——点开桥后建隧道的最长时间；
`bridge_max_lifetime_s` 默认 3600 秒）。**隧道本身没有空闲超时**，只靠寿命上限兜着——
调试通道不该无限期开着。

## 6. 日常任务

### 发布新 APK

```powershell
.\deploy\publish_apk.ps1 -Identity <admin身份标识>
```

脚本自己从 `android/app/build.gradle.kts` 读版本号、跑门禁、构建、上传（带 `-SkipBuild`
可用已构建的 APK）。**版本号不手填**：手填等于把手里的值复制一次，而 OTA 覆盖安装要求新旧
版本可比，填错的表现是设备不升级或反复升级，偏静默。版本号在哪几处、为什么合并不了，
见 `docs/DECISIONS.md`「OTA 与版本」。

响应里的 `notified_devices` 非空即表示在线设备已收到通知。设备会自行下载安装并重连，
无需人工介入——实测约 10 秒完成。

### 其他

| 任务 | 命令 |
|---|---|
| 服务状态 | `systemctl status ntu-shower` |
| 实时日志 | `journalctl -u ntu-shower -f` |
| 备份列表 | `ls -lh /opt/ntu-shower-backup/` |
| 抓设备画面 | `POST /api/v1/devices/<id>/capture`（返回 `data.image_base64`） |
| 远程执行 shell | `POST /api/v1/devices/<id>/exec`，body `{"command": "..."}` |
| 重启设备端程序 | `POST /api/v1/devices/<id>/restart` |
| 卸载设备端程序 | `POST /api/v1/devices/<id>/uninstall` |
