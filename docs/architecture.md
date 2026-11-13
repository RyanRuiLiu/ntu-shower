# ntu-shower 架构

## 系统全景

```text
浴室App(com.datalook.bath, bath.db)
        │ root sqlite 读取
        ▼
┌─────────────────┐   WSS /gateway/device   ┌─────────────────────────┐
│  设备 APK        │◀═══════════════════════▶│  <public-domain> │
│  (Android 4.4.4) │   HTTPS /api/v1/*       │  nginx 443 唯一公网入口    │
│  ANDROID_ID 标识 │   /ota/manifest       │  ┌───────────────────┐  │
│  自更新/自管理    │                         │  │ FastAPI 单服务进程  │  │
└─────────────────┘                          │  └───────────────────┘  │
                                             └─────────────────────────┘
```

前端是 `server/web/` 下的零构建静态文件，由本进程挂在根路径
（`/api/v1`、`/ota`、`/gateway` 与 `/healthz` 的路由注册在前，不会被静态文件抢走）。
用户面接口无需凭据即可查询状态，操作类需 `Authorization: Bearer` 凭据（或管理台
签名会话 Cookie）+ 能力位，见 modules/access 的守卫。

## 已定稿的传输层事实（2026-09 真机实测）

- 设备：Allwinner DS83X，Android 4.4.4（API 19），1.8G 内存，1366×768，adbd 即 root
- 设备无 DNS（系统 DNS 配的 8.8.8.8 被校园网封锁）；114.114.114.114 的 UDP/TCP 53 实测可用 → APK 内置 DNS 解析器，**以域名为唯一主键，不使用缓存、次等解析路径或硬编码地址**
- 系统 TLS 栈上限 TLSv1.2：`SSLSocket.supportedCipherSuites` 真机枚举 54 个，无任何 AES-GCM/CHACHA20（2026-11 实测），系统栈只能 CBC
- 因此 APK 内置 Conscrypt（BoringSSL）提供 TLS1.2 AEAD；服务端为纯 AEAD（TLS1.3 + TLS1.2 GCM/CHACHA20），不保留 CBC
- 实测握手：设备 ClientHello 仅 3 个 AEAD 套件（CHACHA20 / AES128-GCM / AES256-GCM），协商 CHACHA20-POLY1305
- 服务端单张 ECDSA 通配符证书
- 信封 `v` 的升级条件：新增可选字段不升版本（收端忽略未知键，ADR-0006）；改字段语义、
  删字段等不兼容变化升 `v` 并两端同发。网关对入站帧校验 `v`：hello 不符回
  `proto.version_mismatch` 并断开（1002），会话中途不符回错后同样断开——不静默丢帧，
  那会让新协议设备表现为"连着但什么都不动"

## 服务端平面

**版本化的单一资源树**：对外 HTTP 面统一挂 `/api/v1` 前缀（路径版本化，破坏性
变更升 v2）；URL 只表达资源，不表达权限，谁能调用由能力位守卫决定。模块按
领域划分，`/devices/*` 由 devices、sessions、bridge、config 各域按资源归属提供
端点。

| 域 | 职责（具体路径的权威在代码：各模块的 `APIRouter` 与 `core/protocol.py`） |
|---|---|
| gateway | 设备长连接；调试通道隧道。路径为设备硬编码契约，无版本前缀、冻结 |
| devices | 设备档案与远程操作 |
| doors | 喷头现态与实时推送（door.update 数据承载，直接应用） |
| sessions | 会话操作，目标设备在路径里 |
| config | 全局配置、登记表、单台覆盖 |
| voice | 固定音频素材库 / 分段规则 / 事件投递记录 / 素材下载；设备面 `/voice/assets/{id}.mp3` |
| identities | 身份域读模型与签发/编辑/撤销 |
| ota | 版本清单 / 下载 / 发布管理 |
| audit | 接口调用与命令级审计，以 trace_id 关联 |

**这张表列的是分工，不是路径清单**：路径会随功能增删而变，而它们已经写在代码里（各模块的
`APIRouter` 前缀加上装饰器），前端那一侧由 `tests/test_api_contract.py` 守着。在文档里再抄
一份只会多一个无人守的副本。

### 鉴权模型（modules/access）

**统一身份制。** URL 不出现权限词；能力位是唯一权限模型。身份通道：
管理台走签名会话 Cookie（`/api/v1/auth/login`），其余调用方走
`Authorization: Bearer` 凭据（登记主体时签发，只存哈希，见 `persistence/principals`）。

| 端点类别 | 能做什么 | 凭证 |
|---|---|---|
| 公开 | 设备与喷头查看、实时推送、发布清单与下载 | 无需凭据 |
| 用户操作 | 分配 / 结算（`session`）、拍照（`photo`），设备须在作用域内 | Bearer 凭据 + 能力位 × 设备作用域 |
| 名单登记 | 增删任意学号在四份 `*_students` 名单里的登记（`tasks`）。**名单是 SERVER scope、不分设备，所以判定只看能力位、不看作用域** | Bearer 凭据 + `tasks` 能力位 |
| 统计查看 | 一台设备一个窗口的全套统计、时间点回放、个人统计（`stats`）。**同样不看设备作用域**——统计页自己选设备，而个人统计按学号跨设备取 | Bearer 凭据 + `stats` 能力位 |
| 管理 | 运维档案、远程调试、配置、名单、发布写、审计 | Bearer 凭据 + `admin` 能力位 |

**为什么是统一身份制、能力位为什么是唯一的权限模型、以及"无注册流程"与"密码不落库"的
理由，见 [ADR-0008](adr/0008-identity.md)。** 这里只留它长什么样：

- 管理台身份持有 `["admin","session","photo"]`，代用户操作时走与用户**同一条**路径，
  审计 actor 即其身份——不存在特权通道。
- `/api/v1/devices` 的 `actions` 由服务端按调用方算出（access 唯一判定），客户端不复制作用域规则。

### HTTP 错误响应

对齐 RFC 9457 Problem Details：`application/problem+json`，
`{type, title, status, detail}`，统一由 `core/errors.py` 装配（含 422 校验错误）。

## 设备端结构

```text
com.ryanliu.ntushower
├── core/       配置（域名锚点/运行时配置）、身份、内置 DNS、TLS、HTTP 工厂、root shell、性能日志
├── transport/  协议编解码（MessageCodec 单一来源）、网关客户端（连接/认证/心跳/重连）
├── features/   doors 采集上报 / automation A11y 自动化（含遮盖层）/ notice 屏幕提示 / statusline 楼层状态行 / overlay 共用浮层窗口 / voice 语音播报 / water 水控板直控 / remote 调试通道与截屏 / selfmanage 自更新与无障碍启用
├── service/    AgentService 主服务 / A11yService 无障碍实体
├── receiver/   覆盖安装自启（MY_PACKAGE_REPLACED）与开机广播（标准 ROM 路径）
└── ShowerApp   崩溃捕获
```

### 设备端拉起矩阵（全部真机实测）

| 场景 | 拉起者 | 恢复实测 |
|---|---|---|
| 开机 | 系统重绑无障碍 → A11yService 自举 AgentService（本 ROM 不投递开机广播） | ✅ 30 秒 |
| OTA/手动重装 | MY_PACKAGE_REPLACED（stopped 状态也送达） | ✅ 两次 OTA + 桥上 install -r |
| 进程被杀 | 看门狗告警（进程死后系统侧仍存活） | ✅ ~60 秒 |
| 崩溃 | ShowerApp 未捕获异常 → 1 秒告警重启 | 实现（机理同看门狗） |
| 浴室 App 被杀 | A11yService 事件流 → BathAppGuard 决策拉起 | 实现并单测 |

多路径并发拉起经 AgentService.onStartCommand 一次性初始化守卫收敛，无冲突。

## 时间轴与口径

系统里流动着两条时间轴。**这一节是它们的唯一规范**：设备端、服务端、消费者的相关代码都按它
理解时间字段，字段属于哪条轴不写进各自模块的注释，只写在这里。

### 两条轴

| 轴 | 形态 | 出现位置 |
|---|---|---|
| 设备轴 | `yyyyMMddHHmmss`，设备墙钟读数，不含时区 | 浴室 App 的 `t_doorlist`、设备上报的帧载荷 |
| 服务端轴 | RFC 3339 UTC 毫秒 | 库内一切时刻、对外一切时刻、帧里的 `captured_at`（由设备侧锚定，见 `core/clock/ServerClock`） |

设备轴的原值只在原始层保留（`door_snapshot.doors`），供溯源与重放；派生层与对外接口一律是
服务端轴。

**两处例外**：`link.pong` 的 `device_time` 与帧载荷里的 `device_clock`，都是设备墙钟上的当前
读数，但经过设备侧格式化、呈 RFC 3339 形态。格式不改变它们的轴——它们必须与 `t_doorlist` 的
墙钟同源，所以取墙钟；用途见 `server/app/core/device_clock.py`。除这两处，上报的时刻一律走锚定。

### 三条换算规则

**一、段时长取同一口钟的差值，恒为真实值。**

一段出水从开阀到关阀的秒数 = 关阀时刻 − 该段开阀时刻。开阀时刻在段开始那一帧记下
（`in_time`），关阀时刻在段结束那一帧取得（`close_time`；设备在写它的同一条语句里把
`in_time` 清空，所以两个值分属相邻两帧）。

两个读数出自同一口钟，**差值与被拨动多少无关**，因此不需要任何修正。这是本系统唯一无需
换算即成立的量。各段相加即一场会话的出水总秒数。

**二、每个读数用它自己那一刻的偏差。**

```text
assigned_at = open_time  + skew@开会话那一刻
opened_at   = in_time    + skew@开这一段那一刻
closed_at   = close_time + skew@关这一段那一刻
```

`open_time` 在整场会话里不变——设备只在释放喷头时清零，暂停与恢复都不改它——所以它平移
出来的是**真实的分配时刻**，不是下界。`in_time` 与 `close_time` 各自平移，得到那一段的真
实起止。

**逐个平移之所以安全，是因为窗口内偏差是常量**：一次占用压在 20 分钟以内（设备满
`maxbathtime` 就强制释放），而 20 分钟里的漂移只有 0.03 秒。跳变只有 23:5x 那一处，而设备
23:53 会自动释放所有喷头——任何一次会话都不会跨过它。（为什么这么定、被它取代的那套锚点
换算错在哪，见 [ADR-0002](adr/0002-time-axis.md)。）

早先的做法是"整场会话只用一次偏差"（拿 `open_time` 当锚点、其余时刻相对它换算）。那确实
让基准唯一，但把**锚点之后发生的拨钟摊进了后面每个读数**：段内被拨了十分钟，那一段的时长
就凭空长出十分钟。改用逐个平移，再用"设备轴差 vs 观测差"交叉校验把被污染的段认出来
（见 `service._close_segment`）。

偏差本身从哪来，见 `server/app/core/device_clock.py`——那里是它的唯一出处。

**三、算不出真实值时，与浴室 App 同口径。**

设备侧的判断都是"某个已存储的时刻与此刻之差"，而"此刻"取自设备钟：

```text
|openTime  − now|    等待开阀
|closeTime − now|    关阀空闲
|inTime    − now|    本段已淋浴时长
bathTime             各段累加，每次关阀时 += |inTime − now|
```

钟一旦变化这些值全部变化，且因为是绝对值，**向前拨与向后拨的后果相同**。四个自动释放条件
（`maxinspacetime` / `maxbathtime` / `maxbathspacetime` / 关机前 5 分钟）都建立在这些差值上。

当偏差还无从确定或设备离线（差值里混进了与使用无关的时间）时，本项目**按同样的公式、用第一条
与第二条给出的时刻重算**，得到与浴室 App 一致的数字，并如实标明这一段不可信。"无从确定"的
判据在 `server/app/core/device_clock.py`。

### 离线与重启

**浴室 App 重启不清库。** 启动时的 `initDoorInfos()` 只做三件事：删掉不在本班牌范围内的行、
补齐缺失的行、重写水控板映射；`cardno` / `certno` / `openTime` / `inTime` / `closeTime` /
`bathTime` 原样保留。断电、进程被杀、覆盖安装之后的形态一样。

重启后的第一轮扫描（一秒内）就会处置这些残留行，按残留形态命中不同条件：

| 离线时学生在 | 库里的残留 | 命中的条件 | 该次的时长口径 |
|---|---|---|---|
| 已分配未开阀 | `openTime` 有 | `\|openTime − now\| ≥ maxinspacetime` | 不计时长 |
| 正在出水 | `openTime` + `inTime` 有 | `\|openTime − now\| ≥ maxbathtime` | `min(\|inTime − now\| + bathTime, maxbathtime)` |
| 暂停中 | `openTime` + `closeTime` 有 | `\|closeTime − now\| ≥ maxbathspacetime` | `bathTime` |
| 已选中未分配 | `status = 2` | `\|openTime − now\| ≥ maxinspacetime` | 不计时长 |

**"正在出水"与"暂停中"的差别只在 `inTime` 是否为空**，但两者的时长口径相差悬殊：前者把整个
离线时长计入并截断到上限，后者完全不计入。

设备在线但两次上报之间隔了较长时间（漏帧、读库失败）与真正的离线是两回事，判据是网关的
连接事件（`device_link_events` 表），而不是帧间隔。

### 消费者的契约

| 字段 | 轴 | 含义 |
|---|---|---|
| `open_time` | 服务端 | 分配时刻，由设备轴换算 |
| `in_time` | 服务端 | 当前这一段的开阀时刻；等待开阀时为空。**暂停后不空**——设备清空了 `inTime`，这一段的开阀时刻由段表回答 |
| `close_time` | 服务端 | 本次关阀时刻；正在出水时为空 |
| `bath_time_s` | — | 各段出水秒数之和，是时长不是时刻，不受设备钟影响 |
| `reported_at` | 服务端 | 这一帧的采集时刻 |
| `last_used_at` | 服务端 | 上次开阀时刻；取自该喷头上一个**已关阀**的段，会话表只用来说明是谁用的 |

**对外不出现设备轴的值。** 设备轴没有时区标记，任何消费者按本地时间解析都会得到错误结果
（实际发生过：设备钟快一小时，网页把分配时刻渲染成 `00:55`，并把 `|时刻 − 本地时间|` 当成
已洗时长在计）。

## 签名

`android/ntu-shower.keystore`（RSA 2048，有效期至 2056；别名与口令由密钥保管方提供）。
**为什么必须一步到位用正式证书、以及它丢了会怎样，见 [ADR-0013](adr/0013-ota.md)。**

证书指纹（SHA-256，OTA 时设备端校验用）：`72ac1447302225b16a621be9461c6202c4796f787acc780b376b75c2572b7fb5`

## 设备上的旧系统组件（卸载前必读）

迁移期设备上仍有旧系统组件。它们在**仍保留它的设备上**是有意保留的路径，不是残留脏数据：

| 包 | 归属 | 说明 |
|---|---|---|
| `com.ryanliu.ntushower` | 本项目 | 主力 agent |
| `com.ntu.remoteshower.showeragent` | 旧系统 | 迁移期保留；**2F 已卸载** |
| `com.ntu.remoteshower.adbtunnel` | 旧系统 | 迁移期保留；**2F 已卸载** |
| `com.datalook.bath` | 浴室 App | 被控对象，不可动 |

**2026-10-25**：2F 浴室（设备标识不入文档）经人确认卸载上述两个旧包，设备上只剩本项目；
1F/3F–6F 未动。卸载**只由人显式决定并手动执行**，不写自动卸载脚本，也不在部署流程里代为清理。

**调试时必须知道的一件事**（真机实测结论，以仍保留旧组件的设备为前提）：旧系统的两个无障碍服务
与本项目的 `A11yService` **同时处于启用状态**。UI 自动化出现随机异常时，先排除旧组件干扰，
再怀疑本项目代码。**本项目不读取、不写入、不依赖旧系统的任何资源。**
