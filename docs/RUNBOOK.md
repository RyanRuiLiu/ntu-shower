# 运维手册

本册只写**已实际执行验证过**的步骤。每步标注验证方式；未验证的步骤不得写入。

## 拓扑

| 项 | 值 |
|---|---|
| 入口 | `https://ntu-shower.iseekdream.com`（Caddy 443 → 127.0.0.1:8110） |
| 服务器 | `root@iseekdream.com`（SSH 密钥 `~/.ssh/iseekdream-ubuntu.pem`） |
| 运行形态 | Docker Compose（2026-11-06 起）：镜像自 ACR 拉取，`docker compose -f /opt/ntu-shower/compose.yaml up -d` |
| 编排文件 | `/opt/ntu-shower/compose.yaml`（由仓库根 `compose.yaml` 同步；数据路径在 compose 的 `environment` 钉死） |
| 镜像 | `crpi-5rhaq5x8z5ec9tig.cn-hangzhou.personal.cr.aliyuncs.com/ryanruiliu/ntu-shower:<tag>`（`docker.yml` 双推 GHCR/ACR；tag 双轨 `sha-<短哈希>` 与版本号） |
| 配置 | `/etc/ntu-shower/ntu-shower.env`（容器经 `env_file` 注入，不入库） |
| 数据目录 | `/var/lib/ntu-shower`（`ntu-shower.db`、`voice/`、`ota_releases/`；bind mount 进容器同路径，属主 `ntu-shower`=UID 997） |
| 备份目录 | `/var/backups/ntu-shower`（脚本 `/opt/ntu-shower/scripts/ntu-shower-backup.sh`）|
| 旧应用目录 | `/opt/ntu-shower/server`（venv 形态，2026-11-06 起退役冻结，仅应急回滚用，见「应急回滚」） |
| 备份单元 | `ntu-shower-backup.timer`（每日 00:00，`Persistent=true`；宿主机执行，读 `/var/lib`，与容器无关） |

## 痕迹取回（出问题时按设备 + 时间段还原现场）

三层痕迹，以 `command_id` 与 `trace_id` 贯通（标准见 docs/adr/「可观测性」）：

```bash
# ① HTTP/网关审计（谁、何时、调了什么、结果）：本机直连后端。
#    管理面凭 admin 身份（X-Identity 头），身份在网页端"身份"页签发
curl -s -H "X-Identity: <admin身份标识>"   "http://127.0.0.1:8110/api/v1/audit/calls?device_id=<设备>&limit=100"

# ② 命令级审计（trace_id 与 ① 互通）
curl -s -H "X-Identity: <admin身份标识>" "http://127.0.0.1:8110/api/v1/audit/commands?device_id=<设备>"

# ③ 设备端命令痕迹（与 ② 同一 command_id 呼应）
#    经 ops 页设备面板的命令执行，或 bridge shell：
tail -n 50 /sdcard/ntu-shower/audit.log      # JSONL：受理/重复/结果/网关生命周期
tail -n 50 /sdcard/ntu-shower/automation.log # 自动化性能采样
tail -n 50 /sdcard/ntu-shower/crash.log      # 崩溃现场

# ④ 应用 stdout（容器 json-file，JSON 事件流，含 trace_id 可与 ① 对上）
ssh iseekdream.com 'docker logs ntu-shower --since 1h' | grep '"trace_id":"<trace-id>"'
```

保留期：`api_call` 90 天（`audit_retention_days`）每日自动清理；OTA 发布保留
最近 5 个（`ota_release_keep`）。设备端各落盘文件全部有界，无需人工清理。

## 部署

2026-11-06 起服务以 Docker Compose 运行：镜像由 `docker.yml` 在 CI 构建并双推 GHCR/ACR，
服务器只做拉取与拉起。tag 双轨：`sha-<短哈希>`（每个 main 提交各一个）与版本号（每个 `v*` 标签）。
compose 的 `environment` 已把数据路径钉死到挂载点（`/var/lib/ntu-shower/...`），根文件系统只读。

```bash
# ① 本地：门禁必须全绿（命令见 CONTRIBUTING「质量门」，与 CI 逐条对应，此处不复述）；
#    合并进 main 后 CI 自动构建镜像，docker/build job 日志里查本次 tag（形如 sha-1ed250f）。

# ② 服务器：预拉镜像——在停旧服务之前做，拉取期间旧服务照常运行，零停机窗口被压到切换那一步
ssh iseekdream.com 'docker pull crpi-5rhaq5x8z5ec9tig.cn-hangzhou.personal.cr.aliyuncs.com/ryanruiliu/ntu-shower:<tag>'

# ③ 本地：同步编排文件（deploy 资产唯一事实源；协议 schema 随镜像走，不再单独同步）
scp compose.yaml iseekdream.com:/opt/ntu-shower/compose.yaml

# ④ 服务器：切换。停旧 systemd 服务与拉起容器之间是唯一的停机窗口（秒级）；
#    两者都绑 127.0.0.1:8110，不能并存。NTU_SHOWER_TAG 必填（compose 拒绝空值）。
ssh iseekdream.com 'systemctl stop ntu-shower && cd /opt/ntu-shower && NTU_SHOWER_TAG=<tag> docker compose up -d'

# ⑤ 服务器：验证。healthz 的 version 字段证明目标版本生效（不止在运行）；
#    设备重连看容器日志的 "WebSocket /gateway/device [accepted]"——设备断电时段切机器的，
#    次日 10:55 上电后自行回连，以日志出现 accepted 为准补一次部署后验证。
ssh iseekdream.com 'curl -fsS http://127.0.0.1:8110/healthz; echo; docker ps --filter name=ntu-shower --format "{{.Status}}"'
ssh iseekdream.com 'docker logs ntu-shower 2>&1 | tail -20'

# ⑥ 服务器：退役旧单元（保留单元文件与 venv 作应急回滚入口，不卸载、不删除）
ssh iseekdream.com 'systemctl disable ntu-shower'
```

**回滚**（部署后发现问题时，回滚到上一个镜像 tag——一条命令，秒级）：

```bash
ssh iseekdream.com 'cd /opt/ntu-shower && NTU_SHOWER_TAG=<上一个tag> docker compose up -d'
```

### 应急回滚（venv+systemd 旧形态）

`ntu-shower.service` 单元保留在服务器但已 disable；`/opt/ntu-shower/server` 的旧 venv 与
代码随之冻结在切换前的版本。只在镜像路径整体不可用（ACR 与 GHCR 同时不可达）时走它：
先 `docker compose down` 释放 8110 端口，再 `systemctl enable --now ntu-shower`。
走这条路等于接受旧版本行为，恢复容器后应尽快切回。

### 注意事项

- 运行时由镜像锁定（`python:3.13-slim`），CI 测的就是同一份依赖集；"本地通过不代表生产通过"
  的旧预检教训由镜像同一性天然解决（历史教训：3.10 时代 `typing.TypedDict` 被
  pydantic 拒绝，本地全绿线上即崩——那正是当年的 `deploy.sh` 预检存在的理由）。
- 打包与依赖安装不再存在：依赖变更走 requirements.txt → CI 镜像 → pull。
- 配置（Caddy / compose / backup）的唯一事实来源是仓库 `deploy/stack/`，
  **不要在服务器上直接改**；改法与同步命令见 `deploy/README.md`。
- 数据库备份在宿主机 timer 上执行、读 `/var/lib/ntu-shower`，与容器无关（加密取舍见 ADR-0018）。

### v2 切换后的运行形态（已落地）

这台机器由 nginx 反代 8110（顶层单服务 `compose.yaml`），切换不动 nginx。`deploy/stack/` 的 Caddy 全栈属另一台机器。v2 数据层已是唯一通路：

- 凭据校验在 `accept()` 之后，无效凭据以 RFC 6455 1008 关闭帧拒绝；设备端收到 1008 清凭据后走 claim 自注册换真凭据（决策见 `docs/adr/`）
- `DEVICE_ENROLL_TOKEN` 在 `/etc/ntu-shower/ntu-shower.env`，值与 APK 构建期的 `deviceEnrollToken` 一致；留空即注册通道关闭，装了 APK 的设备会收到 401
- 管理台以账号口令认证（`POST /api/v1/auth/login` 下发签名会话 Cookie）或 Bearer 凭据访问 API；凭据由运维签发、服务端只存哈希，明文不入库不入档
- OTA 只保留稳定回滚点与当前版本；带缺陷的过渡版按 `DELETE /api/v1/ota/releases/{version}` 下架，发布记录与 APK 文件一起删

### 应急回滚到 v1（切换窗口留存的资产）

资产齐备：v1 镜像 tag、v1 库一致性备份（`/var/backups/ntu-shower/cutover-*/`）、切换前的 `.env` 副本（`ntu-shower.env.pre-cutover`）。回滚顺序：

1. `NTU_SHOWER_TAG=<v1-tag> docker compose down`
2. 用 v1 库备份覆盖 `/var/lib/ntu-shower/ntu-shower.db`，清掉 `-wal` / `-shm` 残留
3. 恢复 `ntu-shower.env.pre-cutover`
4. `NTU_SHOWER_TAG=<v1-tag> docker compose up -d`，以 `healthz` 的 version 字段确认目标版本生效

回滚等于接受 v1 行为，恢复 v2 后应尽快切回。

### 表结构变更的落地

改 `server/app/core/db.py` 的 DDL 后，已存在的库不会跟着变，按下列顺序落地
（2026-10-04 在生产备份副本上演练：36168 帧 → 会话 5625 / 段 9676，越界检查全 0，
帧数一帧不少）：

- **原始层只加列**：`ALTER TABLE door_snapshot ADD COLUMN device_clock TEXT`——老帧就该留空，
  它们确实没有这一列，那不是"缺数据"；
- **派生层连表结构一起重建**：`python -m tools.rebuild_derived --db /var/lib/ntu-shower/ntu-shower.db` 会 `DROP` 那四张表再建、再由原始层
  重算。只 `ALTER` 原始层**不够**——`CREATE TABLE IF NOT EXISTS` 不会去改一张已存在的表，
  旧结构会让重算在第一帧就撞上"没有这一列"；
- **核对**：`python -m tools.audit_timeline --db /var/lib/ntu-shower/ntu-shower.db`，**帧数必须与变更前一致**。

以上命令经容器执行（镜像内即生产运行时）：

```bash
ssh iseekdream.com 'docker exec ntu-shower python -m tools.rebuild_derived --db /var/lib/ntu-shower/ntu-shower.db'
ssh iseekdream.com 'docker exec ntu-shower python -m tools.audit_timeline --db /var/lib/ntu-shower/ntu-shower.db'
```

### 语音播报前置（voice，启用时一次性）

服务端不再合成语音：规则只是一串固定音频、数字变量与停顿。上传的音频由服务端用
ffmpeg 统一转成 MP3，因此只需系统有 `ffmpeg` 与 `ffprobe`（需带 libmp3lame）：

```bash
# ffmpeg / ffprobe（MP3 编码）已内置于镜像（Dockerfile apt 层），无需宿主机安装；
# 就绪状态与数字素材齐备性走接口看
ssh iseekdream.com 'curl -sS -H "X-Identity: <admin身份标识>" http://127.0.0.1:8110/api/v1/voice/status'
```

`ffmpeg_available=true` 才能上传素材；`digits` 列出已标记的 0–9 数字素材，含数字变量的
规则要求 0–9 齐全。素材文件在 `data/voice/assets/`，按内容哈希命名，随 `data/` 保留；
历史音效源文件在 `data/voice/effects/`。
规则保存返回 `invalid-voice-rule` 时，先看返回的缺失素材或缺失数字，不要在页面上反复重试。

## 备份与恢复

### 自动备份（数据库）

`ntu-shower-backup.timer` 每日 00:00 执行 `VACUUM INTO` 在线备份（校验完整性后 gzip 压缩），
滚动保留 14 份，落在 `/var/backups/ntu-shower/ntu-shower-<时间戳>.db.gz`。
备份文件含学号、凭证与审计原文，目录权限 700；加密经 ADR-0018 记录为暂不执行（例外见 `CONTRIBUTING.md`）。

手动触发与查看：

```bash
ssh iseekdream.com 'systemctl start ntu-shower-backup.service && journalctl -u ntu-shower-backup -n 5'
```

### keystore（不做自动滚动）

keystore 是**全系统唯一长期绑定物件**：丢失意味着所有在网设备都必须物理接触、卸载重装。
它体积小且不变，因此采用人工副本 + 指纹校验，而不是自动滚动（滚动反而增加误删风险）。

```bash
# 备份到服务器（异地于开发机）
scp config/keystore/ntu-shower.keystore iseekdream.com:/var/backups/ntu-shower/
ssh iseekdream.com 'chmod 600 /var/backups/ntu-shower/ntu-shower.keystore'

# 校验两侧一致（指纹必须相同）
Get-FileHash config/keystore/ntu-shower.keystore -Algorithm SHA256    # 本地
ssh iseekdream.com 'sha256sum /var/backups/ntu-shower/ntu-shower.keystore'
```

keystore 的可用性验证：从备份副本执行 `gradlew assembleRelease` 能签名成功
即证明副本可用（签名不匹配的 APK 无法覆盖安装在网设备）。

### 恢复演练

数据库备份在临时位置实际还原过一次，验证脚本与结果如下（不碰生产）：

```bash
ssh iseekdream.com 'BAK=$(ls -1t /var/backups/ntu-shower/ntu-shower-*.db.gz | head -1)
  rm -rf /tmp/restore_test && mkdir -p /tmp/restore_test
  gunzip -c "$BAK" > /tmp/restore_test/restored.db
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
须在下一轮之前修好。2026-11-06 切换容器形态后，脚本中的 python 段改经容器执行
（`ssh iseekdream.com 'gunzip -c "$BAK" | docker exec -i ntu-shower python ...'` 形态、
数据库路径 `/var/lib/ntu-shower/ntu-shower.db`）；下一次季度演练按新形态执行并更新本节。

## 故障处置

### OTA 反复下载同一个版本

**症状**：日志每数秒一轮 `WebSocket accepted → GET /ota/manifest → GET /api/v1/ota/releases/<版本>/download`，
设备被反复重装。

**根因**：`force` 发布 + `ota_release_install` 表无该设备的安装确认 →
每次握手都判定"未安装"→ 永远推送。

```bash
# 诊断：发布记录与安装确认
ssh iseekdream.com 'docker exec ntu-shower python - <<PY
import sqlite3
con = sqlite3.connect("/var/lib/ntu-shower/ntu-shower.db")
print("release:", con.execute("SELECT version, version_code, force, target FROM ota_release").fetchall())
print("install:", con.execute("SELECT * FROM ota_release_install").fetchall())
PY'

# 止损：下架该发布（最直接）
curl -X DELETE -H "X-Identity: <admin身份标识>" http://127.0.0.1:8110/api/v1/ota/releases/<版本>
```

**预防**：`mark_installed_upto` 已在设备 hello 时调用（且早于 `latest_for`），
并有回归测试 `test_force_release_not_repushed_after_install_confirmation` 锁定。

### 服务起不来

```bash
ssh iseekdream.com 'docker ps -a --filter name=ntu-shower --format "{{.Status}}"; docker logs ntu-shower --tail 40'
```

- 容器反复重启（Restarting）→ 看日志首屏的异常栈；启动即崩的代码问题按部署章节回滚到
  上一镜像 tag。镜像与 CI 测的是同一份依赖集，`PydanticUserError` / `ImportError` 这类
  解释器版本错配在当前形态下应已绝迹——真出现说明镜像构建与 CI 不一致，先查 tag 推错没有。
- 旧形态的 `systemctl is-active ntu-shower` 只在「应急回滚」里才有意义。

### 设备能力集（capabilities）

规则（设备怎么声明、服务端怎么比对、空数组为什么放行）见 `docs/adr/`「协议与契约」。
**排查**：若某命令被拒且 `reason_code=device.unsupported_action`，先看设备声明里有没有它：

```bash
ssh iseekdream.com 'curl -s -H "X-Identity: <admin身份标识>" http://127.0.0.1:8110/api/v1/devices/runtime | python3 -m json.tool'
```

### 设备离线

```bash
ssh iseekdream.com 'curl -s -H "X-Identity: <admin身份标识>" http://127.0.0.1:8110/api/v1/devices/runtime'
```

注意 `online` 字段是**最后一次上报值**，与内存中的活动连接分开判断；
以日志中是否有该设备的 `WebSocket /gateway/device [accepted]` 为准。

### 语音播报不响

1. **规则是否匹配**：设备、学号两个维度各自具体或「全部」（`*`），具体压过通配
   （先设备、后学号维度），同档内设备集合更小者优先、再比学号集合；「全部学号」的规则若把这个学号
   放进了排除名单；规则停用或没有规则就不会下发。看 `/api/v1/voice/deliveries`
   有没有对应记录。
2. **事件是否过期**：`voice_event_max_age_s` 默认 60 秒，离线积压导致过期会跳过；
   规则保存后的预下载失败只让首次播放现场下载；设备日志看 `audio.prepared` /
   `audio.prefetch-failed`，页面「最近推送」的 `downloaded` 表示本次播放是否走了下载。
3. **命令是否到达设备**：审计页看 `voice.play` / `voice.prepare` 的 status 与
   reason_code；离线是 `device.offline`，旧 APK 是 `device.unsupported_action`。
4. **素材是否就绪**：`/api/v1/voice/status` 看 `ffmpeg_available` 与 `digits`；含数字
   变量的规则要求 0–9 齐全。
5. **规则引用是否完整**：被规则引用的素材删不掉；若库被外部动过，事件解析会失败并在
   服务端日志记 `voice.rule-invalid`，页面「最近推送」不会有该事件的成功记录。
6. **设备是否真的在播**：设备 `device.log` 看 `audio.played` / `audio.play-failed` /
   `audio.volume-restored`；音量恢复失败会保留待恢复标记，下次启动或播放前再试。
   规则行的「推送」按钮可不等事件、立即在指定设备上真播一遍，用于现场验证。
   `voice.clip_download_failed` 多出现在规则刚保存、预下载还没完成时——播放自身只有
   60 秒下载预算；等后台预下载完成后重试，或减少单条规则引用的素材。
7. **和浴室 App 的提示音重叠**：设备播放前会自动检测 `STREAM_MUSIC` 上的浴室提示音，
   等它结束再播，不需要逐条配延迟。看设备日志 `audio.bath-wait` 的 `waited_ms`；
   出现 `audio.bath-wait-timeout` 说明等待超过 `voice_bath_wait_max_ms`（默认 15 秒），
   检查浴室 App 是否卡在循环播放。网页「最近推送」的下发/完成时刻、分段与
   `downloaded` 用于区分链路慢、现场下载还是设备播放慢。

### 调试通道（ADB 桥）

**按需开、到点自动关**：手动触发才开，空闲或到达寿命上限由服务端回收。

```bash
# 打开（设备侧会 root 开启 adbd）
curl -X POST -H "X-Identity: <admin身份标识>" http://127.0.0.1:8110/api/v1/devices/<id>/bridge/open
# 本地隧道 + adb
ssh -N -L 15655:127.0.0.1:15655 root@iseekdream.com &
adb connect 127.0.0.1:15655 && adb devices

# 查看状态：含 expires_in_s（距寿命到点还有多久）
curl -s -H "X-Identity: <admin身份标识>" http://127.0.0.1:8110/api/v1/devices/<id>/bridge
# 用毕关闭
curl -X POST -H "X-Identity: <admin身份标识>" http://127.0.0.1:8110/api/v1/devices/<id>/bridge/close
```

两个上限都可配（`bridge_pair_timeout_s` 默认 30 秒——点开桥后建隧道的最长时间；
`bridge_max_lifetime_s` 默认 3600 秒）。**隧道本身没有空闲超时**，只靠寿命上限兜着——
调试通道不该无限期开着。

## 复盘

- 每个 SEV1 与 SEV2 事件必须复盘。
- 每个发布必须复盘：目标是否达成、发现了什么、下个迭代改什么。
- 复盘针对系统与流程；改进项必须有责任人、期限与验证方式。
- 复盘结论写入事件记录或变更记录。

## 发布与部署规则

### 11.1 发布物

构建、发布、运行分离与配置注入按 The Twelve-Factor App；本章规定发布与部署规则；发布准备步骤由 `CONTRIBUTING.md` 所有，生产部署与回滚步骤由 `docs/RUNBOOK.md` 所有。

1. 每次发布必须产生不可变构建物；同一版本不得重新构建。
2. 构建物必须包含版本、提交标识、构建时间、校验和与签名信息。
3. 服务端构建物与设备端安装包分开；协议 Schema 与生成物随发布同步。
4. 发布物不得包含本地配置、运行数据、测试数据、密钥与缓存。
5. 发布物必须有清单；清单包含文件名、大小、校验和与用途。

### 11.2 发布前检查

1. 所有质量门通过。
2. 无 S1 与 S2 缺陷（定义见 §16.7）；S3 有责任人与排期。
3. 版本号已更新；变更记录已更新；破坏性变更已通知消费方。
4. 契约、生成物、服务端与设备端实现一致。
5. 安全扫描与依赖扫描完成；高危与严重问题已处置。
6. 备份可用；回滚点明确；数据变更已在副本上演练。
7. 部署步骤、验证步骤与回滚步骤已复核。
8. 发布记录已建立。

### 11.3 部署

1. 部署通过脚本执行；不得在生产服务器上手工编辑代码与配置。
2. 部署前必须备份当前版本与数据；备份必须有校验和与还原说明。
3. 部署必须保持运行数据、发布包与配置；其余内容以构建物为准。
4. 部署必须幂等；重复执行不得产生重复数据或重复任务。
5. 部署过程必须记录：时间、执行人、版本、目标、结果与异常。
6. 除部署流程、运行手册授权的运维动作与经明确授权的数据修正外，生产环境只读；不得在生产上运行测试套件、重算、演练或临时脚本。

### 11.4 部署后验证

1. 健康检查通过不代表新版本生效；必须检查本次变更的特征行为。部署后验证只做健康检查、关键路径冒烟与观测，不运行测试套件、重算或实验。
2. 必须验证：服务状态、关键接口、身份与授权、设备连接、日志与审计。
3. 必须观察错误率、时延、队列深度、磁盘与备份状态。
4. 设备端 OTA 必须验证版本、安装结果、重连与功能回归。
5. 验证失败必须立即停止后续步骤，按回滚方案执行。
6. 验证结果写入发布记录。

### 11.5 回滚

1. 回滚必须使用发布前构建物与备份；不得重新构建旧版本。
2. 代码回滚使用上一版本构建物；数据回滚使用备份或补偿变更。
3. 已应用的数据变更不得用反向脚本回滚；使用备份还原或追加补偿变更。
4. 回滚必须按主键或版本精确恢复；不得用条件筛选近似恢复。
5. 回滚后必须逐项核对回滚点；不得只看服务状态或行数。
6. 回滚必须记录触发原因、执行人、时间、版本、核对结果与后续动作。

### 11.6 OTA

1. 安装包必须使用正式签名；签名密钥的备份与恢复见 §13.5。
2. 发布声明必须与安装包内容版本一致；不一致的发布必须拒绝。
3. `versionCode` 必须单调递增；设备端按版本与安装确认判断升级。
4. 发布必须支持定向与分阶段；不得一次性推送到全部设备。
5. 设备端必须限制同一版本的安装尝试次数；失败后停止并留下可见记录。
6. 发布必须支持下架；下架必须同时移除注册记录与安装包。
7. 升级后必须验证版本、启动、连接与关键功能；失败必须停止后续推送。

### 11.7 配置与数据变更

1. 配置变更必须通过登记表与部署流程；不得直接修改生产配置。
2. 配置变更必须有回滚值；变更后必须验证生效范围。
3. 数据变更按 §4.10 执行；发布记录包含依据、执行人、时间、影响范围与核对结果。

### 11.8 发布记录

发布记录必须包含：

1. 版本、提交标识、构建时间与校验和。
2. 变更摘要与破坏性变更。
3. 验证结果与证据位置。
4. 部署时间、执行人、目标环境与结果。
5. 回滚点与回滚验证结果。
6. 已知问题与后续动作。

## 运行与维护规则

### 12.1 服务目标

本章规定运行规则；逐步操作由 `docs/RUNBOOK.md` 所有。

1. 每项对外服务的可用性、关键接口时延与恢复目标必须声明。
2. 目标必须有测量方法、统计窗口与数据来源。
3. 目标由项目负责人批准，写入运行手册；变更目标必须记录原因。
4. 未达到目标时必须建立事件或改进项，不得静默接受。

### 12.2 监控与告警

1. 必须监控：服务状态、错误率、关键接口时延、队列深度、磁盘、内存、数据库大小、备份时间、证书有效期与设备在线数。
2. 告警必须可执行：说明现象、影响、判断依据、处置手册与责任人。
3. 告警不得重复轰炸；同一原因聚合为一条事件。
4. 告警阈值必须有依据；调整阈值必须记录原因。
5. 监控数据保留期必须满足事件复盘与容量分析。

### 12.3 日志与审计

1. 日志按 `core/logging.py` 生成。
2. 审计记录必须覆盖身份变更、权限变更、配置变更、发布、回滚、数据修正与设备控制命令。
3. 审计记录必须只追加；不得修改或删除单条记录。
4. 审计记录访问必须最小权限；导出必须记录。
5. 审计保留期由安全所有者文档规定；清理任务必须可审计。

### 12.4 备份与恢复

1. 必须备份：数据库、发布物、配置模板、签名密钥与必要的运行记录。
2. 备份必须加密；密钥与备份分开保管。
3. 备份必须有保留策略、校验和与异地副本。
4. 恢复演练每季度执行一次；记录恢复时间、完整性与数据差异。
5. 恢复目标必须定义：可接受的数据丢失窗口与恢复时间。
6. 备份失败必须告警；连续失败必须建立事件。

### 12.5 事件管理

| 级别 | 示例 | 响应 |
|---|---|---|
| SEV1 | 数据丢失、安全事件、服务不可用、设备失控 | 立即处置；恢复后 24 小时内建立事件记录 |
| SEV2 | 关键功能不可用、设备大面积离线 | 当日处置；建立事件记录 |
| SEV3 | 非关键功能异常、单设备异常 | 下一工作日处置 |
| SEV4 | 文案、样式与体验问题 | 排期处理 |

事件级别使用 SEV1 至 SEV4；缺陷与审阅发现使用 S1 至 S4（定义见 §16.7），两套级别不得混用。

1. 事件处置顺序：保护人身与设备安全 → 阻止影响扩大 → 恢复服务 → 保留证据 → 根因分析。
2. 事件记录必须包含时间线、影响、处置、证据、根因、改进项与责任人。
3. 根因分析针对系统与流程，不针对个人。
4. 改进项必须有责任人与期限；到期未完成必须升级。

### 12.6 问题管理

1. 重复发生的事件必须升级为问题，分析共同根因。
2. 问题记录必须链接相关事件、变更与 ADR。
3. 修复必须包含防止复发的测试或检查。
4. 问题关闭必须验证改进项有效。

### 12.7 容量与补丁

1. 必须跟踪数据库大小、日志增长、备份空间、设备数量与连接数。
2. 容量达到阈值前必须扩容或清理；不得等到磁盘耗尽。
3. 运行时、依赖、证书与系统组件必须跟踪支持期限；到期前完成升级。
4. 补丁必须先在非生产环境验证；生产补丁按 §11 执行。
5. 无法立即补丁的漏洞必须有补偿控制、期限与责任人。

### 12.8 设备队列

1. 每台设备必须有唯一标识、档案、信任状态、版本与在线状态。
2. 设备注册与信任必须由责任人批准；撤销必须立即生效。
3. 设备端版本分布必须可见；升级必须分阶段。
4. 设备离线、版本异常、存储不足与命令失败必须可见。
5. 设备退役必须撤销身份、清理数据、卸载程序并记录。

### 12.9 数据保留

1. 每类数据必须有保留期、依据、责任人与清理方式。
2. 统计数据与不可复现证据不得按普通日志清理。
3. 个人数据保留必须满足最小化原则；超过保留期必须删除或匿名化。
4. 删除必须有批准记录；删除后必须验证不可恢复。
5. 备份中的过期数据按备份保留策略处理；不得无限保留。

### 12.10 访问控制

1. 生产访问按最小权限授予；默认只读。
2. 生产变更必须通过批准与审计；紧急变更必须事后补记录。
3. 密钥、令牌与证书不得共享；离职或角色变化必须撤销。
4. 访问权限每季度复核一次；无使用记录的权限必须撤销。
5. 远程访问必须使用加密通道与强身份认证；不得暴露管理端口到公网。

## 日常任务

### 发布新 APK

```powershell
.\deploy\publish_apk.ps1 -Identity <admin身份标识>
```

脚本自己从 `android/app/build.gradle.kts` 读版本号、跑门禁、构建、上传（带 `-SkipBuild`
可用已构建的 APK）。**版本号不手填**：手填等于把手里的值复制一次，而 OTA 覆盖安装要求新旧
版本可比，填错的表现是设备不升级或反复升级，偏静默。版本号在哪几处、为什么合并不了，
见 `docs/adr/`「OTA 与版本」。

响应里的 `notified_devices` 非空即表示在线设备已收到通知。设备会自行下载安装并重连，
无需人工介入——实测约 10 秒完成。

### 升级循环（设备反复断开重连、版本不动）

签名：`/ota/manifest` 请求暴增 + 同一台设备反复"接入→断开"、`ota_release_install`
里同一版本出现多条记录。2026-10-24 全线发生过：发布登记 0.4.40、APK 字节里却是
0.4.39/83，设备 `pm install` Success 而版本不变，永远"有新版"。

处置顺序：**先下架止血**（`DELETE /api/v1/ota/releases/<version>`，设备停手回到当前
版本），再查原因。服务端发布接口会解析 APK 内容版本并拒绝不一致的发布——那条
路走通了说明发布本身没问题；设备端同一版本重试 3 次后也会停在
`ota.attempts-exhausted`（device.log，设备页可见），恢复方式是修正发布后重推。

### 其他

| 任务 | 命令 |
|---|---|
| 服务状态 | `ssh iseekdream.com 'docker ps --filter name=ntu-shower'` |
| 实时日志 | `ssh iseekdream.com 'docker logs -f ntu-shower'` |
| 备份列表 | `ls -lh /var/backups/ntu-shower/` |
| 抓设备画面 | `POST /api/v1/devices/<id>/capture`（返回 `data.image_base64`） |
| 远程执行 shell | `POST /api/v1/devices/<id>/exec`，body `{"command": "..."}` |
| 修改设备端时间 | `POST /api/v1/devices/<id>/clock/set`，body `{"datetime": "YYYY-MM-DD HH:MM:SS"}`（设备本地墙钟） |
| 重启本程序 | `POST /api/v1/devices/<id>/app/restart`（不重启整机，也不动浴室 APP） |
| 重启浴室 App | `POST /api/v1/devices/<id>/bath/restart`（先强停再冷启动，会中断进行中的会话） |
| 重启整机 | `POST /api/v1/devices/<id>/reboot` |
