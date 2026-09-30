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

## 2. 部署

```bash
# ① 本地：门禁必须全绿（与 CI 同一组命令，见 README「质量门」）
cd server && ruff check . && ruff format --check . && mypy app --strict && pytest -q --cov=app

# ② 本地：打包（务必排除运行时数据与本地配置）
#
# `--exclude=.env` 是必须的，不是可选的清理项：生产的 `.env` 是 systemd 的
# EnvironmentFile，装着 OPS_TOKEN 与数据库路径。本地那份是 `.env.example` 抄来的占位值
# （`OPS_TOKEN=change-me`），一旦随包上去就会覆盖生产配置，而服务启动自检会因此
# 拒绝启动——2026-10-01 实际发生过一次，表现是 systemd 反复重启、healthz 无响应。
# 排除只匹配 `.env` 这个整名，`.env.example` 照常随包发布。
tar czf server_deploy.tar.gz -C server \
    --exclude=.venv --exclude=data --exclude=ota_releases --exclude=.env \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude=.mypy_cache \
    --exclude=.ruff_cache --exclude=.coverage --exclude='*.pyc' .
scp server_deploy.tar.gz <deploy-host>:/tmp/

# ③ 服务器：备份现状（回滚点）
#
# 含 `.env`：它是 systemd 的 EnvironmentFile，装着 OPS_TOKEN 与数据库路径，而它不在仓库里
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
- `deploy/` 是配置唯一事实来源，不要在服务器上直接改 nginx/systemd 配置。

## 3. 备份与恢复

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

## 4. 故障处置

### OTA 反复下载同一个版本（已发生过一次）

**症状**：日志每数秒一轮 `WebSocket accepted → GET /ota/manifest → GET /ota/download/<版本>`，
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
curl -X DELETE -H "X-Ops-Token: $TOKEN" http://127.0.0.1:8001/ops/releases/<版本>
```

**预防**：`mark_installed_upto` 已在设备 hello 时调用（且早于 `latest_for`），
并有回归测试 `test_force_release_not_repushed_when_device_already_on_that_version` 锁定。

### 服务起不来

```bash
ssh <deploy-host> 'systemctl is-active ntu-shower; journalctl -u ntu-shower -n 40 --no-pager'
```

- 日志出现 `OpsTokenNotConfiguredError` → 服务器 `.env` 的 `OPS_TOKEN` 是占位值，
  设置真实令牌后重启（这是有意的启动自检，不是故障）；
- 日志出现 `PydanticUserError` / `ImportError` → 部署了与本机解释器版本不匹配的代码，
  按 §2 回滚，并补齐第 ④ 步预检。

### 设备能力集（capabilities）

设备在 `link.hello` 中声明自己支持的 action 集合（`transport/SupportedActions.ALL`），
服务端在下发命令前比对：**声明的范围内**才发，避免把设备不认识的命令发过去。

- 空数组表示**未声明**（升级前的旧版本设备），此时不做拦截——否则升级服务端会把在网设备锁死；
- 设备升级后 capabilities 会自动带上真实集合，拦截随之生效；
- 若某命令被拒且 `reason_code=device.unsupported_action`，先看设备声明里有没有它：

```bash
ssh <deploy-host> 'TOKEN=$(grep OPS_TOKEN /opt/ntu-shower/server/.env | cut -d= -f2);
  curl -s -H "X-Ops-Token: $TOKEN" http://127.0.0.1:8001/ops/devices | python3 -m json.tool'
```

### 设备离线

```bash
ssh <deploy-host> 'TOKEN=$(grep OPS_TOKEN /opt/ntu-shower/server/.env | cut -d= -f2); \
  curl -s -H "X-Ops-Token: $TOKEN" http://127.0.0.1:8001/ops/devices'
```

注意 `online` 字段是**最后一次上报值**，与内存中的活动连接可能不一致；
以日志中是否有该设备的 `WebSocket /gateway/device [accepted]` 为准。

### 调试通道（ADB 桥）

**按需开、到点自动关**：手动触发才开，空闲或到达寿命上限由服务端回收。

```bash
# 打开（设备侧会 root 开启 adbd）
curl -X POST -H "X-Ops-Token: $TOKEN" http://127.0.0.1:8001/ops/devices/<id>/bridge/open
# 本地隧道 + adb
ssh -N -L 15655:127.0.0.1:15655 deploy@<deploy-host> &
adb connect 127.0.0.1:15655 && adb devices

# 查看状态：含 expires_in_s（距寿命到点还有多久）
curl -s -H "X-Ops-Token: $TOKEN" http://127.0.0.1:8001/ops/devices/<id>/bridge/status
# 用毕关闭
curl -X POST -H "X-Ops-Token: $TOKEN" http://127.0.0.1:8001/ops/devices/<id>/bridge/close
```

两个上限都可配（`bridge_idle_timeout_s` 默认 300 秒、`bridge_max_lifetime_s` 默认 3600 秒）。
**空闲超时管不到"一直有流量"的情况**，所以要寿命上限兜着——调试通道不该无限期开着。

## 5. 日常任务

### 发布新 APK

版本号在**三处**，发布前必须一起改——`test_version_consistency.py` 会在门禁里拦住不一致，
但知道要改哪三处能省一轮往返：

| 位置 | 字段 |
|---|---|
| `android/app/build.gradle.kts` | `versionCode`（递增整数）与 `versionName` |
| `server/app/core/version.py` | `APP_VERSION` |
| `server/pyproject.toml` | `[project] version` |

改完跑一遍门禁（版本一致性是其中一项），再构建、再发布：

```bash
cd android && ./gradlew detekt :app:testDebugUnitTest :app:jacocoUnitTestCoverageVerification assembleRelease
curl -X POST https://<public-domain>/ops/releases \
  -H "X-Ops-Token: $OPS_TOKEN" \
  -F "version=0.3.35" -F "version_code=42" -F "target=all" \
  -F "file=@app/build/outputs/apk/release/app-release.apk"
```

响应里的 `notified_devices` 非空即表示在线设备已收到通知。设备会自行下载安装并重连，
无需人工介入——实测约 10 秒完成。

### 其他

| 任务 | 命令 |
|---|---|
| 服务状态 | `systemctl status ntu-shower` |
| 实时日志 | `journalctl -u ntu-shower -f` |
| 备份列表 | `ls -lh /opt/ntu-shower-backup/` |
| 抓设备画面 | `POST /ops/devices/<id>/capture`（返回 `data.image_base64`） |
| 远程执行 shell | `POST /ops/devices/<id>/exec`，body `{"command": "..."}` |
| 重启设备端程序 | `POST /ops/devices/<id>/restart` |
| 卸载设备端程序 | `POST /ops/devices/<id>/uninstall` |
