# 架构决策记录

本目录存放架构决策记录（ADR），一决策一文件。状态取值：Accepted / Superseded / Deprecated。

| 编号 | 标题 | 状态 |
|---|---|---|
| [ADR-0001](0001-overall-direction.md) | 总体取向 | Accepted |
| [ADR-0002](0002-time-axis.md) | 时间轴：两条铁律 | Accepted |
| [ADR-0003](0003-data-layering.md) | 数据分层：五张表 | Accepted |
| [ADR-0004](0004-stats.md) | 统计层：口径与数据集 | Accepted |
| [ADR-0005](0005-architecture.md) | 架构与模块 | Accepted（其中「设备影子」一条 Deprecated，见文内后记） |
| [ADR-0006](0006-protocol-contract.md) | 协议与契约 | Accepted |
| [ADR-0007](0007-device.md) | 设备端 | Accepted |
| [ADR-0008](0008-identity.md) | 身份与能力 | Superseded（被 ADR-0019 取代） |
| [ADR-0009](0009-remote-config.md) | 远程配置 | Accepted |
| [ADR-0010](0010-screen-hint.md) | 屏幕提示的控制边界 | Accepted |
| [ADR-0011](0011-floor-status.md) | 楼层状态行的控制边界 | Accepted |
| [ADR-0012](0012-voice.md) | 语音播报的控制边界与时间口径 | Accepted |
| [ADR-0013](0013-ota.md) | OTA 与版本 | Accepted |
| [ADR-0014](0014-observability.md) | 可观测性 | Accepted |
| [ADR-0015](0015-water-board.md) | 水控板直控的现场依据 | Accepted |
| [ADR-0016](0016-testing.md) | 测试与质量 | Accepted |
| [ADR-0017](0017-deploy.md) | 部署与入口 | Accepted |
| [ADR-0018](0018-backup-no-encryption.md) | 数据库备份暂不加密 | Accepted |
| [ADR-0019](0019-auth-standardization.md) | 认证标准化——删除自定 `X-Identity` 头，统一 Bearer 凭据与会话 | Accepted |
| [ADR-0020](0020-device-enrollment.md) | 设备接入采用 claim 自注册（fleet provisioning by claim） | Accepted |
| [ADR-0021](0021-gateway-1008.md) | 网关凭据校验在 accept 之后，以 RFC 6455 1008 关闭 | Accepted |
| [ADR-0022](0022-android-api19-freeze.md) | 设备端依赖冻结：API 19 兼容线 | Accepted |
