# ntu-shower 协议 v1

状态：**v1 已上线** · 传输：WSS (TLS 1.2) · 编码：JSON 文本帧

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
- 错误码：点分命名空间，**示例均取自实际实现**，如 `proto.hello_required`（首帧不是 link.hello）、
  `proto.bad_json`（帧不是合法 JSON）、`proto.unknown_type`（未知消息类型）、
  `proto.unknown_action`（未知命令动作）、`session.unsupported_by_device`（设备未声明该能力）；
- link.error 帧结构：payload 为 `{ code, message, details? }`（扁平）——**code 是协议，message 不是**。

### 0.3 Schema 管理

每种 type 一份 JSON Schema（draft 2020-12）存 `protocol/schema/`；CI 校验样例消息，
并反向断言"schema 文件集合 == 消息清单"（`tests/test_protocol_schemas.py`）。
新增字段 = 加可选字段 + schema 更新，协议不升版；破坏性变更才升 `v`。

### 0.4 设备端原则

设备**无界面**、零配置。本地不存任何业务配置：

- **身份**来自系统 `ANDROID_ID`（不落文件、不混淆加密，见 §1）；
- **域名**来自构建配置（`gradle.properties` 的 `serverDomain`，唯一构建期锚点）；
- 其余一切（心跳间隔、上报去抖、OTA 策略、自动化节奏）由服务端经 `hello_ack.config`
  与 `config.update` 下发；
- 唯一例外是**发件箱**（`door_outbox.db`）：待确认的快照按序暂存于此，
  ack 后删除，用于断网期间不丢数据。

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
- `capabilities` 由设备声明（`transport/SupportedActions.ALL`），服务端在下发命令前比对，拒绝把设备不认识的命令发过去。空数组表示**未声明**（旧版本），此时不拦截；
- 威胁模型如实声明：device_id 即身份（无第二凭据），伪造需要物理读取目标设备的 ANDROID_ID；设备是我们自己的，接受此取舍。

## 2. 心跳与时钟（已确认）

```
link.ping   双向发起，payload: { sender_time }
link.pong   原路回应相同结构（sender_time 为各自的发送时刻）
```

两个间隔虽都叫"心跳"，但方向与用途不同，**不要混用**：

| 配置键 | 默认 | 谁发起 | 用途 |
|---|---|---|---|
| `heartbeat_interval_s` | 30s | **设备**（`GatewayClient` 的定时器） | 向服务端表明存活 |
| `server_ping_interval_s` | 60s | **服务端**（网关的 ping 循环） | 测量并维护时钟偏差（EMA） |

**在线状态的判定依据是活动的 WebSocket 连接，不是心跳超时**：
设备掉线时由连接断开触发 `set_device_offline`（网关 `finally` 块）。
`device.online` 字段是**最后一次上报值**，与内存中的活动连接可能不一致——
判断设备是否真在线，以日志里有无 `WebSocket /gateway/device [accepted]` 为准（见 RUNBOOK §4）。

## 3. 状态上报

```
door.snapshot        （设备→服务端，全量）
{ snapshot_id: uuid,
  source: "db_watch" | "startup",
  doors: [ { door_no, card_no, cert_no, acc_no,
             open_time, in_time, close_time,
             bath_time_s, status_raw } ] }

door.snapshot_ack    （服务端→设备）{ snapshot_id }
```

- **设备端零派生**：字段与浴室 App 数据库 `t_doorlist` 的列一一对应，
  `status_raw` 是原始整数（0/1/2/3），**状态派生在服务端完成**（见 `doors/service.derive_state`）；
- 时间字段来自设备本地时区，设备端转成 UTC RFC 3339 后上报；空值表示该时刻尚未发生；
- `source` 区分触发方式：`db_watch` = 浴室 App 写库引发的事件驱动采集，
  `startup` = 设备启动时的首次全量；
- 全量而非增量：新增字段按可选字段加，不改协议版本；
- **发件箱保证不丢**：设备侧 SQLite 按序保存、ack 才删、未 ack 按序重发；
  服务端按 `snapshot_id` 去重（ADR 与 schema 见 `protocol/schema/door.snapshot.json`）。

## 4. 命令与结果

```
session.command      （服务端→设备）{ action, params, deadline_ts }
session.command_result（设备→服务端，reply_to）
{ status: "ok"|"failed"|"blocked"|"timeout",
  reason_code: "session.unsupported_by_device"|…,   // 结构化子状态
  data: {…} }                                        // 按 action 而异
```

**`action` 清单**（正则约束见 schema）：`session.assign` / `session.settle` /
`photo.capture`（摄像头）/ `screen.capture`（全屏截图，screenrecord+MediaCodec——
设备 HWC 不支持 screencap，2026-09-30 对照实验确认）/ `debug.exec` /
`app.restart` / `app.uninstall` / `bridge.open` / `bridge.close`。
未知 action → `session.command_result` with `reason_code=proto.unknown_action`。
ADB 桥接数据不走本消息通道（独立二进制 WS，见 ADR-0035）。

- 设备端命令路由为注册制：新功能 = 注册 handler + 在 `SupportedActions` 声明能力位；
- 服务端在下发前比对设备声明的 `capabilities`，空数组表示未声明（不拦截）；
- 超时由服务端 `deadline_ts` 控制，设备过时拒绝执行并回 `timeout`；
- **`data` 按 action 而异**：`photo.capture`/`screen.capture` 回 `image_base64`，
  `debug.exec` 回 `output`。它是开放对象，客户端按 action 分支取字段。

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
| bridge.open | 服务端→设备 | — | bridge.open.json |
| bridge.close | 服务端→设备 | — | bridge.close.json |

桥接数据帧不走本协议：配对完成后切换至独立二进制 WS（见 ADR-0035）。
