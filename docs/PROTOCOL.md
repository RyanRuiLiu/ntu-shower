# ntu-shower 协议 v1

状态：**草案（待用户最终确认）** · 传输：WSS (TLS 1.2) · 编码：JSON 文本帧

## 0. 总则

### 0.1 信封

所有消息共用：

| 字段 | 类型 | 说明 |
|---|---|---|
| `v` | int | 协议版本，现为 `1`；不匹配则回 `link.error(proto.version_mismatch)` 并断开 |
| `type` | string | 消息类型，命名空间 `<层>.<名>` |
| `id` | string (UUIDv4) | 本条消息唯一 id |
| `ts` | string (RFC 3339 UTC 毫秒) | 发送方时间戳，如 `2026-09-29T12:34:56.789Z` |
| `payload` | object | 各类型自定义字段 |
| `reply_to` | string (UUIDv4) | 仅响应消息携带 = 请求的 id |

### 0.2 命名规范

- 字段名一律 snake_case；
- 消息类型：`<层>.<名>`，层 = `link`（链路）/ `door`（喷头）/ `session`（会话）/ `ota`（自更新）/ `config`（配置）/ `bridge`（ADB 桥，服务端→设备单向指令，设备侧桥接数据走独立二进制 WS /gateway/bridge）；
- 错误码：点分命名空间，如 `auth.invalid_token`、`pair.rate_limited`、`proto.unknown_type`、`session.busy`；
- link.error 帧结构：payload 为 `{ code, message, details? }`（扁平）——**code 是协议，message 不是**。

### 0.3 Schema 管理

每种 type 一份 JSON Schema（draft 2020-12）存 `protocol/schema/`；前端 TS 类型由 schema 生成；CI 校验样例消息。新增字段 = 加可选字段 + schema 更新，协议不升版；破坏性变更才升 `v`。

### 0.4 设备端原则

设备**无界面**、零配置：本地只存 `device_id`（自生成 UUID）与 `token`；域名来自构建配置（唯一锚点）；其余一切（心跳间隔、上报频率、OTA 策略）由服务端下发。

## 1. 连接与认证（后台人工认领）

**身份**：`device_id = ANDROID_ID`（系统自带标识，一行代码取得）。重装 APK 不变；恢复出厂会变——变了就是"新设备"，在后台重新点一次"信任"即可。不存文件、不做混淆加密。

**新设备入网**（唯一人工步骤）：新设备上电自动连接 → `link.hello`（携带 device_id）→ 服务端设备表中无此 id 或未信任 → 回 `hello_ack{trusted:false}`，连接保持但不给任何业务；管理员在后台"待认领"列表点**信任** → 该设备此后通信正常。

**日常连接**：`link.hello` → 服务端校验 device_id 已信任 → `link.hello_ack`（含 `server_time` 与 `config`）→ 进入业务。

```
link.hello        { device_id, app_version, os_version, capabilities:[…] }
link.hello_ack    { trusted: bool, server_time, config:{…}, upgrade:{…}|null }
link.error        { code, message, details? }
```

- 信任在服务端设备表里是一个布尔位，随时可撤销（撤销后设备回到"待认领"状态）；
- `capabilities` 由设备声明，服务端只下发设备声明过能力的命令——扩展功能的通道；
- 威胁模型如实声明：device_id 即身份（无第二凭据），伪造需要物理读取目标设备的 ANDROID_ID；设备是我们自己的，接受此取舍。

## 2. 心跳与时钟（已确认）

```
link.ping   双向发起，间隔来自 hello_ack.config（默认 30s），payload: { device_time | server_time }
link.pong   原路回应相同结构
```

服务端 90s 无心跳判离线；两端用 ping/pong 维护时钟偏差（EMA），所有入库时间统一校准到服务端时钟。

## 3. 状态上报

```
door.snapshot        （设备→服务端，全量）
{ snapshot_id, source: "db_watch"|"scheduled",
  doors: [ { door_no, status: "idle"|"in_use"|"unknown",
             occupied_since: ts|null, bath_time_s, raw:{…} } ] }

door.snapshot_ack    （服务端→设备）{ snapshot_id }
```

- 全量而非增量；状态为受控枚举，原始字段放 `raw`（扩展不改协议）；
- 设备发件箱（SQLite）按序保存、ack 才删、未 ack 按序重发（指数退避）；服务端按 `snapshot_id` 去重；
- 触发：浴室 App 数据库变化事件 + 低频定时全量（间隔来自 config），防状态漂移。

## 4. 命令与结果

`session.command` 的 `action` 清单（正则约束见 schema）：
`session.assign` / `session.settle` / `photo.capture`（摄像头）/ `screen.capture`（全屏截图，
screenrecord+MediaCodec——设备 HWC 不支持 screencap，2026-09-30 对照实验确认）/ `debug.exec` /
`app.restart` / `app.uninstall` / `bridge.open` / `bridge.close`。
未知 action → `session.command_result` with `reason_code=proto.unknown_action`。
ADB 桥接数据不走本消息通道（独立二进制 WS /gateway/bridge，见 ADR-0035）。

```
session.command      （服务端→设备）{ action, params, deadline_ts }
session.command_result（设备→服务端，reply_to）
{ status: "ok"|"failed"|"blocked"|"timeout",
  reason_code: "session.no_balance"|…,   // 结构化子状态
  data: {…} }
```

- 一期 action：`session.assign`、`session.settle`、`photo.capture`（结果含 base64 图）、`app.restart`、`app.uninstall`、`device.factory_reset`（自管理命令，用户点名要求）；
- 设备端命令路由为注册制：新功能 = 注册新 handler + 声明能力位；
- 超时由服务端 `deadline_ts` 控制，设备过时拒绝执行并回 `timeout`。

## 5. 配置下发

```
config.update        （服务端→设备）{ config:{…}, revision }
config.ack           （设备→服务端）{ revision }
```

设备启动即拉取（hello_ack.config）+ 服务端可随时推送；本地不落除 token/设备 id 外的任何业务配置。

## 6. OTA 自更新

```
ota.upgrade_available（服务端→设备）{ version, url, sha256, cert_fp, mandatory }
ota.progress         （设备→服务端）{ stage: "downloading"|"verifying"|"installing"|"done"|"failed", detail }
```

- 设备下载 APK → 校验 sha256 与**签名证书指纹**（`72ac1447…bfb5`，见 ARCHITECTURE.md）→ root 静默安装 → 服务自重启；
- 服务端按设备灰度（release 注册表：版本、强制标志、目标设备列表）；
- 指纹不符 = 拒绝安装（防投毒）。

## 7. 消息类型总表

| type | 方向 | 回复 | Schema 文件 |
|---|---|---|---|
| link.hello | 设备→服务端 | link.hello_ack | link.hello.json |
| link.hello_ack | 服务端→设备 | — | link.hello_ack.json |
| link.ping / link.pong | 双向 | — | link.ping.json |
| link.error | 服务端→设备 | — | link.error.json |
| door.snapshot | 设备→服务端 | door.snapshot_ack | door.snapshot.json |
| door.snapshot_ack | 服务端→设备 | — | door.snapshot_ack.json |
| session.command | 服务端→设备 | session.command_result | session.command.json |
| session.command_result | 设备→服务端 | — | session.command_result.json |
| config.update / config.ack | 服务端→设备 / 反向 | — | config.update.json |
| ota.upgrade_available / ota.progress | 服务端→设备 / 反向 | — | ota.upgrade_available.json |
