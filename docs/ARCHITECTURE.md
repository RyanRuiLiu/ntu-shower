# ntu-shower 架构

## 系统全景

```
浴室App(com.datalook.bath, bath.db)                 微信小程序 / Web 前端
        │ root sqlite 读取                                  │ 只调接口
        ▼                                                   ▼
┌─────────────────┐   WSS /gateway/device   ┌─────────────────────────┐
│  设备 APK        │◀═══════════════════════▶│  <public-domain> │
│  (Android 4.4.4) │   HTTPS /api/v1/*       │  nginx 443 统一入口       │
│  配对码激活       │   /ota /admin           │  ┌───────────────────┐  │
│  自更新/自管理    │                         │  │ FastAPI 单服务进程  │  │
└─────────────────┘                          │  └───────────────────┘  │
                                             └─────────────────────────┘
```

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
| gateway | `/gateway/device` (WSS) | 设备长连接：认证/心跳/快照/命令/配置下发 |
| modules.doors | `/api/v1/doors` + `/api/v1/stream` (WS) | 用户与前端：状态查询与实时推送（匿名只读） |
| modules.sessions | `/api/v1/sessions`、`/api/v1/photos` | 已认证用户：分配/结算/拍照（写操作需 user 角色） |
| modules.shadow | `/api/v1` 与 `/admin` 的 shadow 端点 | 设备影子（reported/desired/metadata） |
| modules.auth | `/api/v1/auth/token`、`/admin/users` | 令牌签发与账户管理 |
| modules.devices | `/admin/devices` | 管理面：设备档案/信任/改名 |
| modules.config | `/admin/config`、`/admin/devices/{id}/config` | 管理面：全局默认 + 设备覆盖 |
| modules.ota | `/ota/*`、`/admin/releases` | 版本清单/下载 + 发布管理 |
| admin 审计 | `/admin/commands` | 命令审计查询 |

### 鉴权模型（core/security）

| 角色 | 能力 | 凭证 |
|---|---|---|
| anonymous | 只读接口（doors/shadow/stream） | 无 |
| user | + 操作设备（assign/settle/photo） | Bearer JWT（外部身份断言换取，provider 抽象：internal 已实现，wechat/app 预留） |
| admin | 全功能（设备/配置/OTA/调试/审计/用户管理） | JWT role=admin 或 X-Admin-Token（运维通道） |

账户无注册流程：管理员经 `/admin/users` 预置分配。

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
├── receiver/   覆盖安装自启（MY_PACKAGE_REPLACED）与开机广播（标准 ROM 兼容）
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
