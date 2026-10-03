# ntu-shower 架构

## 迁移期的设备共存（2026-09 真机核实，**卸载前必读**）

目标设备上**同时装着两套系统**。旧系统组件是**有意保留的回退路径**，不是残留脏数据：

| 包 | 归属 | 说明 |
|---|---|---|
| `com.ryanliu.ntushower` | 本项目 | 主力 agent |
| `com.ntu.remoteshower.showeragent` | 旧系统 | **保底**，暂不卸载 |
| `com.ntu.remoteshower.adbtunnel` | 旧系统 | 旧系统的 ADB 方案（本项目改用 443 网关透传，docs/DECISIONS.md） |
| `com.datalook.bath` | 浴室 App | 被控对象，不可动 |

**退出条件**：新项目具备完全自主能力后，**由人手动卸载**旧组件。
不要写自动卸载脚本，也不要在部署流程里代为清理——保底路径的移除必须是显式决定。

**调试时必须知道的三件事**（均为真机实测结论）：

1. 旧系统的两个无障碍服务与本项目的 `A11yService` **同时处于启用状态**。
   UI 自动化出现随机异常时，先排除旧组件干扰，再怀疑本项目代码。
2. 旧 agent 仍在向**旧服务端**上报，其 `device_id` 是自定的 `ntu-shower-001`；
   本项目用真实 ANDROID_ID（如 `1c94310a536777d6`）。两套标识互不相通，
   在旧系统数据库里按 ANDROID_ID 查不到任何东西。
3. 两套服务端完全独立：目录、systemd 单元、nginx 站点、端口（桥接 15555 vs 15655）、
   数据库全部分开，代码互不引用。**本项目不读取、不写入、不依赖旧系统的任何资源。**

## 系统全景

```
浴室App(com.datalook.bath, bath.db)
        │ root sqlite 读取
        ▼
┌─────────────────┐   WSS /gateway/device   ┌─────────────────────────┐
│  设备 APK        │◀═══════════════════════▶│  ntu-shower.iseekdream.com │
│  (Android 4.4.4) │   HTTPS /api/v1/*       │  nginx 443 统一入口       │
│  ANDROID_ID 标识 │   /ota /ops           │  ┌───────────────────┐  │
│  自更新/自管理    │                         │  │ FastAPI 单服务进程  │  │
└─────────────────┘                          │  └───────────────────┘  │
                                             └─────────────────────────┘
```

前端是 `server/web/` 下的零构建静态文件，由本进程挂在根路径
（`/api`、`/ops`、`/docs` 的路由注册在前，不会被静态文件抢走）。
用户面接口无需凭据即可查询状态，操作类需 `X-Identity` 标识 + 能力位。

## 已定稿的传输层事实（2026-09 真机实测）

- 设备：Allwinner DS83X，Android 4.4.4（API 19），1.8G 内存，1366×768，adbd 即 root
- 设备无 DNS（系统 DNS 配的 8.8.8.8 被校园网封锁）；114.114.114.114 的 UDP/TCP 53 实测可用 → APK 内置 DNS 解析器，**以域名为唯一主键，无缓存/兜底/硬编码**
- TLS 上限 TLSv1.2，最强套件 ECDHE-ECDSA/RSA-AES-CBC；**最小配置**（强制 TLSv1.2 + 默认套件 + 打包 ISRG X1/X2）对 nginx 443 / ECDSA 证书实测握手成功
- **OkHttp 套件陷阱（真机 E2E 实测发现）**：OkHttp 默认白名单中 ECDHE-ECDSA 仅含 GCM 系列，而本设备只有 CBC——对 ECDSA 证书握手必然失败（对 RSA 证书反而能通，这是旧系统侥幸可用的原因）。解法：显式 ConnectionSpec 放行 ECDHE-ECDSA/RSA-CBC + GCM，见 `transport/GatewayClient.kt`
- 服务端单张 ECDSA 通配符证书；旧系统为迁就 4.4 而维护的 RSA 证书在新系统中不存在

## 设计原则（用户定死）

1. 禁止兜底、禁止降级、禁止兼容垫层、禁止硬编码环境值
2. 能新则新（构建工具链：Gradle 9.8 + AGP 9.4 + 内置 Kotlin）；设备运行时冻结在 4.4
3. 能简则简：一个域名、一个端口、一张证书、一个服务进程
4. 换服务器 = 只改 DNS 记录，零现场

## 服务端平面

**版本化的单一资源树**：对外 HTTP 面统一挂 `/api/v1` 前缀（路径版本化，破坏性
变更升 v2）；URL 只表达资源，不表达权限，谁能调用由能力位守卫决定。模块按
领域划分，`/devices/*` 由 devices、sessions、bridge、config 各域按资源归属提供
端点。

| 域 | 路径 | 职责 |
|---|---|---|
| gateway | `/gateway/device`（WSS）、`/gateway/bridge`（WSS） | 设备长连接；调试通道隧道。路径为设备硬编码契约，无版本前缀、冻结 |
| devices | `GET /api/v1/devices`（精简+actions，公开）、`GET /api/v1/devices/runtime`（运维档案）、`PATCH /api/v1/devices/{id}`、远程动作 `/api/v1/devices/{id}/exec` 等 | 设备档案与远程操作 |
| doors | `GET /api/v1/doors?device_id=`、`WS /api/v1/stream?device_id=` | 喷头现态与实时推送（door.update 数据承载，直接应用） |
| sessions | `POST /api/v1/devices/{id}/assign`、`…/settle`、`…/photo` | 会话操作，目标设备在路径里 |
| config | `GET/PUT /api/v1/config`、`GET /api/v1/config/spec`、`/api/v1/devices/{id}/config` | 全局配置、登记表、单台覆盖 |
| identities | `/api/v1/identities`（名单响应含能力位枚举） | 身份域读模型与签发/编辑/撤销 |
| ota | `/ota/manifest`（设备硬编码契约，无前缀冻结）、`/api/v1/ota/releases`（读公开、写 admin）、`/api/v1/ota/releases/{v}/download` | 版本清单 / 下载 / 发布管理 |
| audit | `GET /api/v1/audit/calls`、`GET /api/v1/audit/commands` | 接口调用与命令级审计，以 trace_id 关联 |

### 鉴权模型（modules/access）

**统一身份制，全部凭 `X-Identity`。** URL 不出现权限词；能力位是唯一权限模型。

| 端点类别 | 能做什么 | 凭证 |
|---|---|---|
| 公开 | 设备与喷头查看、实时推送、发布清单与下载 | 无需凭据 |
| 用户操作 | 分配 / 结算（`session`）、拍照（`photo`），设备须在作用域内 | `X-Identity` + 能力位 × 设备作用域 |
| 管理 | 运维档案、远程调试、配置、名单、发布写、审计 | `X-Identity` + `admin` 能力位 |

- 管理台不是特权通道：管理台身份持有 `["admin","session","photo"]`，代用户
  操作时走与用户同一路径，审计 actor 即其身份。
- **无注册流程**：授权经 `/api/v1/identities` 预置（小程序用户登记 openid，
  网页用户签发一个标识），撤销即从名单删除；能力位与作用域修改即时生效
  （每请求实时查库）。
- `/api/v1/devices` 的 `actions` 字段由服务端按调用方算出（access 唯一判定），
  客户端不复制作用域规则。
- 小程序身份：openid 由 status-panel 的登录端点换取（本项目**不含微信交互**），
  小程序直连本项目、请求带 `X-Identity: <openid>`，本项目直接认该字符串为身份。
- 浴室账号密码**不落库**：随操作请求直传设备，由浴室系统验证。

### HTTP 错误响应

对齐 RFC 9457 Problem Details：`application/problem+json`，
`{type, title, status, detail}`，统一由 `core/errors.py` 装配（含 422 校验错误）。

## 设备端结构

```
com.ryanliu.ntushower
├── core/       配置（域名锚点/运行时配置）、身份、内置 DNS、TLS、HTTP 工厂、root shell、性能日志
├── transport/  协议编解码（MessageCodec 单一来源）、网关客户端（连接/认证/心跳/重连）
├── features/   doors 采集上报 / automation A11y 自动化（含遮盖层）/ selfmanage 自更新与无障碍启用
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
读数，但经过设备侧格式化、呈 RFC 3339 形态。格式不改变它们的轴——它们专门用于测偏差，必须与
`t_doorlist` 的墙钟同源，所以取墙钟。除这两处，上报的时刻一律走锚定（`captured_at`）。

### 三条换算规则

**一、段时长取同一口钟的差值，恒为真实值。**

一段出水从开阀到关阀的秒数 = 关阀时刻 − 该段开阀时刻。开阀时刻在段开始那一帧记下
（`in_time`），关阀时刻在段结束那一帧取得（`close_time`；设备在写它的同一条语句里把
`in_time` 清空，所以两个值分属相邻两帧）。

两个读数出自同一口钟，**差值与被拨动多少无关**，因此不需要任何修正。这是本系统唯一无需
换算即成立的量。各段相加即一场会话的出水总秒数。

**二、每个读数用它自己那一刻的偏差。**

```
assigned_at = open_time  + skew@开会话那一刻
opened_at   = in_time    + skew@开这一段那一刻
closed_at   = close_time + skew@关这一段那一刻
```

`open_time` 在整场会话里不变——设备只在释放喷头时清零，暂停与恢复都不改它——所以它平移
出来的是**真实的刷卡时刻**，不是下界。`in_time` 与 `close_time` 各自平移，得到那一段的真
实起止。

**逐个平移之所以安全，是因为窗口内偏差是常量**：一次占用压在 20 分钟以内（设备满
`maxbathtime` 就强制释放），而 20 分钟里的漂移只有 0.03 秒。跳变只有 23:5x 那一处，而设备
23:53 会自动释放所有喷头——任何一次会话都不会跨过它。（为什么这么定、被它取代的那套锚点
换算错在哪，见 `docs/DECISIONS.md`。）

早先的做法是"整场会话只用一次偏差"（拿 `open_time` 当锚点、其余时刻相对它换算）。那确实
让基准唯一，但把**锚点之后发生的拨钟摊进了后面每个读数**：段内被拨了十分钟，那一段的时长
就凭空长出十分钟。现在改为逐个平移，再用"设备轴差 vs 观测差"交叉校验把被污染的段认出来
（见 `service._close_segment`）。

偏差取自两条通路：**每帧带的 `device_clock`**（与 `captured_at` 取自同一瞬间，毫秒级）与
设备回的 `link.pong`（两个读数隔一个网络往返，秒级）。前者是主力，后者在空闲时段把已经稳
住的偏差维持住。

**三、算不出真实值时，与浴室 App 同口径。**

设备侧的判断都是"某个已存储的时刻与此刻之差"，而"此刻"取自设备钟：

```
|openTime  − now|    入门空闲
|closeTime − now|    关阀空闲
|inTime    − now|    本段已出水时长
bathTime             各段累加，每次关阀时 += |inTime − now|
```

钟一旦变化这些值全部变化，且因为是绝对值，**向前拨与向后拨的后果相同**。四个自动释放条件
（`maxinspacetime` / `maxbathtime` / `maxbathspacetime` / 关机前 5 分钟）都建立在这些差值上。

当偏差无法确定（这台设备还没有任何偏差记录——`link.pong` 与帧带读数两条通路都没给过）或
设备离线（差值里混进了与使用无关的时间）时，我们**按同样的公式、用第一条与第二条给出的时刻
重算**，得到与浴室 App 一致的数字，并如实标明这一段不可信。

### 离线与重启

**浴室 App 重启不清库。** 启动时的 `initDoorInfos()` 只做三件事：删掉不在本班牌范围内的行、
补齐缺失的行、重写水控板映射；`cardno` / `certno` / `openTime` / `inTime` / `closeTime` /
`bathTime` 原样保留。断电、进程被杀、覆盖安装之后的形态一样。

重启后的第一轮扫描（一秒内）就会处置这些残留行，按残留形态命中不同条件：

| 离线时学生在 | 库里的残留 | 命中的条件 | 该次的时长口径 |
|---|---|---|---|
| 入门未开阀 | `openTime` 有 | `\|openTime − now\| ≥ maxinspacetime` | 不计时长 |
| 正在出水 | `openTime` + `inTime` 有 | `\|openTime − now\| ≥ maxbathtime` | `min(\|inTime − now\| + bathTime, maxbathtime)` |
| 暂停中 | `openTime` + `closeTime` 有 | `\|closeTime − now\| ≥ maxbathspacetime` | `bathTime` |
| 已选中未刷卡 | `status = 2` | `\|openTime − now\| ≥ maxinspacetime` | 不计时长 |

**"正在出水"与"暂停中"的差别只在 `inTime` 是否为空**，但两者的时长口径相差悬殊：前者把整个
离线时长计入并截断到上限，后者完全不计入。

设备在线但两次上报之间隔了较长时间（漏帧、读库失败）与真正的离线是两回事，判据是网关的
连接事件（`device_link_event`），而不是帧间隔。

### 消费者的契约

| 字段 | 轴 | 含义 |
|---|---|---|
| `open_time` | 服务端 | 入门时刻，由设备轴换算 |
| `in_time` | 服务端 | 本次开阀时刻；已关阀时为空 |
| `close_time` | 服务端 | 本次关阀时刻；正在出水时为空 |
| `bath_time_s` | — | 各段出水秒数之和，是时长不是时刻，不受设备钟影响 |
| `reported_at` | 服务端 | 这一帧的采集时刻 |
| `last_used_at` | 服务端 | 上次开阀时刻，来自会话记录 |

**对外不出现设备轴的值。** 设备轴没有时区标记，任何消费者按本地时间解析都会得到错误结果
（实际发生过：设备钟快一小时，网页把入门时刻渲染成 `00:55`，并把 `|时刻 − 本地时间|` 当成
已洗时长在计）。

## 签名

`config/keystore/ntu-shower.keystore`（RSA 2048，别名/密码 `ntu-shower`，有效期至 2056）。
自更新要求新旧 APK 签名一致——此文件是全系统唯一长期绑定物件，必须异地备份。

证书指纹（SHA-256，OTA 时设备端校验用）：`72ac1447302225b16a621be9461c6202c4796f787acc780b376b75c2572b7fb5`

## 构建验证记录

- 2026-09-29：Gradle 9.8.0 + AGP 9.4.1 + 内置 Kotlin + JDK 25 + compileSdk 37 + minSdk/targetSdk 19
- debug / release 均构建成功；release 由 ntu-shower 证书 V2 签名
- lint 仅豁免 `ExpiredTargetSdkVersion`（设备冻结 4.4、不进 Google Play，有意决策）
