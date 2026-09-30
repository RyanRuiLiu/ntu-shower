# 决策记录

全部架构决策以 MADR v3 格式登记于 [`docs/decisions/`](decisions/)，按编号升序追加（新决策取下一个编号，不重写历史）。

| 编号 | 决策 | 日期 |
|---|---|---|
| [0001](decisions/0001-greenfield-replacement.md) | 全新项目替代旧系统 | 2026-09-29 |
| [0002](decisions/0002-fastapi-modular-monolith.md) | 服务端技术栈 FastAPI 模块化单体 | 2026-09-29 |
| [0003](decisions/0003-multi-device-form-factor.md) | 多设备形态：同一 APK + 服务端设备注册表 | 2026-09-29 |
| [0004](decisions/0004-device-identity-androidid.md) | 设备入网身份：ANDROID_ID + 后台人工信任 | 2026-09-29 |
| [0005](decisions/0005-entry-topology-443.md) | 入口拓扑：443 统一入口 + nginx 路径分流 | 2026-09-29 |
| [0006](decisions/0006-domain-and-certificate.md) | 域名与证书 | 2026-09-29 |
| [0007](decisions/0007-built-in-dns-resolver.md) | 设备寻址：内置 DNS 解析器 | 2026-09-29 |
| [0008](decisions/0008-wss-json-envelope.md) | 协议编码：WSS + JSON 信封 | 2026-09-29 |
| [0009](decisions/0009-apk-signing-once.md) | APK 签名一步到位 | 2026-09-29 |
| [0010](decisions/0010-build-toolchain-modern.md) | 构建工具链能新则新 | 2026-09-29 |
| [0011](decisions/0011-no-fallback-philosophy.md) | 设计哲学：禁止兜底/降级/兼容/硬编码 | 2026-09-29 |
| [0012](decisions/0012-device-headless-config.md) | 设备零界面零本地配置 | 2026-09-29 |
| [0013](decisions/0013-protocol-conventions.md) | 协议规范定稿 | 2026-09-29 |
| [0014](decisions/0014-ota-self-update.md) | OTA 全自动自更新链路 | 2026-09-30 |
| [0015](decisions/0015-snapshot-time-calibration.md) | 快照时间按服务器偏差校准 | 2026-09-30 |
| [0016](decisions/0016-inproc-event-bus.md) | 实时推送进程内事件总线 | 2026-09-30 |
| [0017](decisions/0017-no-card-occupant-identity.md) | 无卡模式占用者标识 | 2026-09-30 |
| [0018](decisions/0018-fileobserver-modify-mask.md) | FileObserver 掩码必须含 MODIFY | 2026-09-30 |
| [0019](decisions/0019-quality-gates.md) | 质量门防代码漂移 | 2026-09-30 |
| [0020](decisions/0020-ota-push-and-force-release.md) | OTA 实时推送与 force 调试发布 | 2026-09-30 |
| [0021](decisions/0021-remote-config-14-keys.md) | 远程配置 14 项全集中下发 | 2026-09-30 |
| [0022](decisions/0022-parasite-agent-essence.md) | 项目本质定稿：寄生代理三件主功能 | 2026-09-30 |
| [0023](decisions/0023-device-shadow-abstraction.md) | 设备影子抽象 | 2026-09-30 |
| [0024](decisions/0024-crash-guard-as-extension.md) | 崩溃守护定性为可配置扩展 | 2026-09-30 |
| [0025](decisions/0025-role-model-rbac.md) | 角色模型：只读/用户/管理员三级 | 2026-09-30 |
| [0026](decisions/0026-dispatch-domain-structure.md) | 服务端结构对齐 Dispatch 领域结构 | 2026-09-30 |
| [0027](decisions/0027-protocol-envelope-single-source.md) | 协议信封单一来源 | 2026-09-30 |
| [0028](decisions/0028-rfc9457-problem-json.md) | HTTP 错误响应 RFC 9457 | 2026-09-30 |
| [0029](decisions/0029-bcrypt-official-library.md) | 密码哈希用 bcrypt 官方库 | 2026-09-30 |
| [0030](decisions/0030-real-function-resource-guards.md) | 资源约束按真实功能设计 | 2026-09-30 |
| [0031](decisions/0031-madr-decision-records.md) | 决策记录迁移 MADR v3 | 2026-09-30 |
| [0032](decisions/0032-test-strategy-coverage-exemptions.md) | 测试策略：全面覆盖 + 赦免制 | 2026-09-30 |
| [0033](decisions/0033-versioning-release-policy.md) | 版本与发布规范 | 2026-09-30 |
| [0034](decisions/0034-user-plane-anonymous-auth.md) | 用户面操作接口匿名，废除用户登录体系 | 2026-09-30 |
| [0035](decisions/0035-adb-bridge-zero-port.md) | 真实 ADB 经 443 网关透传（零新端口） | 2026-09-30 |
| [0036](decisions/0036-ota-install-tracking.md) | OTA 安装状态跟踪（防 force 死循环） | 2026-09-30 |
