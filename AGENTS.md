# AGENTS.md

AI 协作者的唯一入口。人类协作者从 [README.md](README.md) 开始。

## 项目

寄生在浴室中控设备上的控制系统：设备端读浴室 App 的 `t_doorlist` 一张表、用无障碍服务操作它的界面，
服务端汇聚多台设备，提供 HTTP API、WebSocket 推送与网页管理台。版本号的唯一来源是
`server/app/core/version.py`。

## 开工

1. 读本文件。
2. 跑 `sh scripts/check.sh`，确认基线全绿再动手。
3. 按任务查下方「文档索引」，不要通读全部文档。

## 规则分两类

### 一、机器执行的（不靠自觉）

| 约束 | 执行者 | 定义处 |
|---|---|---|
| 代码风格、命名、导入排序、异常与日志规范 | `ruff check` | `server/pyproject.toml` |
| 类型完整性 | `mypy --strict` | `server/pyproject.toml` |
| 模块边界：模型层不依赖业务模块；持久化层不依赖各模块的 API 契约 | `lint-imports` | `server/pyproject.toml` |
| 安全：注入、路径穿越、密钥泄漏、盲捕获 | `semgrep`（p/python、p/security-audit） | — |
| 代码与配置不得引用已删除的文档或文档章节号 | `semgrep` | `.semgrep.yml` |
| 行为、契约、时间轴、状态派生 | `pytest`，覆盖率下限 96% | `server/pyproject.toml` |
| 三端契约一致：动作名、状态、配置键、原因码、版本形状 | `test_contract_sync.py` 逐字节比对生成物 | `server/tools/sync_contracts.py` |
| 提交信息格式 | `.githooks/commit-msg`；CI 对 PR 内每个提交重跑 | `.githooks/commit-msg` |
| 依赖漏洞 | `pip-audit` | `.github/workflows/ci.yml` |
| 已入库的秘密 | `gitleaks`（全历史） | `.github/workflows/ci.yml` |
| 文档格式与同文档锚点 | `markdownlint` | `.markdownlint.json` |
| 网页端 JS：漏导入（no-undef）与内联样式纪律 | `eslint` | `server/web/eslint.config.mjs` |
| 网页端 CSS 令牌纪律 | `stylelint` | `server/web/.stylelintrc.json` |
| 设备端风格、单测与覆盖率下限 98% | detekt / lint / JaCoCo | `android/app/build.gradle.kts`、`android/detekt.yml` |

**想加一条约束，就加一个检查。** 写进文档不算数——文档不会被执行。
同一事实只写一处，其余链接；能由代码、`/openapi.json` 或 `protocol/schema/` 读出的事实，不写进文档。

### 二、靠审阅判断的（只有四条，工具测不出来）

1. **依据**：改动要能指向标准编号、RFC、官方文档或 ADR；指不出，就在提交信息里标为「项目约定」。
2. **不兜底**：不写降级路径、兼容层与「以后再删」的临时代码；确需过渡的，写明移除条件并当期移除。
3. **一次一件事**：一个提交只做一类变更；顺手发现的问题单独提交，不混进来。
4. **不确定就停**：见下方「停止条件」。

## 文档索引

| 主题 | 所有者 |
|---|---|
| 项目定位、快速开始、目录导览 | [README.md](README.md) |
| 开发环境、分支、提交、质量门命令 | [CONTRIBUTING.md](CONTRIBUTING.md) |
| 系统边界、组件、数据流、部署拓扑、时间轴定义 | [docs/architecture.md](docs/architecture.md) |
| 状态派生规则、取数口径、不变量 | [docs/reference.md](docs/reference.md) |
| 部署、回滚、备份、故障处置 | [docs/runbook.md](docs/runbook.md) |
| 资产清单、数据分级、威胁模型 | [docs/security.md](docs/security.md) |
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
| HTTP 问题类型与命令失败原因 | `server/app/core/problems.py`、`server/app/core/reason_codes.py` |
| 配置键的类型、范围与归属 | `server/app/modules/config/spec.py` |
| 协议消息结构 | `protocol/schema/*.json` |
| 数据库命名与时间格式 | `server/app/db/base.py` |

## 停止条件

遇到以下情况必须停止并向人类确认，不得自行继续：

- 需要写生产数据库、删除生产数据、修改生产配置或执行生产命令。
- 需要读取、复制或写入密钥、令牌、签名材料与真实个人信息。
- 需要推送、发布、部署或回滚生产环境。
- 需要绕过质量门、关闭检查、跳过审阅或隐藏失败。
- 需求与文档、已有契约或现有测试冲突。
- 无法核对标准、版本、协议、外部事实或测试结果。
- 需要删除受版本控制的文件、历史数据、证据记录或发布物。
- 需要修改权限、CI/CD 配置、密钥保管方式或生产访问控制。

## 生产边界

- 生产机为 `root@iseekdream.com`，服务为 `ntu-shower`。
- 生产机只读。允许的动作只有：读查询、看日志、`systemctl status`，以及 runbook 写明的运维动作与经明确授权的数据修正。
- 不在生产机上运行测试、演练、重算或临时脚本。需要验证时把数据副本取回本地。
- `deploy/` 是 nginx、systemd、备份与部署脚本的唯一事实来源；不得在服务器上直接改配置。

## 跨项目边界

`status-panel` 是独立项目，消费本站接口，维护自己的实现。本站只对 `protocol/schema/`、HTTP 路径与响应形状、错误码负责；接口变更要通知对方，不得直接修改对方仓库。
