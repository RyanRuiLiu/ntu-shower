# ntu-shower 参考手册

写给接手的人：**每个字段是什么、这个数怎么算出来的、状态怎么变**。

分工（别在这份里找它们的内容）：

| 文档 | 管什么 |
|---|---|
| `AGENTS.md` | 知识在哪、跨项目边界、禁令 |
| `docs/ARCHITECTURE.md` | 精简与数据流 |
| `docs/DECISIONS.md` | 约束代码的结论与理由 |
| `docs/RUNBOOK.md` | 部署与故障处置 |
| **本文** | **数据模型与接口：字段语义、派生规则、状态变更、取数口径** |
| `server/app/core/terms.py` | 术语的唯一正名与弃用词（本文用它的词） |

---

## 一、全景

```
浴室 App（com.datalook.bath）
   │  只读它的 t_doorlist 一张表 + 用无障碍服务操作它的界面
   ▼
设备（Android 4.4.4，跑着 NTU Shower）
   │  WSS /gateway/device —— 设备报帧，服务端下发命令
   ▼
服务端（本仓库）
   │  原始层落库 → 派生层算出会话与段
   ├─ HTTP /api/v1/*        给管理台与网页端
   └─ WSS /stream           给消费端（网页端与小程序）实时推送
        ▼
消费端（网页端、status-panel 小程序）
```

**表分三层，做功能与统计只用派生层**：

| 层 | 表 | 用途 |
|---|---|---|
| **原始层** | `door_snapshot`、`device_link_event`、`command_audit`、`api_call` | 设备与调用方的原话，不改写。**它是重算的退路，不是查询入口** |
| **派生层** | `door_live`、`door_state_event`、`usage_segment`、`usage_session` | 我们算出来的。**做功能与统计只碰这四张** |
| **支撑层** | `device`、`global_config`、`device_config`、`access_identity`、`ota_release`、`ota_release_install` | 设备档案、配置、身份、发布 |

原始层为什么必须在：派生规则一旦发现缺陷、或将来要加一个现在想不到的指标，没有原始帧就永久无法重算。旧系统只留了原始帧，那 100 天今天才能重放。

---

## 二、两条时间轴

**这是全项目最容易栽的地方。**

| 轴 | 形态 | 谁写的 | 可信度 |
|---|---|---|---|
| **设备轴** | RFC 3339，但**读数来自设备那口钟** | 设备 | **不可信**：实测自然漂移 8.5 秒/天，且被人为回拨过一小时 |
| **服务端轴** | RFC 3339 UTC 毫秒 | 服务端（NTP 同步） | 可信。**对外只出这一条轴** |

两条轴**格式相同、数值不同**，所以最省事的判据是看字段：帧里的 `open_time`/`in_time`/`close_time`/`device_clock` 是设备轴，`captured_at`/`received_at` 是服务端轴。实测一帧：`captured_at` 是 `21:57:03.244`，而同一帧的 `device_clock` 是 `21:56:56.763`——差 6.48 秒，正是那口钟落后服务端的量。

设备轴的原话只留在原始帧里；`door_live`、`usage_segment`、`usage_session` 的时刻字段**全部在服务端轴上**。

**桥是偏差**（`device.clock_skew_s`，符号约定 `服务端 − 设备`）：

- 来源两条，取的都是一对"设备墙钟读数 / 服务端此刻"：**帧载荷里的 `device_clock`（与 `captured_at` 同刻）是主力**——两个读数只隔一个调用栈，精度是毫秒级；**`link.pong` 的 `device_time` 是副的**——两个读数隔一个网络往返、精度是秒级，它负责在设备空闲（不上报业务帧）时把已经稳住的偏差维持住。多个样本走 EMA（`core/device_clock.py`）。
- **必须持久化**：只留内存的话，服务重启后回到 0 等于这段时间不平移，而"我们刚接手、那边还被占着"恰恰最常发生在设备上线到首次 pong 之间。
- 偏差**随时间变**，所以原始帧里必须同时留 `captured_at`（服务端轴）与 `device_clock`（设备墙钟读数）——事后要重算任何一条换算，都得先有"当时那口钟读数是多少"。
- 平移只在**一次会话的窗口内**成立：会话压在 20 分钟以内、而 20 分钟里的钟漂移只有 0.03 秒。

**一次会话里的六个时刻**（都在服务端轴上）：

| 正名 | 标识符 | 含义 |
|---|---|---|
| **分配时刻** | `usage_session.assigned_at` | 喷头被分配给这个人的时刻 |
| 首次观测时刻 | 同上（`assigned_source = observed` 时） | 拿不到设备原文时退回的观测值，**只是下界，真实更早** |
| **开阀时刻** | `usage_segment.opened_at` | 这一段开始出水 |
| **关阀时刻** | `usage_segment.closed_at` | 这一段停止出水；**空 = 尚未关阀** |
| **释放时刻** | `usage_session.ended_at` | 观测到这次占用结束；**空 = 仍在进行** |
| **最后确认时刻** | `usage_session.last_seen_at` | 最后一次确认它还占着；**释放时刻 − 它 = 盲区** |

---

## 三、原始层

### 3.1 `door_snapshot` —— 一帧

设备报了什么都原样记，不做任何解释。重复投递由 `snapshot_id` 的唯一索引挡掉（设备在收到 ack 前会重发），落库原地不动、派生也随之跳过。

| 字段 | 是什么 |
|---|---|
| `device_id` | 哪台设备 |
| `snapshot_id` | 设备给的帧标识，**唯一索引**——重发的那一帧不会落第二遍 |
| `seq` | 发件箱自增序号，单调递增。**只在最旧的帧被裁掉时才跳号**，据此看得出中间丢了 N 条 |
| `source` | 这帧是怎么来的：`db_watch`（浴室 App 写库触发，状态真的变了）／`startup`（重连或开机后推的全貌）／`import`（从旧系统历史重放） |
| `captured_at` | **采集时刻（服务端轴）**，由设备锚在服务端时间上。它是本帧的权威时刻：会话的起点、终点与淋浴时长都以它为准。设备刚起来还没握上手时为空，此时退回 `received_at` |
| `device_clock` | 采集那一刻**设备墙钟**的读数，与 `captured_at` 取自同一瞬间。两者相减就是偏差 |
| `received_at` | 服务端收到它的时刻 |
| `doors` | 该帧里每个喷头的读数（JSON 数组），字段见协议 `protocol/schema/door.snapshot.json` |

### 3.2 `device_link_event` —— 上下线

帧是**事件驱动**的（状态变化才上报），空闲时不上报。所以"没消息"既可能是"没人用"也可能是"设备离线"——两者在数据里长得一样、事后无法区分。这张表把它分开，同时给出设备利用率的分母。

| 字段 | 是什么 |
|---|---|
| `device_id` | 哪台设备 |
| `occurred_at` | 服务端轴 |
| `event` | `online` ／ `offline`（`modules/devices/schemas.py` 的 `LINK_ONLINE` / `LINK_OFFLINE`） |

**只记"连接建立/断开"这个事实**：为什么断（网络、重启、被踢）不在其中，那要结合断线前的日志看。

### 3.3 `command_audit` —— 我们下发的每条命令

| 字段 | 是什么 |
|---|---|
| `command_id` | 这条命令的标识，与设备端痕迹同一份 |
| `device_id` | 下发给谁 |
| `action` | 动作名，清单的唯一出处是 `app/core/actions.py` |
| `params` | 参数原文（JSON） |
| `created_at` / `finished_at` | 下发时刻 / 拿到结果的时刻；**空 = 还没有结论** |
| `status` | `ok`（做完了）／`failed`（动手了但没成）／`timeout`（超过时限，没有结论）／`rejected`（前置条件不满足，未产生副作用，重试安全）。取值是跨端契约，见 `app/core/outcomes.py` |
| `reason_code` | 失败原因码，登记表见 `app/core/reason_codes.py` |
| `terminal_message` | 给人看的一句话。失败时服务端用 `reason_codes.describe()` 补齐，**保证非空**；成功时设备有值得转达的内容才填（如浴室 App 界面原文）。**分支判定用 `status` 与 `reason_code`，不用它** |
| `trace_id` | 与 `api_call` 关联，查两边即得全链路 |
| `data` | 结构化结果（如截图的 base64、exec 的输出） |

> **`status = ok` 不等于动作做成了。** 设备端把那一轮 UI 流程走完就报 `ok`，而**浴室端
> 拒绝时它照样报 `ok`**——密码错落在提示页上，那一页在设备端被当作正常终态
> （`AutomationFlow.awaitTerminal` 的提示页分支，见 `CommandScheduler.execute` 里
> `error == null` 那条出口），被拒的话只进 `terminal_message`。而 `terminal_message` 是
> 给人看的中文，`outcomes.py` 明写不拿它做分支。所以判"这次到底成没成"只能看**后续帧**：
> 喷头有没有变成「等待开阀」、占用者是不是他、那一行有没有被清零。

### 3.4 `api_call` —— 每一次 HTTP 调用与每次网关连接

| 字段 | 是什么 |
|---|---|
| `kind` | `http`（普通 HTTP 请求）／`gateway`（设备 WS 连接的建立与断开） |
| `requested_at` | 服务端轴 |
| `request_id` / `trace_id` | 请求标识 / 链路标识（有合法 `traceparent` 就沿用它的 trace-id，否则生成） |
| `actor_kind` | `identity`（带了 `X-Identity`）／`anonymous`／`device`（网关） |
| `actor` | 身份原文，**不脱敏**；匿名时为空串 |
| `client_ip` / `user_agent` | 来源 IP（经 nginx 反向代理而来，真实地址在 `X-Real-IP`）／UA |
| `method` / `path` / `query` | 请求行 |
| `request_body` / `response_body` | JSON 体原文。**全有或全无，绝不截断**：截断了后续就查不回真实内容，超限时占位说明。数据重型且可重取的三个端点（`capture`／`dump`／`photo`）不落响应体 |
| `status_code` | HTTP 状态码。**`kind = gateway` 时它是一个约定**：0 = 连接，1 = 断开 |
| `duration_ms` | 耗时；网关那种行为 0 |
| `device_id` | 相关设备（可为空） |
| `problem_type` | RFC 9457 的问题类型；登记表见 `app/core/problems.py` |
| `detail` | 失败响应的中文说明 |

---

## 四、派生层

### 4.1 喷头状态：七个取值

由设备报的**四个原始整数**派生（`doors/service.py:derive_state`）。判定顺序即优先级：

```
status == 1（RAW_STATUS_REPAIR）   → 维修中
status == 3（RAW_STATUS_DISABLED） → 已禁用

占用 = (card_no != 0) 或 (acc_no 不在 {"", "0"})

占用时：
    in_time 非空                     → 淋浴中
    bath_time_s > 0 且 close_time 非空 → 已暂停
    否则                             → 等待开阀
不占用时：
    status == 2（RAW_STATUS_SELECTED）→ 已选中
    否则                             → 空闲
```

**两个集合**（`doors/schemas.py`）：

- **占用** = {等待开阀, 淋浴中, 已暂停} —— 有人
- **可分配** = {空闲, 已选中} —— 正着列可分配的状态而不是反着列：将来新增状态默认落到不可分配一侧

中文名与 `status-panel` 小程序的展示文案一致，不许另起（见 `terms.py`）。

### 4.2 `door_live` —— 这个喷头现在什么状况

一行一个喷头（`PRIMARY KEY (device_id, door_no)`）。

| 字段 | 是什么 |
|---|---|
| `state` | 上面派生出的七个取值之一 |
| `card_no` / `cert_no` / `acc_no` | 占用者标识。**本机无卡，`card_no` 实测 235270 帧无一非零**——"谁"只能看 `cert_no`（为空时退回 `acc_no`） |
| `open_time` | **分配时刻（服务端轴）**。空闲时为空串 |
| `in_time` | **当前这一段的开阀时刻（服务端轴）**。只有正在出水时有值 |
| `close_time` | **当前这一段的关阀时刻（服务端轴）**。只有已暂停时有值 |
| `bath_time_s` | 设备累计的出水秒数。**它是时长不是时刻，不在任何一条时间轴上**；且**不单调**——只在关阀那一刻累加、释放时整体归零 |
| `status_raw` | 设备报的原始 `status` 整数。**别拿它判状态**，用 `state`；新增状态时直接判整数会静默走错分支 |
| `reported_at` | 这一行是据哪一帧写的（服务端轴） |

三个时间字段存的是**服务端轴**的值，换算在写入前完成——设备那口钟的读数只在原始帧里保真。它们让"这个门现在什么状况"一次查询就能渲染；对应的会话与段由 `usage_session` / `usage_segment` 回答。

### 4.3 `door_state_event` —— 每一次状态转换

只在状态**真的变了**时写一行。两种帧不记：**首帧**（没有"从哪来"）与**全貌帧**（`source = startup`，它只刷现态、不派生，见 `SOURCE_STARTUP`）。

| 字段 | 是什么 |
|---|---|
| `occurred_at` | 转换时刻（服务端轴） |
| `device_id` / `door_no` | 哪个喷头 |
| `from_state` / `to_state` | 七个取值中的两个 |
| `card_no` / `cert_no` | 那一刻的占用者。**提成独立列而不是塞进 JSON**：统计要按人筛，JSON 走不了索引 |
| `bath_time_s` | 那一刻设备报的出水进度，诊断用 |

它是对账与诊断的主力：**暂停次数**数 `bathing→paused`，`bathing→free` 与 `paused→free` 含义不同。

> **分辨率的下限**：帧是秒级采样的，"两次写库落在同一帧里"就看不出中间态。要精确的逐次统计，看 `usage_segment`（一次出水一行）而不是事件表。

### 4.4 `usage_segment` —— 一次连续出水

一段 = 一个会话里从开阀到关阀。一个会话含 0 到 n 段（0 段 = 分配了没出水）。

| 字段 | 是什么 |
|---|---|
| `session_id` | 属于哪次会话 |
| `device_id` / `door_no` | 冗余一份门号与设备号，为了按门筛段时免联表（刻意的例外，写明白了） |
| `opened_at` / `closed_at` | 开阀 / 关阀时刻，**服务端轴**。`closed_at` 为空 = 尚未关阀 |
| `flow_s` | **这一段的长度**。注意同名列在 `usage_session` 上是**另一个量**（那个是段之和） |
| `opened_raw` / `closed_raw` | 设备写下的两个读数**原文**（与 `open_time` 同格式：设备钟上的 RFC 3339，例如 `2026-10-03T17:54:06.000Z`——服务端轴上它是 `16:54:15`）。段长取"同一口钟两个读数之差"，没有原文就无法复核、也无法事后重算 |
| `source` | 段长的证据强度，五档，见下 |

**五种来源**（强度不同，统计必须分得清）：

| `source` | 怎么来的 |
|---|---|
| `measured` | 设备轴差 `close_time − in_time` 与观测差一致（相差 ≤ `AB_TOLERANCE_S` = 3 秒）→ 采信设备轴差。**同一口钟两个读数相减，不经换算，是最强的一档**。另有一条路径：段起点就是 `in_time` 平移来的时，直接采信设备轴差 |
| `observed` | 只信观测差（本帧 − 段起点）。**含"我们看见关阀"的那点延迟**（帧间隔量级，1 秒上下） |
| `capped` | 观测差越过一次出水的物理上限（`MAX_SEGMENT_S` = 1200）→ 截断成 1200。**截断过的段与"它真的洗了那么久"必须分得开**，否则统计会把"我们不知道有多长"当成"就那么长" |
| `derived` | 从设备留下的痕迹推回：上线时门已在暂停态，`in_time` 被清空，但设备留下了关阀时刻与累计出水秒数——从前者往前数那么多秒就是那一段的开阀时刻。**只在这是全场第一次暂停时才成立**（之后 `bath_time_s` 是多段之和，退出来会落在会话起点之前或本帧之后，区间检查会丢掉它） |
| `unclosed` | 数据到此为止仍在出水，长度不可知。**只有重放历史那条路会写它**（`server/tools/replay_history.py`）；线上在途段的 `source` 是空串、`flow_s` 是 0、`closed_at` 为空 |

两个**最贵的错误**在这条路上（都不报错，只是数字变成 0，还会被标成最强的 `measured`）：

1. `close_time` 缺失时用 `str(None)` 得到字符串 `"None"`——非空，于是"没有关阀时刻"被当成有值，两读数之差算成 0。所以只在它**真的是字符串**时才当读数。
2. 两个不同读数的差被 `seconds_between` 归零（关阀早于开阀，段内设备钟被往回拨过）。**两个不同的读数不可能差 0 秒**，据此把归零值当"没有设备轴差"退回观测差。

### 4.5 `usage_session` —— 一次占用

一行 = 一个喷头从被分配到被释放。

| 字段 | 是什么 |
|---|---|
| `assigned_at` | **分配时刻**（服务端轴） |
| `assigned_source` | **起点来源**：`inferred`（由设备报的 `open_time` 平移而来——那是真实的分配时刻）／`observed`（退回本帧时刻，**真实起点更早，这是下界**）。`inferred` 的跨度可以用，`observed` 的天然偏小 |
| `observed_span` | **观测连续性**：`continuous` 我们一路看着／`gap` 中间有超过一次占用上限的空档。**只描述我们看没看见，不描述发生过什么**；且**单向只增不减**——断开一次之后，后面接上的连续观测补不回失去的那一段 |
| `last_seen_at` | **最后一次确认它还占着**的那一帧。它才是可信的结束下界——`ended_at` 与它的差就是观测盲区 |
| `ended_at` | 观测到它不再占用的那一帧。**跨盲区时它不是真实结束时刻**，只是"下次见到它的时刻" |
| `flow_s` | **段之和**（我们算的淋浴时长）。能从段表推出来，存一份是为了让统计有个不联表的入口 |
| `device_flow_s` | **设备累计出水秒数**（`bath_time_s` 的最大值）。它是设备说的话，用来与段之和对照：**两者不等就说明中间有段没进表**（丢帧、离线、设备卡住） |
| `segments_complete` | 段之和与设备那个值**是否相等**，结束时定稿。**有 `derived` 段时恒为假**——那段的长度就是从设备那个值推出来的，等式必然成立，拿它自证是循环论证 |
| `card_no` / `cert_no` | 占用者。换人判据用 `cert_no` 而不是 `card_no`（无卡设备上卡号恒 0，拿它比等于没比） |

**起点怎么来的**（`_assigned_at`）：优先把设备报的 `open_time` 加上偏差平移——那是分配那一刻设备写下的，而它在整场会话里不变（暂停与恢复都不改它，只有释放才整体清零），所以无论中间暂停过几次都还是同一个值。

平移值要过**三条自洽检验**，任一不成立就退回观测下界：

1. 起点不晚于本帧——分配不可能发生在我们看见它之后；
2. 起点不早于**上次看见这个喷头**的时刻——更早的会话我们已经见过了；
3. 与同帧 `in_time` 的平移值相差不超过 1200 秒。

第 3 条是**唯一能识破"拨钟时我们离线"**的：那时第 2 条没有参照（前一帧可能远在几小时前、或根本不存在）。2026-10-02 实测：断连期间有人分配，重连前设备钟被拨快一小时，算出来的起点比开阀还早一小时。

> **不要**退回同帧的 `in_time`：两个读数出自同一口钟，钟被改过时它们一起偏；而"本帧时刻"来自服务端钟，是这里唯一不依赖设备钟的锚。

**会话怎么开、怎么续、怎么关**（每帧过一遍 `_maintain_session`）：

```
gap = 上一帧到本帧的秒数（没有上一帧时 -1）

① 先看盲区：gap > 1200 且存在上一帧
   → 把该门未结束的会话在「上一帧的时刻」收尾，并标 observed_span = gap
   （否则库里的陈旧占用会被读成"它还被占着"，那场会话永不结束、起点落在几小时前）

② 本帧状态不在占用集
   → 若上一帧在占用集，关掉该门未结束的会话
   → 返回
   （这一判据让绝大多数帧省掉一次查询；等价性靠"每帧都处理"保证——
     会话不可能在两次观测之间被开出来又没人结，开它的那一帧就会把会话写下）

③ 该门有未结束的会话吗
   没有          → 开会话，起点由 _assigned_at 给出
   有但换了人     → 先关旧的，再开新的（新账的起点同样能平移）
   否则          → 用它

④ 维护段（见 4.4 的开/关/推回三条分支）

⑤ 推进会话：flow_s = 段之和 + 在途段到此刻（上界 1200）
            device_flow_s = MAX(旧值, 本帧 bath_time_s)
            seen = true，last_seen_at = 本帧
```

**关会话**（`_close`）：先把在途段收干净 → 累加 `flow_s` → `touch_session(seen=False)` → `close_session(ended_at, segments_complete)`。

两处刻意的设计：**`last_seen_at` 在关会话时不动**（它记的是最后一次确认还占着，而这一帧恰恰证明它不占了；两者之差就是盲区大小）；**不记结束原因**——浴室 App 在"用户主动释放"与四种超时释放下写的是同一套清零语句，数据库层面完全同形，服务端没有依据区分，硬分就是编造。

### 4.6 易混字段对照

| 容易混 | 区别 | 混了会怎样 |
|---|---|---|
| `captured_at` / `device_clock` / `received_at` | 服务端轴的采集时刻 / 设备墙钟原话 / 服务端收到的时刻 | 拿 `device_clock` 当时刻用，就把可拨的钟当成了事实 |
| `open_time` / `in_time` / `close_time` | 分配 / 开阀 / 关阀；都在服务端轴 | 拿 `in_time` 当会话起点 → 会话时长整体偏小 |
| `assigned_at` / `opened_at` | **会话**起点 / **段**起点 | 统计占用时用了段，零出水的会话全丢 |
| `last_seen_at` / `ended_at` | 最后确认占用 / 观测到结束 | 前者才是可信下界；后者跨盲区时不是真实时刻 |
| `assigned_source` / `observed_span` / `source` / `segments_complete` | 四个**不同维度**的可信度：起点怎么来的 / 我们看没看见 / 段长证据强度 / 段记全没有 | 当一件事用，会把"下界"和"盲区"和"截断"混成一个"不可信" |
| `usage_session.flow_s` / `usage_segment.flow_s` | 段之和 / 那一段的长度 | 同名列不同层级 |
| `flow_s` / `device_flow_s` | 我们算的 / 设备说的 | 两者不等 = 有段没进表；拿二者相等当"完整"是循环论证 |
| `bath_time_s` / `device_flow_s` | 现态里的瞬时值 / 会话上的最大值 | `bath_time_s` 不单调，不能拿去减 |
| `opened_at` / `opened_raw` | 服务端轴 / 设备轴 | 对外只能出服务端轴；原文只用于复核 |
| `status_raw` / `state` | 设备报的原始整数 / 我们派生的七个取值 | 直接判整数，新增状态时静默走错分支 |
| `card_no` / `cert_no` | 卡 / 人 | 本机无卡，卡号恒 0；拿它当人等于没判 |
| `device.clock_skew_s` / `door_snapshot.device_clock` | 持久化的偏差 / 那一刻的墙钟读数 | 偏差随时间变，只能从后者算出来 |
| `revision` / `version_code` | 配置修订号 / 设备端版本号 | 两个"版本"毫不相干 |
| `last_used_at` / `last_used_s` / `last_used_cert`（`DoorView` 视图字段，不是表列） | 上次**开阀**时刻 / 那次的出水秒数 / 那次是谁用的 | 只在喷头**没有进行中的会话**时才有展示意义；判据是**有没有过段**——"分配后直接结算"那种从没出水的会话说不出任何一段 |

### 4.7 取数口径

按指标选数据集，不是一刀切排除异常。**判据都能从列上算出来、不落列**：

| 数据集 | 判据 | 适用 |
|---|---|---|
| **全量** | 全部会话 | 出水类。`flow_s` 只累加有观测证据的段，从不高估 |
| **结束时刻可信** | `ended_at − last_seen_at` ≤ 1200 | 占用 / 等待 / 利用率 / 时段类 |
| **盲区** | 上式取反 | 采集断线率、可疑占用 |
| **超长占用** | `ended_at − assigned_at` > 1200 | 门被占着却一直不出水 |

后两个**不是一回事**：实测（5629 场）盲区那组 **0 场**，而超长占用有 **16 场**——它们的 `last_seen_at` 紧贴 `ended_at`（盲区都 ≤ 1200 秒），也就是每一帧都被看见了，只是喷头被占着却没有出水。**数字会随数据长，判据才是要点**（那两条判据直接从列上算，不落列）。

**不建汇总表**：明细按年计也就十万行量级，而汇总必然与明细漂移。

---

## 五、支撑层

### 5.1 `device` —— 设备档案

| 字段 | 是什么 |
|---|---|
| `device_id` | 主键 |
| `name` | 显示名，管理台可改 |
| `trusted` | 是否已信任。未信任的设备只收 `link.hello`，不下发命令 |
| `app_version` / `version_code` | 设备端版本。**`version_code` 就是 OTA 的安装确认** |
| `os_version` | 系统版本 |
| `capabilities` | 设备声明支持的动作（JSON 数组），由设备在 `link.hello` 里给 |
| `last_seen_at` / `last_ip` | 最后上线时刻（服务端轴）／来源 IP |
| `booted_at` | **开机时刻**（RFC 3339）：由设备上报的已运行秒数与服务端收到它的时刻反推。存时刻而不存秒数——秒数一离开采样瞬间就开始过期，开机时刻是不变的事实 |
| `clock_skew_s` | 偏差（见 §二） |

### 5.2 `global_config` / `device_config` —— 配置

`revision`（每次改递增）+ `config`（JSON）。

- `global_config` 只有一行（`CHECK (id = 1)`）。
- `device_config` 一行一台设备，键是 `device_id`。
- **存的是与默认值的差异，空对象即全部沿用默认**。存完整配置的做法会固化当时的默认值，新增键永远下不下去。
- 生效值每次现算：`默认 ← 全局 ← 设备`。
- 键的类型与范围唯一出处是 `app/modules/config/spec.py`；页面上的分组也由它给出。

### 5.3 `access_identity` —— 身份

| 字段 | 是什么 |
|---|---|
| `identity` | 主键，就是 `X-Identity` 头的值 |
| `kind` | `openid`（微信小程序用户，标识即其 openid）／`web`（网页用户，我方签发的不透明串） |
| `capabilities` | 能力位（能做什么），与 `kind`（这个标识从哪来）分开 |
| `device_scope` | **设备作用域**：`["*"]`（全部设备）或设备 id 列表，限定该身份的能力能作用于哪些设备。登记是全量写入，不传按 `["*"]` 落库 |
| `label` | 给人看的备注 |
| `created_at` | 签发时刻 |

访问控制是**统一身份制**：没有我们自己的用户体系、没有 JWT、也没有令牌端点。**浴室凭证明文存在会话维护的三份名单里**——每份名单是 `学号=密码` 一行一个（见 §8.6）。自动结算与自动续期要替学生调用浴室系统，而分配与结算都必须带密码，它没有别的落脚处；**只有被显式写进某份名单的学号才有这一条**，名单外的人我们对他什么都不做。管理台凭 admin 身份持有的能力位调用同一批路径。

### 5.4 `ota_release` / `ota_release_install`

| `ota_release` 字段 | 是什么 |
|---|---|
| `version` / `version_code` | 版本名 / 版本号（主键是 `version`） |
| `sha256` | APK 校验和，设备下载后校验 |
| `mandatory` | 随 `ota.upgrade_available` 一起下发的布尔标记。**设备端目前不读它**（`android/` 里搜不到该字段），现在是个预留位——别照着名字想当然 |
| `force` | 调试发布：无视版本号推送，**但已安装确认过即不再推** |
| `target` | 发布目标，`all` 或逗号分隔的定向列表 |
| `created_at` | 发布时刻 |

| `ota_release_install` 字段 | 是什么 |
|---|---|
| `version` + `device_id` | 联合主键 |
| `applied_at` | 安装确认时刻 |

安装状态在服务端跟踪：设备 `link.hello` 里的 `version_code` 即安装确认。已确认的 `force` 发布不再推；重推同一版本要先调 `POST /api/v1/ota/releases/{version}/rearm/{device_id}`，紧急下架用 `DELETE /api/v1/ota/releases/{version}`（连文件一起删——只删注册行会让 APK 变成无人认领的孤儿文件）。

---

## 六、不变量

1. **一次会话的总长从分配时刻起算，≤ 1200 秒**（= 设备的 `maxbatchtime`；这个值 2026-05 前是 1800）。
2. **任何一次连续无水 ≤ 300 秒**——从未开阀、或中途关阀，两条各管一段。
3. **盲区 > 1200 秒必须切开会话**，不能当连续。
4. **`observed_span` 只增不减**。
5. **`flow_s ≠ device_flow_s` 即有段没进表**。
6. **`segments_complete` 在有 `derived` 段时恒为假**。
7. **对外只出服务端轴**。
8. **一个喷头同一时刻只有一个占用者**（换人先结旧账再开新账）。
9. **喷头状态中文名与小程序的展示文案一致**，由 `terms.py` 强制。

> 第 1 与第 2 条是**浴室 App 的规则**（`DoorStatusConfirmThread` 每秒轮询、四条超时判定、同一句清零 SQL），不是我们的算法。它们与 `SESSION_MAX_S` 的关系见 `doors/service.py` 的常量注释。

---

## 七、对外接口

### 7.1 通用约定

- 基址 `https://<public-domain>`；管理面接口要 `X-Identity` 头。
- 失败响应是 RFC 9457 的问题对象，类型登记在 `app/core/problems.py`。
- 时间一律 RFC 3339 UTC；设备轴的原话不出现在任何响应里。

### 7.2 HTTP 端点

```
GET      /healthz                                    进程与版本

GET      /api/v1/doors                               喷头现态（公开）
GET      /api/v1/devices                             设备精简清单（公开）
GET      /api/v1/devices/runtime                     设备诊断详情（admin）
PATCH    /api/v1/devices/{device_id}                 改名等（admin）

POST     /api/v1/devices/{id}/assign                 分配喷头        session
POST     /api/v1/devices/{id}/settle                 结算            session
POST     /api/v1/devices/{id}/photo                  摄像拍照        photo
POST     /api/v1/devices/{id}/capture                全屏截图        admin
POST     /api/v1/devices/{id}/dump                   界面节点树      admin
POST     /api/v1/devices/{id}/touch|scroll|key|input 界面操作        admin
POST     /api/v1/devices/{id}/exec                   在设备上执行命令 admin
POST     /api/v1/devices/{id}/app/restart            重启本程序      admin
POST     /api/v1/devices/{id}/bath/restart           重启浴室 App    admin
POST     /api/v1/devices/{id}/reboot                 重启整机        admin
POST     /api/v1/devices/{id}/clock/set              修改设备端时间  admin
GET      /api/v1/devices/{id}/bridge                 调试通道状态    admin
POST     /api/v1/devices/{id}/bridge/open|close      开关调试通道    admin

GET,PUT  /api/v1/config                              全局配置        admin
GET      /api/v1/config/spec                          配置键规格      admin
GET,PUT,DELETE /api/v1/devices/{id}/config           设备级配置覆盖  admin

GET,POST /api/v1/identities                          身份清单/签发   admin
PUT,DELETE /api/v1/identities/{identity}             改/撤身份       admin

GET,POST /api/v1/ota/releases                        发布列表/发布   admin
DELETE   /api/v1/ota/releases/{version}              下架            admin
GET      /api/v1/ota/releases/{version}/download     下载 APK
POST     /api/v1/ota/releases/{version}/rearm/{device_id}  重推同一版本 admin
GET      /ota/manifest                               设备查更新

GET      /api/v1/audit/calls                         HTTP 审计       admin
GET      /api/v1/audit/commands                      命令审计        admin
```

### 7.3 WebSocket 三条

| 路径 | 谁连 | 用途 |
|---|---|---|
| `/gateway/device` | 设备 | 设备报帧、服务端下发命令。先 `link.hello` 声明能力，之后 `link.ping`／`link.pong` 保活并更新偏差 |
| `/gateway/bridge` | 服务端隧道 | 把设备的 ADB 端口转发出来，排障用 |
| `/stream` | 消费端 | 实时推送，两类消息见下 |

`/stream` 的消息类型（唯一出处 `app/core/events.py`，`server/web/core/contract.js` 由 `tools/sync_contracts.py` 生成）：

- **`door.update`** —— 该设备的**完整现态行**（与 `GET /doors` 同一路径产出）。WS 单连接有序投递，**数据承载推送**：客户端直接应用、不回拉。无变化的上报也照发，充当状态再同步。
- **`device.update`** —— 设备精简档案（`device_id`、`name`、`online`、`at`）。**版本、IP、时钟偏差等运维诊断字段不进公开推送**，管理台要详情走 `/devices/runtime`。

队列有界（256），满则丢弃——推送是尽力而为，现态可经 REST 兜取。

### 7.4 契约的唯一出处

`protocol/schema/*.json`（16 份，含中文 `description`）。Kotlin 与 JavaScript 都不能 import Python，所以另两端拿到的是**生成产物**，由 `server/tools/sync_contracts.py` 生成、`test_contract_sync.py` 断言逐字节相同。**改契约就是改那 16 份里的源**，不要手改生成物。

---

## 八、典型流程

### 8.1 一次分配（从 HTTP 到水出来）

```
管理台/小程序
  │ POST /api/v1/devices/{id}/assign  {door_no, cert_no, password}
  ▼
服务端三道预检
  ① 设备已信任且在线
  ② 喷头状态 ∈ 可分配 {空闲, 已选中}        ← 否则 409，附上它当前的状态原文
  ③ 该学号在这台设备上没有未结束的会话      ← 否则 409
  ▼
下发 session.command（action = session.assign）→ 设备
  ▼
设备端一轮完整 UI 操作（AutomationFlow）
  1 回浴室 App 首页            失败 → device.home_unreachable
  2 显示遮盖层（可配置关闭）
  3 选中该喷头 → 点「无卡模式」
  4 等验证页                   失败 → device.verify_page_timeout
  5 填学号 + 密码并确认         失败 → device.login_input_failed
  6 等浴室服务器终点：回首页=成功 / 提示页=被拒   失败 → device.bath_server_timeout
  7 收尾：等回首页 → 撤遮盖 → 清网格选中态
  ▼
设备按序回报 session.command_result（status / reason_code / terminal_message）
  ▼
浴室 App 写库 → 设备的 db_watch 触发 → 出一帧 → 服务端派生
  · door_live.state → 等待开阀            （实测：命令完成后 ≤1 秒）
  · 该门没有未结束的会话 → 开一次 usage_session
```

**判据顺序值得记住**：`waiting`（等待开阀）就说明"会话已建立、还没出水"；真正的出水要等门变 `bathing`，那一帧才会开出第一段。

### 8.2 一次结算

与分配同一条管线，动作换成一个：点浴室 App 的「结算」按钮。预检是"该学号有**占用中**的喷头"（`waiting` / `bathing` / `paused` 都算）。

浴室的答复在 `terminal_message` 里（例如"支付成功…"）。结算成功 → 门那行被清零 → 出一帧 → 服务端关掉会话。**结束原因不记**（见 §4.5）。

### 8.3 配置下发

```
PUT /api/v1/config  或  PUT /api/v1/devices/{id}/config
  ▼  revision 递增
下发 config.update → 设备
  ▼
设备回报 config.ack（服务端等 config_ack_timeout_s = 15 秒）
```

设备端拿到的生效值是三层合并的结果；`config.update` 只带差异。

### 8.4 OTA 发布

```
本机  .\deploy\publish_apk.ps1 -Identity <admin身份标识>
  · 版本号从 android/app/build.gradle.kts 读，不手填
  · 跑门禁 → assembleRelease → POST /api/v1/ota/releases（带 APK 文件）
  ▼
服务端登记 + 裁掉超过 ota_release_keep 个的最旧发布 + 向在线目标设备推 ota.upgrade_available
  ▼
设备下载 → 校验 sha256 → pm install → 覆盖安装由 MY_PACKAGE_REPLACED 广播自启 → 重连
  ▼
重连的 link.hello 里 version_code 即安装确认
```

版本号在**三处**、由 `tests/test_version_consistency.py` 强制一致：`server/app/core/version.py` 的 `APP_VERSION`、`server/pyproject.toml` 的 `version`、`android/app/build.gradle.kts` 的 `versionName`。前两个是 Python 侧（一个供运行时、一个是包元数据），第三个在 Kotlin 构建脚本里——跨语言没有共享一处的办法。`versionCode` 递增是 OTA 覆盖安装的前提。

### 8.5 设备重连

```
WS /gateway/device 建立 → 记 device_link_event(online) 与 api_call(kind=gateway, status=0)
  ▼
link.hello   声明能力、版本、已运行秒数 → 服务端写 device 档案
  ▼
link.pong    带上设备墙钟 → 更新偏差        ← 偏差必须先有，之后的平移才有依据
  ▼
推一遍全貌帧（source = startup）           ← 与状态是否变化无关
  ▼
派生：门现态、事件、会话与段
```

**重连后的第一帧要小心**：若断开期间门一直被占着、且间隔超过 1200 秒，会话会在"最后看见它的时刻"收尾并另起一场——否则库里的陈旧占用会被读成"它还被占着"。

### 8.6 会话维护（替他续期、替他结账）

三个功能都在喷头被占用或已暂停时生效，**名单外的人一概不碰**。开关、阈值与名单都在配置里
（四组：会话维护名单 / 预约续期 / 会话续期 / 自动结算），**保存即生效**。

| 功能 | 什么时候动手 | 上限 |
|---|---|---|
| **预约续期** | 他分到了喷头却一直没开阀，到 `renew_wait_threshold_s`（默认 270 秒） | 同一串最多续 `renew_wait_max_count` 次（默认 3 次） |
| **会话续期** | 他在淋浴中、会话到了 `renew_session_threshold_s`（默认 1140 秒＝19 分钟） | 无上限，可以一直续 |
| **自动结算** | 他关了阀、到 `auto_settle_pause_threshold_s`（默认 60 秒）还没再开 | 每个会话只做一次 |

**名单是 `学号=密码` 一行一个**（配置页的「会话维护名单」组，三份各是"键=值"编辑器）。
三份**各自完整、互不依赖**——同一个学号要开两个功能，就在那两份名单里各写一遍。凭证跟着
名单走，于是"在名单里"与"有密码"是同一件事，**不可能出现"名单里有他、却没有密码"那种
静默不执行**；代价就是那句话：要开两个功能就写两遍。

**学号要照 `door_state_event.cert_no` 里的原样抄**：前导零、空格对不上就是不在名单里，
我们会**静默不动手**（不报错、也不做）。这台设备上"谁"看 `cert_no`——`card_no` 实测恒为 0。

**续期是两步**：先结算，**等门真的空了的那一帧**，再分配回同一个喷头。第二步不能立刻发
——`perform_assign` 的前置是服务端先看见门空闲（它要去读 `door_live`），所以那一步由**帧**
推动，不轮询。补分配有 30 秒时限：没有它的话，设备离线几小时再上线会给一个早已走掉的
学生开一场会话。

**每个会话每个功能只动手一次，失败不重试。** 失败之后重算出的到点还是同一刻，不记一笔
就会自旋。结算被"没有占用中的喷头"拒掉**不算失败**——那正是"他已经走了、或者他自己已经
结过账了"。

**一次续期水会断一下**（结算是把会话结束掉），量级 5–10 秒。所以阈值必须留在浴室 App 那
两条上限以内（`renew_wait_threshold_s` ≤ 300、`renew_session_threshold_s` ≤ 1200）：设到
上限就等于那条判据永远轮不到我们动手，浴室端先释放了。

**同一台设备上的续期一条接一条**（设备只能串行）。同时到点的人越多，最后一个动手越晚，
所以 `renew_session_threshold_s` 那条余量（1200 − 阈值）要盖住"两条命令 ÷ 排队长度"。

**"同一串"由喷头的空档划断**：相邻两场之间「前一场的 `ended_at` → 后一场的 `assigned_at`」
不超过 300 秒（浴室 App 那条无水释放门槛）就算同一串。我们自己的续期间隔是秒级，真人走了
再回来是分钟级，两者分得开。

**"暂停从哪一刻开始"就是 `door_live.close_time`**（正名「关阀时刻」，与暂停时长的口径同源）。
它要经钟偏差换算，所以**设备还没有偏差记录时是空的**——真实设备连上后一个往返内就有
（网关在 `link.hello` 之后立刻主动 ping），而且偏差跨重启持久化，所以线上不会落在那个
空窗里。测试里要显式建立偏差，否则测的不是线上的样子。

判据在 `app/modules/sessions/decision.py`（纯函数、逐条单测），机制在
`app/modules/sessions/scheduler.py`。

---

## 九、注意事项

**时间**

- 设备轴不可信，对外只出服务端轴。
- 偏差在**两次校时之间被沿用**，所以设备钟在那期间被改过、而平移值恰好落进三条自洽检验的缝隙里时，起点会偏——2026-10-02 实测过一次（偏了整整一小时）。
- `bath_time_s` **不单调**（只在关阀那一刻累加、释放时归零），不能拿去减。

**数据**

- **盲区不等于故障**：它只说"我们没看见"。
- **五种段长来源强度不同**，统计必须分开看。
- **截断过的段（`capped`）不等于"真的洗了那么久"**。
- **结束原因不可判**，别写"这是超时释放/主动出门"。
- **`CREATE TABLE IF NOT EXISTS` 意味着已存在的库不会跟着 DDL 变**——改 DDL 要重建库（开发阶段的取向，见 `AGENTS.md`）。若本地库报"没有某一列"，那是它落后了，删了重建。

**接口**

- 推送是尽力而为（队列满即丢），**现态以 REST 为准**。
- 命令是**同步**的：服务端等到设备答复或超时（`assign_timeout_s` / `settle_timeout_s` = 60 秒；`config_ack_timeout_s` = 15 秒）。
- 设备声明的能力集决定能下发什么：**声明了、但不含这个动作**就拒发，而不是等它回 unknown_action；**空能力集不据此拦截**（那表示还没升级过的老设备，拦了会把在网设备锁死——见 `sessions/service.py` 里那一处的说明）。
- 同通道的命令排队执行，**队满才拒**（那说明积压已多，该换个时间再来），与"设备正忙"不是一回事。

---

## 十、术语

唯一正名与弃用词在 `server/app/core/terms.py`（含全仓扫描器）。**本文用它的词，不另立一份**：`python -m app.core.terms` 可随时自查。
