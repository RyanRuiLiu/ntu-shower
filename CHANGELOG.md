# Changelog

本文件从 0.6.2 起按 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) 维护。变更分类固定为 Added、Changed、Deprecated、Removed、Fixed、Security。版本号采用 [Semantic Versioning 2.0.0](https://semver.org/)。

## [Unreleased]

## [0.6.3] - 2026-10-30

### Added

- 新增 `STANDARDS.md`：覆盖生命周期、文档、代码、文本、审阅、发布、运行、安全与 AI 协作的工程标准。
- 新增 `CONTRIBUTING.md`：开发环境、质量门、分支、提交、审阅与发布准备步骤。
- 新增 `docs/SECURITY.md` 与 `docs/TESTING.md`：安全控制与测试策略的所有者文档。

### Changed

- 重写 `README.md` 与 `AGENTS.md`：入口文档只保留定位、快速开始与导航；AI 操作边界与停止条件集中到 `AGENTS.md`。
- 文档体系按唯一所有者重整：架构、参考、运行手册、安全、测试与决策记录各自负责一类事实。
- 修正文档中的失效引用、重复命令、过期版本说明与路径不一致。
- 网页端审计、配置与界面检视的页签、列表行、树节点和菜单支持键盘操作；表单补齐标签与分组名称。
- 设备端将启动配置读取、数据库打开与 TLS 初始化移出主线程；测试改为事件驱动，不再依赖固定睡眠。

### Fixed

- 修复概览页喷头卡片的转义遗漏。
- 为设备 WebSocket 发送、调试桥写入与命令通道等待增加超时与截止时间处理。
- 限制设备日志上送大小；超出上限的请求直接拒绝，不入库。
- 网页端统一设备未选择与重启确认文案，补充 `prefers-reduced-motion` 支持，移除无效样式与重复文案。
- 修复 Python 3.10 下取消命令被忽略：已放弃的命令不再继续下发并占住通道。

### Security

- stdout 日志不再写入身份标识与完整学号；审计表中的请求原文按受限数据管理。
- nginx 增加内容安全策略，并移除样式来源的 `'unsafe-inline'`。
- 设备端约束 OTA 清单 URL 与版本格式；移除 `READ_EXTERNAL_STORAGE` 权限，`BootReceiver` 不再导出。

### Removed

- 移除仓库中的生成物与临时验证产物；运行数据、覆盖率报告与本地配置不进入版本控制。

## [0.6.2] - 2026-10-30

### Added

- 首个按本文件维护的基线记录。此日期是记录日期，不是历史发布日；此前变更见 Git 记录。
