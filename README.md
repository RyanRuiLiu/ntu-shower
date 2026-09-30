# ntu-shower

NTU 浴室系统二代：寄生代理设备端（Android 4.4.4 老中控）+ 单服务端 + API 优先。替代 `ntu-remote-shower`（旧系统并行运行至退役）。

设备端三件主功能：**读**（浴室 App 数据库状态采集）/ **操作**（无障碍自动化：分配·结算·拍照）/ **活**（自更新、看门狗、崩溃自愈等自生命周期）。其余功能均为该地基上的扩展。

## 项目结构

| 目录 | 职责 |
|---|---|
| `android/` | 设备端 APK（Kotlin，minSdk/targetSdk 19）：`core/` 基础设施 · `transport/` 网关客户端与协议编解码 · `features/` doors 采集 / automation 自动化 / selfmanage 自更新 |
| `server/` | FastAPI 模块化单体：`gateway/` 设备 WS 网关 · `modules/` doors/sessions/shadow/auth/devices/config/ota 各领域 · `core/` 协议/鉴权/错误/时钟 |
| `protocol/` | 协议单一事实源：全部消息类型的 JSON Schema（draft 2020-12），CI 样例校验 |
| `docs/` | `ARCHITECTURE.md` 架构 · `PROTOCOL.md` 协议 · `DECISIONS.md` 决策记录 |
| `config/keystore/` | APK 签名密钥（gitignored，异地备份——全系统唯一长期绑定物件） |

## 运行模型

- 唯一入口：`https://<public-domain>`（443 统一入口，单张 ECDSA 通配符证书，nginx 路径分流）
- 设备身份：`device_id = ANDROID_ID`，后台人工点"信任"后通信（见 docs/PROTOCOL.md §1）
- 鉴权角色：anonymous 只读 / user 可操作设备（JWT）/ admin 全功能（JWT 或 X-Admin-Token）
- 配置：设备运行参数全部由服务端远程下发（全局默认 + 设备覆盖），设备零本地配置

## 快速开始

```bash
# 服务端（开发）
cd server && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
cp .env.example .env && .venv/Scripts/uvicorn app.main:app --reload --port 8000

# 设备端（构建需先复制 android/gradle.properties.example）
cd android && ./gradlew :app:assembleRelease

# 质量门
cd server && ruff check app tests && mypy app && pytest -q   # 服务端
cd android && ./gradlew :app:testDebugUnitTest detekt         # 设备端
```

## 质量约定

- 禁止兼容/降级/迁移/兜底/保底代码；未上线，随时删旧重建
- 服务端：ruff（含 import 排序）+ mypy 严格模式 + pytest，全部零违规
- 设备端：detekt（CI/JDK21）+ JUnit 单测覆盖协议编解码/配置/解析纯函数
- 协议变更必须三处同步：schema、docs/PROTOCOL.md、双端实现
