# ntu-shower 参考手册

数据层的**判定规则、口径与不变量**——只写从代码里读不出来的那一层。

| 想知道 | 看哪里 |
|---|---|
| 字段、类型、约束、逐字段注释 | `server/app/models/` |
| HTTP 接口与请求/响应形状 | 运行中的 `/openapi.json`（FastAPI 生成） |
| 协议消息结构 | `protocol/schema/*.json` |
| 系统边界、时间轴定义 | [`architecture.md`](architecture.md) 的「时间轴与口径」 |
| 决策理由与备选方案 | [`adr/`](adr/) |
| 部署、回滚、故障处置 | [`runbook.md`](runbook.md) |

**本文不复制字段表和端点表**：那两份由代码生成或直接可读，复制一份就多一份漂移。

## 喷头状态：七个取值

由设备报的**四个原始整数**派生。判定的唯一实现是 `app/modules/doors/service.py` 的
`derive_state`——判定顺序、占用判据、对任意输入的满射性，以及「报修/停用排在占用之前」
的原因，都在该模块注释里，此处不复述。

**两个集合**的唯一实现是 `app/models/enums.py`（`OCCUPIED_STATES` / `ASSIGNABLE_STATES`）。
可分配一组**正着列**（列出谁是可分配的）而不是反着列排除项：新增状态默认落到不可分配一侧，
这是枚举注释没有说的那条理由。

中文名的唯一数据源是 `app/modules/doors/schemas.py` 的 `_STATE_LABELS`，与小程序展示文案一致。

## 易混字段对照

| 容易混 | 区别 | 混了会怎样 |
|---|---|---|
| `captured_at` / `device_clock` / `received_at` | 服务端轴的采集时刻 / 设备墙钟原话 / 服务端收到的时刻 | 拿 `device_clock` 当时刻用，就把可拨的钟当成了事实 |
| `open_time` / `in_time` / `close_time` | 分配 / 开阀 / 关阀；都在服务端轴 | 拿 `in_time` 当会话起点 → 会话时长整体偏小 |
| `assigned_at` / `opened_at` | **会话**起点 / **段**起点 | 统计占用时用了段，零出水的会话全丢 |
| `last_seen_at` / `ended_at` | 最后确认占用 / 观测到结束 | 前者才是可信下界；后者跨盲区时不是真实时刻 |
| `assigned_source` / `observed_span` / `source` / `segments_complete` | 四个**不同维度**的可信度：起点怎么来的 / 本项目看没看见 / 段长证据强度 / 段记全没有 | 当一件事用，会把"下界"和"盲区"和"截断"混成一个"不可信" |
| `usage_sessions.flow_s` / `usage_segments.flow_s` | 段之和 / 那一段的长度 | 同名列不同层级 |
| `flow_s` / `device_flow_s` | 本项目算的 / 设备报的 | 两者不等 = 有段没进表；拿二者相等当"完整"是循环论证 |
| `bath_time_s` / `device_flow_s` | 现态里的瞬时值 / 会话上的最大值 | `bath_time_s` 不单调，不能拿去减 |
| `opened_at` / `opened_raw` | 服务端轴 / 设备轴 | 对外只能出服务端轴；原文只用于复核 |
| `status_raw` / `state` | 设备报的原始整数 / 本项目派生的七个取值 | 直接判整数，新增状态时静默走错分支 |
| `card_no` / `cert_no` | 卡 / 人 | 本机无卡，卡号恒 0；拿它当人等于没判 |
| `device.clock_skew_s` / `door_snapshots.device_clock` | 持久化的偏差 / 那一刻的墙钟读数 | 偏差随时间变，只能从后者算出来 |
| `revision` / `version_code` | 配置修订号 / 设备端版本号 | 两个"版本"毫不相干 |
| `last_used_at` / `last_used_s` / `last_used_cert`（`DoorView` 视图字段，不是表列） | 上次**开阀**时刻 / 那次的出水秒数 / 那次是谁用的 | 只在喷头**没有进行中的会话**时才有展示意义；判据是**有没有过段**——"分配后直接结算"那种从没出水的会话说不出任何一段 |

## 不变量

1. **一次会话的总长从分配时刻起算，≤ 1200 秒**（= 设备的 `maxbatchtime`；这个值 2026-05 前是 1800）。
2. **任何一次连续无水 ≤ 300 秒**——从未开阀、或中途关阀，两条各管一段。
3. **盲区 > 1200 秒必须切开会话**，不能当连续。
4. **`observed_span` 只增不减**。
5. **`flow_s ≠ device_flow_s` 即有段没进表**。
6. **`segments_complete` 在有 `derived` 段时恒为假**。
7. **对外只出服务端轴**。
8. **一个喷头同一时刻只有一个占用者**（换人先结旧账再开新账）。
9. **喷头状态中文名只有一个数据源**：`app/modules/doors/schemas.py` 的 `_STATE_LABELS`。

> 第 1 与第 2 条是**浴室 App 的规则**（`DoorStatusConfirmThread` 每秒轮询、四条超时判定、
> 同一句清零 SQL），不是本项目的算法。它们与 `SESSION_MAX_S` 的关系见
> `app/modules/doors/service.py` 的常量注释。

## 注意事项

### 时间

- 偏差在**两次校时之间被沿用**，所以设备钟在那期间被改过、而平移值恰好落进三条自洽检验的缝隙里时，起点会偏——2026-10-02 实测过一次（偏了整整一小时）。

### 数据

- **盲区不等于故障**：它只说"本项目没看见"。
- **段长来源强度不同**（`app/models/enums.py` 的 `SegmentSource`：measured / observed / capped / derived），统计必须分开看。
- **截断过的段（`capped`）不等于"真的洗了那么久"**。
- **结束原因不可判**，别写"这是超时释放/主动出门"（"算不出来的三样"的完整清单在
  `app/modules/doors/service.py` 模块注释里）。
- **DDL 只走 Alembic**：应用不建表，库结构变更经迁移（compose 的 migrate 服务先于应用
  执行 `upgrade head`）。本地库报"没有某一列"是它落后了，跑 `alembic upgrade head`；
  流程见 [`runbook.md`](runbook.md) 的「数据库迁移」。

### 接口

- 推送是尽力而为（队列满即丢），**现态以 REST 为准**。
- 命令是**同步**的：服务端等到设备答复或超时。时限是环境值（`assign_timeout_s` /
  `settle_timeout_s` / `config_ack_timeout_s`，默认值与语义见 `app/core/config.py`）。
- 设备声明的能力集决定能下发什么：**声明了、但不含这个动作**就拒发，而不是等它回 unknown_action；**空能力集不据此拦截**（那表示还没升级过的老设备，拦了会把在网设备锁死——见 `app/modules/sessions/service.py` 里那一处的说明）。
- 同通道的命令排队执行，**队满才拒**（那说明积压已多，该换个时间再来），与"设备正忙"不是一回事。

### 契约

`protocol/schema/*.json` 是协议的唯一源（含中文 `description`）。Kotlin 与 JavaScript 都不能
import Python，所以另两端拿到的是**生成产物**，由 `server/tools/sync_contracts.py` 生成、
`test_contract_sync.py` 断言逐字节相同。**改契约就是改 schema 里的源**，不要手改生成物。

`/stream` 的消息类型唯一出处是 `app/core/events.py`，`server/web/core/contract.js` 由同一脚本生成。
