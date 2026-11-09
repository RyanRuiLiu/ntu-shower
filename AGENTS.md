# AGENTS.md

本文件是 AI 协作者的唯一入口。人类协作者从 [README.md](README.md) 开始；工程规则的所有者是 [STANDARDS.md](STANDARDS.md)。

## 项目目标

把 ntu-shower 逐步改造成：以业界标准为唯一依据、单一事实来源、每批可停可续可回滚、多个 AI 能无缝接续的项目。

## 当前状态

- 生产形态：容器运行，nginx 反代回环端口；设备网关与网页管理台同源。
- 版本：以 `server/app/core/version.py` 的常量为唯一来源，本文件不写死。
- 生产细节与回滚资产：见 [docs/RUNBOOK.md](docs/RUNBOOK.md)。

## 必读顺序

1. 本文件。
2. 按任务查阅下方「文档索引」中的所有者文档。

## 硬规则

1. 依据原则：每个改动标注依据（标准编号、RFC、官方文档链接）；指不出依据的标为「项目约定」，不得冒充标准。
2. 单一事实来源：同一主题只写一处，其余链接；不复制表、清单、命令全集与字段全集。
3. 单批次：一次只做一件事；范围冻结，顺手发现的问题只记入待办。
4. 行为变更：改行为必须有测试覆盖或兼容期（Expand-Contract），否则不合并。
5. 可发布：每批结束必须可发布，不留半成品。
6. 回滚：生产改动前留回滚点，并在提交信息记录。
7. 不换栈：不引入新框架、不改技术栈，除非有 ADR 批准。
8. 无验收不做：没有明确验收标准的改动不做。

## 文档索引（按需查阅，不必每次读）

| 主题 | 所有者 |
|---|---|
| 项目定位、快速开始、目录导览 | [README.md](README.md) |
| 开发环境、分支、提交、质量门命令 | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 工程规则：生命周期、代码、文本、审阅 | [STANDARDS.md](STANDARDS.md) |
| 系统边界、组件、数据流、部署拓扑 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 数据模型、字段语义、接口索引 | [docs/REFERENCE.md](docs/REFERENCE.md) |
| 部署、回滚、备份、故障处置 | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| 资产清单、数据分级、威胁模型 | [docs/SECURITY.md](docs/SECURITY.md) |
| 测试策略、层级、覆盖率政策 | [docs/TESTING.md](docs/TESTING.md) |
| 决策背景、备选方案、后果 | [docs/adr/](docs/adr/) |
| 发布历史与用户可见变更 | [CHANGELOG.md](CHANGELOG.md) |

## 代码入口

| 任务 | 入口 |
|---|---|
| 动作名、执行通道、动作边界 | `server/app/core/actions.py` |
| 会话续期、预约续期、自动结算 | `server/app/modules/sessions/` |
| 延长服务时间、MCU 重布、拨钟 | `server/app/modules/extend_hours/` |
| 免费名单 | `server/app/modules/freelist/` |
| 语音规则、素材与播放 | `server/app/modules/voice/` |
| 术语正名与弃用词 | `server/app/core/terms.py` |
| HTTP 问题类型与命令失败原因 | `server/app/core/problems.py`、`server/app/core/reason_codes.py` |
| 配置键的类型、范围与归属 | `server/app/modules/config/spec.py` |
| 协议消息结构 | `protocol/schema/*.json` |
| 数据库命名与时间格式 | `server/app/core/db.py` |

## 质量门

完整命令在 [CONTRIBUTING.md](CONTRIBUTING.md) 的「质量门」小节，与 CI 逐条对应。本文件不复制命令。

一改一验：每次修改后立即运行相关门禁，不攒一批再验。

## 停止条件

遇到以下情况必须停止并向人类确认，不得自行继续：

- 需要写生产数据库、删除生产数据、修改生产配置或执行生产命令。
- 需要读取、复制或写入密钥、令牌、签名材料与真实个人信息。
- 需要推送、发布、部署或回滚生产环境。
- 需要绕过质量门、关闭检查、跳过审阅或隐藏失败。
- 需求与 STANDARDS.md、所有者文档或现有契约冲突。
- 无法核对标准、版本、协议、外部事实或测试结果。
- 需要删除受版本控制的文件、历史数据、证据记录或发布物。
- 需要修改权限、CI/CD 配置、密钥保管方式或生产访问控制。

## 生产边界

- 生产机为 `root@iseekdream.com`，服务为 `ntu-shower`。
- 生产机只读。允许的动作只有：读查询、看日志、`systemctl status`，以及 RUNBOOK 写明的运维动作与经明确授权的数据修正。
- 不在生产机上运行测试、演练、重算或临时脚本。需要验证时把数据副本取回本地。
- `deploy/` 是 nginx、systemd、备份与部署脚本的唯一事实来源；不得在服务器上直接改配置。

## 跨项目边界

`status-panel` 是独立项目，消费本站接口，维护自己的实现。本站只对 `protocol/schema/`、HTTP 路径与响应形状、错误码负责；接口变更要通知对方，不得直接修改对方仓库。
