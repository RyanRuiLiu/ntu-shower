# ADR-0014: 可观测性

- 状态：Accepted
- 日期：2026-10-30（整理入档；原始决策日期见 Git 历史）
- 决策者：项目负责人
- 取代：无

## 背景

本条目汇总该领域的有效决策；原始讨论、实测与取舍见相关代码注释、`docs/evidence/` 与 Git 历史。

## 决策
>
> 标准依据：OWASP Logging Cheat Sheet（记什么、不记什么）、NIST SP 800-92
> （审计记录的生成/保护/保留）、W3C Trace Context（关联标识）、OpenTelemetry
> Log Data Model（字段命名）。2026-10-01 采纳。

- **三层痕迹，一条链**：`api_call`（HTTP/网关审计）→ `command_audit`（命令级审计）
  → 设备端 `audit.log`（命令痕迹 JSONL），以 `command_id` 与 `trace_id` 贯通——
  "这个命令服务端发给谁、设备收到没有、结果是什么"在三个层面各有一行可查。
- **审计记什么**：全部 /api、/ops、/ota 请求（认证失败、授权失败、参数不合法、
  服务端错误天然落表，OWASP 的必记事件类）；设备网关的连接与断开（含设备来源 IP）。
  不记静态资源与 /healthz；发布上传的 APK 二进制体不落库（本体已存 ota_releases，
  审计行记版本与大小即完整事实）。
- **如实记录、不脱敏、不截断**（2026-10-01 用户决定，覆盖同日早先的脱敏决定）：
  本系统是单运维者工具，审计的价值就在于如实——调用方令牌、身份、来源 IP、
  User-Agent、请求体原文**全文落库**；`device.exec` 的输出（不可重现的运维事实）
  随命令审计落 `data` 列。媒体（截图/拍照的 base64）与可重取的 `ui.dump` XML
  仍不落库——那是体积与时效取舍，不是脱敏。stdout 日志不写身份、学号、口令与令牌；两者的分工见 `docs/SECURITY.md`。
- **两个审计分类的分工**：`api_call`（接口调用）记"每一次后台请求——谁、从哪个
  IP、调了什么、结果如何"；`command_audit`（命令流水）记"每条发到设备的命令——
  参数、结果、设备回报"。**必要分开**：命令是"先登记、结果异步回填"的两段式
  生命周期，与一次写完的请求行形状不同；合并会得到一张两边都缺列的稀疏表。
  前端用标签切换并以 tooltip 写明各自职责。
- **stdout 日志是 JSON 事件流**（12-Factor §XI）：一行一个对象，字段照 OTel
  Log Data Model（`ts`/`level`/`logger`/`message`/`event`），`trace_id`/
  `request_id` 有值才输出。`event` 是给机器检索的稳定代号（kebab-case），与
  中文 `message` 分开——改文案不能破坏检索；属性只收标量。不写文件、不做轮转，
  归档是运行环境的职责。
- **HTTP 与命令的关联**：W3C `traceparent`（调用方带就沿用其 trace-id，否则生成），
  响应回 `X-Request-ID`；问题响应经 `X-Problem-Type` 头把 RFC 9457 type 带进审计行。
  命令派发时把 trace_id 写进 `command_audit`——跨层查一次 SQL 就是全链路。
- **保留期执行而不是声明**：审计两表（`api_call` 与 `command_audit`）按
  `audit_retention_days`（默认 90 天）由每日任务删除——审计是滚动窗口；**业务
  数据（喷头状态迁移、使用会话）永久保留**，那是运营历史。OTA 发布按
  `ota_release_keep`（默认 5 个）在新版登记时裁剪。
  设备端落盘文件全部有界且集中：`/sdcard/ntu-shower/` 下 device.log（统一日志，
  见下）/ crash.log / automation.log 各 256KB 封顶、ota-install.log（脱手脚本
  唯一可写通道）每次安装截断、OTA 下载包装完即删、outbox 按行数上限裁剪、
  shell pid 文件正常完成与超时两条路径都自清。敏感数据（发件箱含学号）在应用
  私有目录 `/data/data/<包名>/databases/agent.db`，共享存储上无敏感内容。
- **设备端统一日志（core.log.DeviceLog）**：全设备唯一结构化落盘入口——一个类、
  一个文件（device.log，JSONL）、一种格式，事件名与服务端 log_event 同族
  （command.*/ gateway.* / service.*/ config.* / ota.*）。服务生命周期、配置
  变更、命令痕迹、网关状态全部经它落盘；敏感红线（学号/密码/令牌/剪贴板内容
  永不入日志）写入类注释作为硬约定。看门狗闹钟用 setExact 逐次排（4.4 的
  setRepeating 有不可纠正的批处理漂移；setExactAndAllowWhileIdle 要 API 23）。
- **设备端痕迹是文件而不是 Logcat**：Logcat 环形缓冲重启即空，而"命令收到没有、
  回了什么"是远程排障的第一直觉；`/sdcard/ntu-shower/audit.log` 用 `device.exec`
  直接 `cat` 取回。

## 备选方案

原始记录未保留完整的备选方案；被取代的结论见 Git 历史。

## 后果

见各条决策正文与相关代码注释。

## 验证

由相关测试、`docs/REFERENCE.md` 与运行记录验证。
