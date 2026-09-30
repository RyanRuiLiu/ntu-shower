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
   本项目用真实 ANDROID_ID（如 `<device-id>`）。两套标识互不相通，
   在旧系统数据库里按 ANDROID_ID 查不到任何东西。
3. 两套服务端完全独立：目录、systemd 单元、nginx 站点、端口（桥接 15555 vs 15655）、
   数据库全部分开，代码互不引用。**本项目不读取、不写入、不依赖旧系统的任何资源。**

## 系统全景

```
浴室App(com.datalook.bath, bath.db)
        │ root sqlite 读取
        ▼
┌─────────────────┐   WSS /gateway/device   ┌─────────────────────────┐
│  设备 APK        │◀═══════════════════════▶│  <public-domain> │
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

| 平面 | 路径 | 职责 |
|---|---|---|
| gateway | `/gateway/device`、`/gateway/bridge`（均 WSS） | 设备长连接；调试通道隧道 |
| modules.doors | `/api/v1/doors`、`/ops/doors`、`/api/v1/stream`（WS） | 喷头现态与实时推送。两个查询端点**同源同形**，只是后者多一道令牌校验 |
| modules.sessions | `/api/v1/sessions/*`、`/ops/sessions/*`、`/ops/commands` | 分配 / 结算 / 拍照；命令审计查询 |
| modules.shadow | `/api/v1/devices/{id}/shadow`、`/ops/devices/{id}/shadow` | 设备影子（reported / desired / metadata） |
| modules.access | `/api/v1/identity`、`/ops/identities` | 小程序用 code 换身份、查询自身能力；授权名单维护 |
| modules.devices | `/ops/devices` | 设备档案 / 信任 / 改名 / 远程执行 / 截图 |
| modules.config | `/ops/config`、`/ops/devices/{id}/config` | 配置登记表、全局默认与设备覆盖 |
| modules.ota | `/ota/*`、`/ops/releases` | 版本清单 / 下载 + 发布管理 |
| modules.bridge | `/ops/devices/{id}/bridge/*` | 调试通道经 443 透传（零新端口，docs/DECISIONS.md） |

### 鉴权模型（core/security + modules/access）

**按接口面分三档，不设角色。** 不同的人拿到的是不同的**接口请求权限**——权限本身就是
能力位，没有"这个人是什么角色"这一层，也就不存在"按角色下发不同内容"。

| 接口面 | 能做什么 | 凭证 |
|---|---|---|
| 查看类 | 读喷头现态、阴影、实时推送 | 无需凭据 |
| 用户操作类 | 分配 / 结算（需 `ops`）、拍照（需 `photo`） | `X-Identity` + 能力位 |
| 管理面 | 设备 / 配置 / OTA / 调试 / 名单 | `X-Ops-Token` |

- **无注册流程**：授权经 `/ops/identities` 预置（小程序用户登记 openid，
  网页用户签发一个标识），撤销即从名单删除。
- 小程序身份：`wx.login` 的 code → 服务端换 openid → 直接作为标识返回，
  不另发会话令牌（docs/DECISIONS.md）。
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

## 签名

`config/keystore/ntu-shower.keystore`（RSA 2048，别名/密码 `ntu-shower`，有效期至 2056）。
自更新要求新旧 APK 签名一致——此文件是全系统唯一长期绑定物件，必须异地备份。

证书指纹（SHA-256，OTA 时设备端校验用）：`72ac1447302225b16a621be9461c6202c4796f787acc780b376b75c2572b7fb5`

## 构建验证记录

- 2026-09-29：Gradle 9.8.0 + AGP 9.4.1 + 内置 Kotlin + JDK 25 + compileSdk 37 + minSdk/targetSdk 19
- debug / release 均构建成功；release 由 ntu-shower 证书 V2 签名
- lint 仅豁免 `ExpiredTargetSdkVersion`（设备冻结 4.4、不进 Google Play，有意决策）
