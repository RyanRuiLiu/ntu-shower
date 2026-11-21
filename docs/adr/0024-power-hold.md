# ADR-0024: 电源计划域（hold 替代拨钟态）

- 状态：Accepted
- 日期：2026-11-20
- 决策者：项目负责人
- 取代：ADR-0007 中「崩溃守护参数化」之外的全部设备时钟编排描述；`modules/extend_hours`
  的过程式拨钟状态机整体删除（Git 历史即其存档）

## 背景

旧实现（extend_hours）是**过程式拨钟状态机**：触发 → 拨快 → 滚动续写 → 过午夜且无人
占用后拨回。2026-11-20 二楼事故暴露了它的结构性缺陷：收尾审计写库撞上 job_runs 枚举
词表的 CHECK 约束（测试库由 metadata 建表、生产库由迁移建表，词表漂移不可见），
`clear_hold` 永远执行不到——设备钟被每轮 −15 分钟持续回拨，两天累计 21.3 天，且自动
校时被「延长中」互斥条件一直压制。

## 决策

1. **计划由设备自报，服务端不定任何时刻**。设备读浴室 App 参数文件（V8
   autoCloseTime/autoOpenTime）经 `device.status` 帧上报，存入设备影子行。配置键
   `power_off_at` / `power_on_at` / `server_zone_offset_minutes` 删除——全部自适应，
   平台改计划无需任何服务端改动。缺报按"计划未知"处理：不动手、告警，不发明默认值。
2. **延长 = hold（恒温器行业语义）**：一次性、按夜定界、自动恢复。影子行
   `hold_started_at / hold_source / hold_offset_s / hold_assignments` 表达；manual
   （设备页按钮）与 automated（名单占用）两种来源共用同一状态机与恢复路径。
3. **写钟一律绝对钟面 step（chrony makestep）**：目标由服务端轴 + 上报时区计算，与
   设备当前读数无关——重复写结果一致，2F 式的增量回拨灾难在结构上不可能。
4. **hold 期间 MCU 截止是租约（DHCP/etcd lease）**：每 120 秒重布"表盘此刻 + 600 秒"，
   服务端消失最迟一个租期断电。租约到点落库（`lease_deadline`），进程重启对账可见。
   （**已被 [ADR-0025](0025-single-arming-hold.md) 取代**：滚动续写与 MCU 的钟面检查
   语义冲突，2026-11-21 六台集体断电；改为一次布防承担整晚。）
5. **执行是对账（Kubernetes controller）**：每 30 秒逐设备使影子行收敛于期望；每步
   幂等、崩溃重入安全。意图先落库、动作随后、审计最后——审计失败绝不回滚状态。
6. **偏差只测量、不被命令**：`shift_clock_skew` 删除。偏置期间测量偏差天然等于
   −offset + 真实漂移，doors 逐读数平移（ADR-0002）自动给出真实时刻；写钟后第一帧
   即收敛（帧自带 `device_clock` + `captured_at`，gateway 先更新偏差再派生）。

## 行业参照

BACnet Schedule（周计划 + 例外日程）、恒温器 Hold Until / Resume Schedule
（Honeywell/ecobee）、AWS IoT Shadow（影子行 reported，项目已有两处同型引用）、
Kubernetes reconcile、DHCP/etcd lease、chrony makestep。

## 后果

- 两端契约变化：服务端删 3 配置键、`device.status` 新帧、`/devices/{id}/hold|resume`
  新端点、看板 `extending` → `holds`；设备端 OTA 一次（PowerFacts 读取与上报、
  TimeSync 重布目标改本地事实）。
- 旧拨钟词汇整体退役（shift/restore/clock-moved 等终局值、extend_hours 键与模块名），
  词表由测试对迁移建出的库逐值 INSERT 钉住——词表漂移从此必红。
- 无兼容层：不保留旧键、旧模块、旧进程内状态；生产部署即解除二楼死锁（迁移清列）。
