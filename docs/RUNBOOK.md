# RUNBOOK.md — 运维手册

本册只写**已实际执行验证过**的步骤。每步标注验证方式；未验证的步骤不得写入。

## 1. 拓扑

| 项 | 值 |
|---|---|
| 入口 | `https://ntu-shower.iseekdream.com`（nginx 443 → 127.0.0.1:8001） |
| 服务器 | `root@iseekdream.com`（SSH 密钥 `~/.ssh/iseekdream-ubuntu.pem`） |
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
tar czf server_deploy.tar.gz -C server \
    --exclude=.venv --exclude=data --exclude=ota_releases \
    --exclude=__pycache__ --exclude=.pytest_cache --exclude=.mypy_cache \
    --exclude=.ruff_cache --exclude=.coverage --exclude='*.pyc' .
scp server_deploy.tar.gz iseekdream.com:/tmp/

# ③ 服务器：备份现状（回滚点）
ssh iseekdream.com 'cd /opt/ntu-shower/server && tar czf /tmp/ntu-shower-backup-$(date +%s).tar.gz \
    app tests pyproject.toml requirements.txt requirements-dev.txt web'

# ④ 服务器：预检——用生产解释器验证新代码能导入
#    这一步不可省：本地解释器版本可能与生产不同，只有在生产解释器上导入才作数
ssh iseekdream.com 'rm -rf /tmp/deploy_check && mkdir -p /tmp/deploy_check \
    && tar xzf /tmp/server_deploy.tar.gz -C /tmp/deploy_check \
    && cd /tmp/deploy_check \
    && PYTHONPATH=/tmp/deploy_check /opt/ntu-shower/server/.venv/bin/python \
       -c "from app.main import create_app; create_app(); print(\"IMPORT OK\")"'

# ⑤ 服务器：替换并重启
ssh iseekdream.com 'systemctl stop ntu-shower \
    && cd /opt/ntu-shower/server && rm -rf app tests \
    && tar xzf /tmp/server_deploy.tar.gz -C /opt/ntu-shower/server \
    && systemctl start ntu-shower && sleep 3 && systemctl is-active ntu-shower'

# ⑥ 验证三项
ssh iseekdream.com 'curl -s http://127.0.0.1:8001/healthz'          # 版本正确
ssh iseekdream.com 'journalctl -u ntu-shower -n 20 --no-pager'      # 无异常栈
# 设备是否重连：看日志出现 "WebSocket /gateway/device [accepted]"
```

**回滚**（第 ⑤ 步之后任一项验证失败即执行）：

```bash
ssh iseekdream.com 'systemctl stop ntu-shower \
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
ssh iseekdream.com 'systemctl start ntu-shower-backup.service && journalctl -u ntu-shower-backup -n 5'
```

### keystore（不做自动滚动）

keystore 是**全系统唯一长期绑定物件**：丢失意味着所有在网设备都必须物理接触、卸载重装。
它体积小且不变，因此采用人工副本 + 指纹校验，而不是自动滚动（滚动反而增加误删风险）。

```bash
# 备份到服务器（异地于开发机）
scp config/keystore/ntu-shower.keystore iseekdream.com:/opt/ntu-shower-backup/
ssh iseekdream.com 'chmod 600 /opt/ntu-shower-backup/ntu-shower.keystore'

# 校验两侧一致（指纹必须相同）
Get-FileHash config/keystore/ntu-shower.keystore -Algorithm SHA256    # 本地
ssh iseekdream.com 'sha256sum /opt/ntu-shower-backup/ntu-shower.keystore'
```

### 恢复演练（建议每季度一次）

1. 从备份取 DB → 停服 → 覆盖 `data/ntu-shower.db` → 启服 → check `/healthz` 与设备重连；
2. 从备份取 keystore → 本地 `gradlew assembleRelease` 能签名成功，即证明副本可用。

## 4. 故障处置

### OTA 反复下载同一个版本（已发生过一次）

**症状**：日志每数秒一轮 `WebSocket accepted → GET /ota/manifest → GET /ota/download/<版本>`，
设备被反复重装。

**根因**：`force` 发布 + `ota_release_install` 表无该设备的安装确认 →
每次握手都判定"未安装"→ 永远推送。

```bash
# 诊断：发布记录与安装确认
ssh iseekdream.com '/opt/ntu-shower/server/.venv/bin/python - <<PY
import sqlite3
con = sqlite3.connect("/opt/ntu-shower/server/data/ntu-shower.db")
print("release:", con.execute("SELECT version, version_code, force, target FROM ota_release").fetchall())
print("install:", con.execute("SELECT * FROM ota_release_install").fetchall())
PY'

# 止损：下架该发布（最直接）
curl -X DELETE -H "X-Admin-Token: $TOKEN" http://127.0.0.1:8001/admin/releases/<版本>
```

**预防**：`mark_installed_upto` 已在设备 hello 时调用（且早于 `latest_for`），
并有回归测试 `test_force_release_not_repushed_when_device_already_on_that_version` 锁定。

### 服务起不来

```bash
ssh iseekdream.com 'systemctl is-active ntu-shower; journalctl -u ntu-shower -n 40 --no-pager'
```

- 日志出现 `AdminTokenNotConfiguredError` → 服务器 `.env` 的 `ADMIN_TOKEN` 是占位值，
  设置真实令牌后重启（这是有意的启动自检，不是故障）；
- 日志出现 `PydanticUserError` / `ImportError` → 部署了与本机解释器不兼容的代码，
  按 §2 回滚，并补齐第 ④ 步预检。

### 设备离线

```bash
ssh iseekdream.com 'TOKEN=$(grep ADMIN_TOKEN /opt/ntu-shower/server/.env | cut -d= -f2); \
  curl -s -H "X-Admin-Token: $TOKEN" http://127.0.0.1:8001/admin/devices'
```

注意 `online` 字段是**最后一次上报值**，与内存中的活动连接可能不一致；
以日志中是否有该设备的 `WebSocket /gateway/device [accepted]` 为准。

### ADB 桥

```bash
# 管理面触发（设备侧会 root 开启 adbd）
curl -X POST -H "X-Admin-Token: $TOKEN" http://127.0.0.1:8001/admin/devices/<id>/adb/open
# 本地隧道 + adb
ssh -N -L 15655:127.0.0.1:15655 root@iseekdream.com &
adb connect 127.0.0.1:15655 && adb devices
# 用毕关闭：/adb/close（空闲 300s 也会被应用层回收）
```

## 5. 日常任务

| 任务 | 命令 |
|---|---|
| 服务状态 | `systemctl status ntu-shower` |
| 实时日志 | `journalctl -u ntu-shower -f` |
| 备份列表 | `ls -lh /opt/ntu-shower-backup/` |
| 发布新 APK | 管理面 `POST /admin/releases`（表单：version / version_code / mandatory / force / target / file） |
| 重启设备 Agent | 命令 `app.restart`（经 `/admin/devices/<id>/exec` 通道之外的管理面命令） |
| 抓设备画面 | `POST /admin/devices/<id>/screen`（返回 `data.image_base64`） |
