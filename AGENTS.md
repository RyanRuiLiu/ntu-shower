# AGENTS.md — 给 AI 协作者的入口

一屏读完再动手。细节按需点开，不要通读全部文档。

## 这是什么

寄生在浴室终端上的控制系统。终端跑着**原生的浴室 APP**（`com.datalook.bath`），
我们**读它的数据**（只读 `t_doorlist` 一张表）并**通过无障碍服务操作它的界面**。
服务端汇聚多台终端、提供 API 与后台。

## 改代码前必读（只有三份）

| 你要做的事 | 读哪份 |
|---|---|
| 任何改动 | `docs/STANDARDS.md` —— 命名、日志、消息语义、质量门 |
| 碰协议 / 双端字段 / 加消息 | `docs/PROTOCOL.md` + `protocol/schema/` |
| 加配置键 / 改 API / 加模块 | `CONTRIBUTING.md` §4「常见任务」——任务到文件的直接映射 |

`docs/DECISIONS.md` 只在需要知道"为什么这样决定"时查索引，**不要通读 36 条 ADR**。

## 门禁（不绿不算完成）

```bash
cd server
ruff format . && ruff check . --fix && mypy app --strict
pytest -q --cov=app --cov-report=term-missing     # 覆盖率门 96%，当前 97%

cd ../android
./gradlew detekt :app:testDebugUnitTest :app:jacocoUnitTestCoverageVerification
```

## 硬约束

- **不写兼容/回退/迁移代码**。开发阶段，数据库直接删了重建；schema 只有最初版本。
- **不留无用物**，但"当前无人读取"≠无用：**历史数据删了就回不来**，
  统计口径的数据（`usage_session`/`door_state_event`/`command_audit`）必须先攒。
- **一改一验**。改完立刻跑对应端门禁，不要攒一批再验。
- **命令超过 10 秒**说明有问题（测试有 5 秒单例超时，超了就是它在等一个永不到来的事件）。
- 服务端与设备端**对称**：改一侧必查另一侧（键集合由 `test_config_spec.py` 对账）。

## 现状（改之前先对齐）

- 版本 **0.3.6**，三处版本号由测试校验一致性
- 权限：`X-Identity` 标识 + 能力位（`ops` 分配结算 / `photo` 拍照）；
  小程序 `wx.login` → openid，网页用管理员签发的标识；管理面另用 `X-Admin-Token`
- 契约：35/35 端点有显式响应类型，44 个组件 schema
- 生产：`root@iseekdream.com`，服务 `ntu-shower`（旧系统 `ntu-remote-shower` 是保底，未下线，**用独立的库，不要动**）
