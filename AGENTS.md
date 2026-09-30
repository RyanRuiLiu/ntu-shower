# AGENTS.md

寄生在浴室终端上的控制系统。终端跑着原生的浴室 APP（`com.datalook.bath`），
我们**只读**它的数据（`t_doorlist` 一张表）并**通过无障碍服务操作它的界面**。
服务端汇聚多台终端，提供 API 与网页端。

## 知识在哪

**规范与要点都在代码注释里，本文档不复述。** 这个项目由 AI 开发：注释不会漂移
（改代码必然看到），而文档会被读一半、会过期、会互相矛盾。

- 能力、执行通路、命名规则 → `server/app/core/actions.py`
- 失败原因码 → `server/app/core/reason_codes.py`
- HTTP 问题类型 → `server/app/core/problems.py`
- 配置键的类型与范围 → `server/app/modules/config/spec.py`
- 协议消息格式 → `protocol/schema/*.json`（含中文 description）
- 数据库命名与时间格式 → `server/app/core/db.py`
- 精简与数据流 → `docs/ARCHITECTURE.md`
- 部署与故障处置 → `docs/RUNBOOK.md`
- 当时怎么想的 → `docs/DECISIONS.md`

**要把一件事写下来时，先问它能不能进代码注释。** 仓库文档只有上面那几份，
**能删就删**——每一份都是未来的漂移来源。

## 禁令

- **禁止「兜底 / 降级 / 兼容垫层 / 回退」**。它们描述的是**一种设计**：假定主方案
  不完整，再补一个次等的。本项目的取向是每条通路各自完整、按场景选。用词会固化
  思维——写成"兜底方案"的代码，往往真的只做到次等水准。
  由 `server/tests/test_language_rules.py` 强制。
- **不写兼容 / 回退 / 迁移代码**。开发阶段，数据库直接删了重建，schema 只有最初版本。
- **不留无用物**，但「当前无人读取」≠无用：历史数据删了就回不来，统计口径的数据
  （`usage_session` / `door_state_event` / `command_audit`）必须先攒。
- **一改一验**。改完立刻跑门禁，不攒一批再验。
- **命令超过 10 秒说明有问题**；测试有 5 秒单例超时，超了就是它在等一个永不到来的事件。

## 门禁

```bash
cd server
ruff format . && ruff check . --fix && mypy app --strict
pytest -q --cov=app --cov-report=term-missing

cd ../android
./gradlew detekt :app:testDebugUnitTest :app:jacocoUnitTestCoverageVerification
```

覆盖率与测试数由 CI 实时产出，**不写进任何文档**——写死的数字必然过期。

## 生产

`root@iseekdream.com`，服务 `ntu-shower`。
旧系统 `ntu-remote-shower` 未下线，**用独立的库，不要动**。
`deploy/` 是 nginx / systemd / backup 配置的唯一事实来源。
