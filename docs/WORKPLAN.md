# WORKPLAN

本文件是批次进度的唯一来源。[AGENTS.md](../AGENTS.md) 引用本文件；其他文档不复制进度。

## 状态机

待办 → 进行中 → 待验收 → 已完成 / 已取消

## 当前进度

| 项 | 值 |
|---|---|
| 当前批次 | D1 文档极简重构 |
| 当前状态 | 进行中 |
| 上一批完成 | 无 |
| 阻塞项 | 无 |

## 批次

| ID | 目标 | 依据 | 范围 | 不做 | 验收 | 状态 | 执行者 | commit |
|---|---|---|---|---|---|---|---|---|
| D1 | 文档极简重构 | AGENTS.md、Diátaxis、ADR | 根目录与 docs 下的文档、STANDARDS 登记表 | 改代码 | 文档门禁绿、同一主题仅一处、所有链接可解析 | 进行中 | CodeBuddy | — |
| B0 | 现状基线清单 | ISO/IEC 25010 可维护性 | 只读审计仓库与文档 | 改代码 | 清单交付 | 待办 | — | — |
| B1 | 审计认 X-Identity | OWASP ASVS V7 | `server/app/core/audit.py` 与测试 | 不改认证通道 | 管理操作审计可追溯 | 待办 | — | — |
| B2 | 生产迁移机制 | Alembic 官方文档 | 顶层 `compose.yaml`、RUNBOOK | 改 schema | 空库可 `alembic upgrade head` | 待办 | — | — |
| B3 | 网页端认证页 | RFC 6750、OWASP ASVS V2/V3 | `server/web/`、`server/app/auth/` | 删 X-Identity 兼容 | 认证与会话可用 | 待办 | — | — |

## 变更日志

- 2026-11-09：建立 WORKPLAN；重写 AGENTS.md 为唯一必读入口。D1 进行中。
