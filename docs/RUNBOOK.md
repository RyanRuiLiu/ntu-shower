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
scp server_deploy.tar.gz iseekdream.com:/tmp/

# ②b 协议 schema 单独同步：它在 server/ 之外（服务器上是 /opt/ntu-shower/protocol），
#     而服务端与设备端都以它为唯一事实源。漏同步会让两端对字段的理解分叉——服务端
#     按新 schema 收、设备按旧 schema 发，而两边都不会报错。
scp protocol/schema/*.json iseekdream.com:/opt/ntu-shower/protocol/schema/

# ③ 本地：把部署脚本送到服务器
#
# 它不随包发布（包里只有 server/ 的内容），每次部署时送过去执行。
scp deploy/scripts/deploy.sh iseekdream.com:/tmp/

# ④ 服务器：部署
#
# 脚本依次做五件事：用**生产解释器**预检 → 停服并备份（保留最近 5 份到
# /opt/ntu-shower/backups）→ 保留 .venv／data／ota_releases／.env、其余全删后再解包
# → requirements.txt 变过才装依赖 → 启动并验证（is-active + /healthz）。
# 任一步失败自动回滚；预检放在停服之前，所以预检不过时服务一直没停过、无需回滚。
#
# **"保留四项、其余全删"是刻意的**：以前是 `rm -rf app tests`，只能清那两个目录，
# `web/`、`tools/` 只解压覆盖——仓库里删掉的文件会永久留在服务器上。实际已攒下两处：
# `web/core/log.js`（已删的孤儿）与 `scripts/`（改名成 `tools/` 前的旧目录）。
# 保留清单与理由写在脚本头部注释里。
ssh iseekdream.com 'sudo NT_TARBALL=/tmp/server_deploy.tar.gz bash /tmp/deploy.sh'

# ⑤ 人工复核
#
# healthz 只回 `{"status":"ok",...,"version":"0.4.23"}`，而版本号冻结在 0.4.23
# （它要与 android 的 versionName 对齐，不随服务端改动走）——**所以它证明不了这次部署**。
# 要确认新代码真的上去了，查这次改动的特征串：见脚本输出与其后 `journalctl` 的日志。
ssh iseekdream.com 'journalctl -u ntu-shower -n 20 --no-pager'      # 无异常栈启动
# 设备是否重连：看日志出现 "WebSocket /gateway/device [accepted]"
```

**回滚**（部署后发现问题的两步）：

```bash
# 先看有哪些回滚点
ssh iseekdream.com 'ls -1t /opt/ntu-shower/backups/server-*.tar.gz | head'
# 回滚（同一个脚本的 --rollback 入口，走与部署相同的"保留四项、其余全删"逻辑）
ssh iseekdream.com 'sudo NT_ROLLBACK=/opt/ntu-shower/backups/server-<时间戳>.tar.gz bash /tmp/deploy.sh --rollback'
```

### 注意事项（均为踩过的坑）

- **必须用生产 Python 预检**：本地 3.13 通过不代表生产 3.10 通过。
  曾发生 `typing.TypedDict` 在 3.10 + pydantic 2.13 下被拒（要求 `typing_extensions.TypedDict`），
  本地全绿而线上启动即崩——`deploy.sh` 的第 1 步就是为此存在的，而且它排在停服之前，
  预检失败时服务一直没停过。
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

### 语音播报前置（voice，启用时一次性）

服务端只在规则保存/试听时调用 Piper；模型、`espeak-ng`、`ffmpeg` 不随代码包发布。
首次启用语音或补齐另一个音色后执行一次（脚本按登记表下载**全部音色**并校验）：

```bash
# requirements.txt 已由 deploy.sh 安装；确认生产解释器能导入 Piper
ssh iseekdream.com '/opt/ntu-shower/server/.venv/bin/python -c "import piper; print(1)"'
# 系统依赖：espeak-ng（Piper 的运行依赖其共享库）与 ffmpeg（MP3 编码，需带 libmp3lame）
ssh iseekdream.com 'command -v espeak-ng || apt-get install -y espeak-ng; ffmpeg -hide_banner -encoders | grep libmp3lame'
# 模型（约 63MB，落在 data/voice/models；按内置的字节数与 SHA-256 校验，可重复执行）
ssh iseekdream.com 'cd /opt/ntu-shower/server && .venv/bin/python -m tools.fetch_voice_model'
# 合成状态（admin 身份）
ssh iseekdream.com 'curl -sS -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/voice/status'
```

`ready=true` 才是四项（模型 / Piper / espeak-ng / ffmpeg）都齐；任一缺失时规则保存与
试听会失败。磁盘占用：模型约 63 MB，合成音频缓存按最近使用淘汰、上限 200 MB
（`data/voice/clips/`），都在 `data/` 下、随部署保留。设备端无需任何配置，音频经
`/voice/clips/{clip_id}.mp3` 拉取。规则保存返回 `voice-synthesis-failed` 时，先查这个
接口与 `journalctl -u ntu-shower`，不要在页面上反复重试。

## 4. 备份与恢复

### 自动备份（数据库）

`ntu-shower-backup.timer` 每日 00:00 执行 `VACUUM INTO` 在线备份，
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

### 恢复演练（**已执行，2026-09-30**）

数据库备份在临时位置实际还原过一次，验证脚本与结果如下（不碰生产）：

```bash
ssh iseekdream.com 'BAK=$(ls -1t /opt/ntu-shower-backup/ntu-shower-*.db | head -1)
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
ssh iseekdream.com '/opt/ntu-shower/server/.venv/bin/python - <<PY
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
ssh iseekdream.com 'systemctl is-active ntu-shower; journalctl -u ntu-shower -n 40 --no-pager'
```

- 日志出现 `PydanticUserError` / `ImportError` → 部署了与本机解释器版本不匹配的代码，
  按 §2 回滚。`deploy.sh` 的第 1 步（生产解释器预检）本该拦住它——若它没拦住，先查那一步
  是否被跳过。

### 设备能力集（capabilities）

规则（设备怎么声明、服务端怎么比对、空数组为什么放行）见 `docs/DECISIONS.md`「协议与契约」。
**排查**：若某命令被拒且 `reason_code=device.unsupported_action`，先看设备声明里有没有它：

```bash
ssh iseekdream.com 'curl -s -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/devices/runtime | python3 -m json.tool'
```

### 设备离线

```bash
ssh iseekdream.com 'curl -s -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/devices/runtime'
```

注意 `online` 字段是**最后一次上报值**，与内存中的活动连接可能不一致；
以日志中是否有该设备的 `WebSocket /gateway/device [accepted]` 为准。

### 语音播报不响

1. **规则是否匹配**：设备、学号两个维度各自具体或「全部」（`*`），具体压过通配
   （先设备、后学号维度）；「全部学号」的规则可能把这个学号放进了排除名单；规则
   停用或没有规则就不会下发。看 `/api/v1/voice/deliveries` 有没有对应记录。
2. **事件是否过期**：`voice_event_max_age_s` 默认 60 秒，离线积压或音频准备耗时过长
   会跳过；投递状态为 `skipped`。
3. **命令是否到达设备**：审计页看 `voice.play` / `voice.prepare` 的 status 与
   reason_code；离线是 `device.offline`，旧 APK 是 `device.unsupported_action`。
4. **音频是否就绪**：`/api/v1/voice/status`；规则保存 503 时先按上一节补 Piper/模型。
5. **音效是否就绪**：模板里 `{fx:名称}` 引用的音效先看「音效库」里还在不在（被引用
   的音效删不掉、改不了名；若库被外部动过，保存规则时会点名缺失）。上传支持常见格式，
   服务端统一转换，时长上限 10 秒；`{pause:毫秒}` 是纯静音，不查库。
6. **设备是否真的在播**：设备 `device.log` 看 `audio.played` / `audio.play-failed` /
   `audio.volume-restored`；音量恢复失败会保留待恢复标记，下次启动或播放前再试。
   规则行的「推送」按钮可不等事件、立即在指定设备上真播一遍，用于现场验证。
7. **和浴室 App 的提示音重叠**：设备播放前会自动检测 `STREAM_MUSIC` 上的浴室提示音，
   等它结束再播，不需要逐条配延迟。看设备日志 `audio.bath-wait` 的 `waited_ms`；
   出现 `audio.bath-wait-timeout` 说明等待超过 `voice_bath_wait_max_ms`（默认 15 秒），
   检查浴室 App 是否卡在循环播放。网页「最近推送」的阶段耗时列（音频准备 / 事件到
   下发 / 下发到播完 / 现场下载还是命中设备缓存）用于区分链路慢还是设备播放慢。

### 调试通道（ADB 桥）

**按需开、到点自动关**：手动触发才开，空闲或到达寿命上限由服务端回收。

```bash
# 打开（设备侧会 root 开启 adbd）
curl -X POST -H "X-Identity: <admin身份标识>" http://127.0.0.1:8001/api/v1/devices/<id>/bridge/open
# 本地隧道 + adb
ssh -N -L 15655:127.0.0.1:15655 root@iseekdream.com &
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

### 升级循环（设备反复断开重连、版本不动）

签名：`/ota/manifest` 请求暴增 + 同一台设备反复"接入→断开"、`ota_release_install`
里同一版本出现多条记录。2026-10-24 全线发生过：发布登记 0.4.40、APK 字节里却是
0.4.39/83，设备 `pm install` Success 而版本不变，永远"有新版"。

处置顺序：**先下架止血**（`DELETE /api/v1/ota/releases/<version>`，设备停手回到当前
版本），再查原因。服务端发布接口现在会解析 APK 内容版本并拒绝不一致的发布——那条
路走通了说明发布本身没问题；设备端同一版本重试 3 次后也会停在
`ota.attempts-exhausted`（device.log，设备页可见），恢复方式是修正发布后重推。

### 其他

| 任务 | 命令 |
|---|---|
| 服务状态 | `systemctl status ntu-shower` |
| 实时日志 | `journalctl -u ntu-shower -f` |
| 备份列表 | `ls -lh /opt/ntu-shower-backup/` |
| 抓设备画面 | `POST /api/v1/devices/<id>/capture`（返回 `data.image_base64`） |
| 远程执行 shell | `POST /api/v1/devices/<id>/exec`，body `{"command": "..."}` |
| 修改设备端时间 | `POST /api/v1/devices/<id>/clock/set`，body `{"datetime": "YYYY-MM-DD HH:MM:SS"}`（设备本地墙钟） |
| 重启本程序 | `POST /api/v1/devices/<id>/app/restart`（不重启整机，也不动浴室 APP） |
| 重启浴室 App | `POST /api/v1/devices/<id>/bath/restart`（先强停再冷启动，会中断进行中的会话） |
| 重启整机 | `POST /api/v1/devices/<id>/reboot` |
