# Changelog

本文件从 0.6.2 起按 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) 维护。变更分类固定为 Added、Changed、Deprecated、Removed、Fixed、Security。版本号采用 [Semantic Versioning 2.0.0](https://semver.org/)。

> 版本核对（2026-11-05）：`0.6.4` 从未存在于任何源码树或安装包中——版本号由
> 0.6.3 直接升到 0.6.5（提交 d9e29d2），该节原为一次记档性条目。其内容已并入
> 下方 0.6.5 的 Fixed；不保留一个没有对应版本状态的版本号。

## [Unreleased]

### Changed

- **迁移链重置为单一基准**：全部历史增量迁移（initial schema、power hold、列更名、审计标准化）删除，从当前模型自动生成一份干净的 baseline（21 张表一次建齐，与 metadata 逐字节一致）。此后结构变更只在基准上生成增量。审计两表（api_calls/job_runs）为滚动窗口数据随旧表弃用，不写兼容搬运；生产切换经一次性结转脚本（用完即删）完成业务数据迁移。
- **审计按官方标准逐条对账修订**（OWASP Logging Cheat Sheet / NIST SP 800-92 / OpenTelemetry Logs Data Model / W3C Trace Context，官方现文核实，ADR-0014 修订）：自创项清零——api_calls 删 kind 列（网关生命周期行的唯一出处是 device_link_events）与 device_id 列（从路径或查询串推断设备属自创关联，调用审计只记录请求本身）；actor_kind 词表删 identity（X-Identity 已于 ADR-0019 删除）；job_runs 的 door_no/cert_no 改可空，0/空串哨兵占位废除，旧行哨兵值改写 NULL；stdout 日志字段名换 OTel 官方键（timestamp/severity_text/body/event_name/attributes/exception.stacktrace/resource.service.name）；X-Problem-Type 自创响应头删除，problem 的 type 与 detail 改从 RFC 9457 响应体官方字段读取。
- **历史可见性补齐**（管理面板此前只覆盖三类）：设备上下线历史（`GET /devices/{id}/link-events`）、日志上送历史（`GET /devices/{id}/logs/history`）、OTA 安装记录（`GET /ota/installs`，新 ota:read 作用域）三个只读出口新增，web 两套审计面板补齐安装与链路两类。

## [0.8.6] - 2026-11-22

### Changed

- **凌晨晚关门（close 早于 open 且已越过午夜+10）的恢复时刻改为 close + 10 分钟**：此前统一按「午夜后 10 分钟」现算，该场景会形成近一整天的跳变；恢复时刻必须落在被跳过的 close 之后，午夜+10 在此刻已是过去。常规夜（含 close ≤ 00:10 的跨午夜触发）语义不变。
- **OTA 自安装的重启掉线不再误报「布防可能未生效」**：设备上报 `ota.progress`/`installing` 后的进程重启掉线是预期表现，gateway 将该时刻通知电源计划并形成 300 秒宽限窗，宽限内的掉线豁免 `hold-failed` 留痕；宽限外恢复正常告警。
- 旧版与兼容残留清理：设备端删除旧播报缓存目录（`/sdcard/ntu-shower/voice`）的升级清理逻辑（0.8.4 起各设备启动时已执行）；web-legacy 剪贴板删除「选中让手动复制」的降级分支（生产 https 安全上下文下 clipboard API 恒可用，统一失败出口）；删除 X-Identity 相关测试（机制已删，未知头的忽略行为属框架而非产品，决策见 ADR-0019）。

## [0.8.5] - 2026-11-22

### Changed

- **今晚延长（hold）改为整夜覆盖布防**：断电目标 = 次日 auto_close_time（`power.schedule` 新参数 `off_day_offset=1`），设备带偏置运行到清晨、次日 auto_open_time 照常上电——取代 0.8.3 的「恢复时刻+余量」短延长（午夜后 20 分钟即断电）；「今晚延长」的业务意图就是整夜不关机（ADR-0025 修订）。短延长的实证缺陷（偏置存续期内校正回退偏置，真实断电时刻从布防目标漂移）随「截止恒在钟面之外」一并消失。
- **恢复补写准一步**：恢复时刻的恢复先把墙钟写回真实轴（无条件执行，不依赖 time_sync 开关——偏置不写回则下一晚的恢复时刻算术即被破坏），再清 hold 列；写准失败（离线、上报事实缺失）hold 保持、下一轮重试。取代 0.8.3 的恢复零命令设计。恢复时刻前的手动恢复是尽力取消：先写准、再重布「真实此刻 + 1.5 分钟」的更早截止。
- 命名随语义更名：`lease_deadline` 列更名 `arming_deadline`（纯更名迁移），自创词清零——「落点」→恢复时刻、「park」→pending、「放回」→重分配；web 两套界面的 hold 文案与「MCU 布防截止」展示同步。
- 网关 app 层 link.ping 降频为纯锚点载体（300 秒），判活唯一实现在 WS 协议层（设备 OkHttp pingInterval 30 秒 / 服务端 uvicorn 默认 20 秒）；OTA manifest 轮询降频到 6 小时（推送为主通道，安装以手动推送为常态）。
- 设备端 `McuPowerSchedule` 支持断电目标的日偏移换算（总延时不足时如实拒绝的 0 延时守卫不变）。

### Fixed

- 事故时间线用例按整夜覆盖语义重写；新增两个边界用例：午夜后请求的 hold 意图跨日存活到今晚触发窗（以建立时刻之后第一次到来的 close 为界）、已施加后再点延长幂等且不下发任何命令。## [0.8.4] - 2026-11-21

### Changed

- **两端基础代码按 14 个功能方面完成全面重写**（协议契约、持久化基座、时间、安全网络栈、身份与链路、命令通道、电源设备端、配置、审计、采集、OTA、外设、组合根、web），行为语义由既有测试钉住。要点：
  - 消息类型单源化到 protocol/schema（生成两端登记表）；config.ack → config.update_ack、ota.upgrade_available → ota.update_available 按行业用词改名。
  - session_scope 的引擎按 URL 单例缓存（SQLAlchemy 官方模型：进程内创建一次）。
  - 设备端跨端时刻统一到服务端时间轴（信封 ts、日志 ts、落盘配置时刻均取 ServerClock）；ServerClock 锚点改单引用整体交换，消除并发交错下的负增量。
  - TLS 信任面收窄到单一根（ISRG Root X2，生产链实测单根可达）；DNS 事务 ID 换安全随机源；明文 URL 禁入运行时代码并入 semgrep 门禁。
  - 链路心跳三层收敛两层：设备侧判活移交 OkHttp pingInterval（WS 协议层），设备自发 ping 删除；关闭码按 RFC 6455 私用段分工（1008 凭据无效重注册、4403 设备停用停机）。
  - 命令传输层锁等待换标准 wait_for（3.12 起官方重实现）；审计路径收敛两条；维护通道执行前判过期。
  - MCU 写入器按受控实验定论重写：最后写入唯一生效、0 延时在唯一入口拒绝（无立即语义）、enable 固定 3；写钟配对语义对齐单次布防（ADR-0025）。
  - 采集中间件迁纯 ASGI（Starlette 官方推荐形态）；traceparent 按 W3C 规范收紧（小写、拒全零、拒 version ff）。
  - 网关单进程前提写入 Dockerfile；配置登记表自描述对齐实际（归档默认开、四份名单）。
  - 全部注释按正式书面语言标准重述；emoji 全项目禁用并入 semgrep 门禁。

## [0.8.3] - 2026-11-21

### Fixed

- **今晚延长（hold）的断电截止少算一个偏移量**：预补偿布防写在拨钟之前（此刻墙钟=真实轴），拨钟把墙钟推前 offset_s，而 MCU 的钟面检查按拨后钟面走——截止实际在 apply+租期（00:02）到点，而非落点+租期（00:20）。2026-11-21 事故：六台 hold 后在 00:00–00:02 集体断电。公式改为「表盘此刻 + 2×偏移 + 余量」。
- **hold 的恢复判定改为落点**（施加时刻+偏移）：旧判定在被跳过的 close（23:58）一过就恢复，比落点早 12 分钟——延长的全部意义就是越过 close。恢复改为**零命令**：不动钟面，写回由 following_schedule 的自动校时承担，其重布按营业时段判定（过 close 不重布，否则延长被变成 24 小时）；恢复重布按事实换算出 0 分钟延时是当晚集体断电的直接一环。
- 拨钟写准后作废旧钟偏差（`device_clock.forget`）：拨钟后 30 秒内的续租/校正按旧轴换算会把截止布错轴（当晚 3F 的续租布到了次日 00:02）。
- 触发窗外的 hold 意图先 park：此前 00:05 的误点会以「下一个 close」为界把服务延长到次日 00:10，等于 24 小时营业。
- 布防在位、落点未到而设备掉线：留痕一次（布防可能被 MCU 静默丢弃，漏报会让延长无声失效）。

### Changed

- hold 的 MCU 布防从滚动租约（每 2 分钟续写「表盘此刻+租期」）改为**一次布防承担整晚**：滚动续写的截止可能不晚于已在位的那个，与 MCU 的钟面检查语义冲突。服务端消失截止照常到点——到点断电正是期望终态，无需另设失效安全窗口。
- resume 端点不再要求设备在线（409 移除）：恢复只清状态，离线同样可恢复。
- 事故时间线以用例钉死：用当晚真实参数（close 23:58、23:51:44 点击）逐 tick 重演，断电截止必须落在落点+余量、恢复零命令、恢复后的校正不重布。

## [0.8.2] - 2026-11-20

### Changed

- 计划事实的读取时机收敛到**连接建立时**（含重连与服务重启后的首次连接），60 秒看门狗 tick 不再读文件：平台计划一天至多变一次，1440 次/天的轮询没有收益；连接存续期间的变更由下一次重连或次日开机刷新。

### Fixed

- 设备计划事实改为读浴室 App 自己的 `data.xml`（`autoopentime` / `autoclosetime`，与 App 启动时装载并交给 smdt 布防的是同一份值），不再读 `/sdcard/datalook/log/Params_*.txt` 下载日志：日志只是副本、会被 `logExpiredDate` 清理，且 0.8.1 的 `rootRead` 把路径当命令下发，shell 去执行那个 `.txt`，退出码非 0 后按缺失处理——`device.status` 始终缺报。读取统一走 `cat '<path>'`（与 `WaterTargetReader` 读 waterList 同一写法），用例钉住命令形状与键名。0.8.1 发布时设备已断电，缺陷未在真机暴露。

## [0.8.1] - 2026-11-20

### Fixed

- 设备端计划事实上报不出（125 引入）：浴室 App 参数文件 `Params_*` 属 `root:battery`，agent 进程直读拿不到内容，`device.status` 因此始终缺报——改经 root shell 列目录与读文件（与 DoorSnapshotReader 读 bath.db 同一前提）。facts 落库后校时、hold 与语音时区按既有对账链路自愈，无需服务端变更。

## [0.8.0] - 2026-11-20

### Added

- **电源计划域（power）重写**：设备经 `device.status` 帧自报平台开关机计划与本机时区（读浴室 App 参数文件，V8 autoCloseTime/autoOpenTime），服务端不再持有任何时刻配置——`power_off_at` / `power_on_at` / `server_zone_offset_minutes` 三个配置键删除，平台改计划无需服务端改动。延长服务改写为 hold（恒温器行业语义）：影子行状态 + 30 秒对账 + MCU 租约（2 分钟续期/10 分钟租期，失效安全）；写钟一律绝对钟面 step（chrony makestep），禁止增量拨。设备页新增「今晚延长」「恢复计划」按钮（hold/resume 端点）。删除 `modules/extend_hours` 过程式拨钟状态机、`shift_clock_skew` 与 job_runs 词表外的全部旧词汇（shift/restore/clock-moved 等终局值）。迁移重建 `job_runs` 词表 CHECK 并清理二楼残留拨钟态；测试建库改走 alembic（与生产同源），迁移词表漂移由用例逐值 INSERT 钉死。行业参照与事故背景见 ADR-0024。

### Added

- 安全政策（`SECURITY.md`）：漏洞报告渠道（GitHub 私密漏洞报告）、确认时限、支持范围与设计立场；内部威胁模型仍归 `docs/security.md`，两份职责分离互相链接。
- Issue 模板（`.github/ISSUE_TEMPLATE/`）：缺陷报告表单与分类入口；漏洞一律走安全政策渠道，不走公开 Issue。

### Changed

- CI 公开化前置加固：每个 job 全部声明 `timeout-minutes`（挂起按额定分钟计费，不再烧 GitHub 6 小时全局上限）；全部 checkout `persist-credentials: false`；semgrep 两步并入 server job；Dependabot 自动合并改官方 `gh pr merge --auto` 单步；main 推送跳过 ci（同一提交已在 PR 全量验证，main 由 build→deploy 链守卫）。
- 全部文案去 emoji：文档、注释与界面提示的装饰性符号删去（功能性键盘字形与占位符不在禁列）。
- 协议 schema 的 `$id`/`$ref` 文档 URI 换用 RFC 2606 保留域 `example.com`（占位符尖括号不是 RFC 3986 合法 URI 字符；`$id` 是静态文档 URI，不是部署期配置值）。
- ACR 镜像仓库与命名空间改经仓库 secrets 传入（`ACR_REGISTRY` / `ACR_NAMESPACE`），共享构建 workflow 对空值回退合法 local 名——公开后 fork PR 拿不到 secrets 也能完成 build-only 校验。

### Fixed

- 历史重写期 `docker.yml` 的镜像引用被字面占位符化，构建链以 invalid tag 失效——随上一条 secrets 化恢复，build→deploy 链全绿。
- 快速开始补 `alembic upgrade head`：fresh 库直接 uvicorn 启动即崩（lifespan 的未完成命令收尾查询先于 schema 存在），README 与 CONTRIBUTING 已按实测路径修正。
- 0.7.5 配置面精简的收尾：注释与文档里指向已删配置键的引用全部改指现存常量（runbook 语音与桥接排查段、ADR-0007/0012/0013 按现行行为修订、两端代码注释、测试样例键）；遮盖层与崩溃守护钉死后的恒真开关与解析兜底按「不兜底」移除，行为零变化。
- 设备页「已运行」误用钟偏差渲染（0.7.3 重写引入）：改为从开机时刻推算真实运行时长。

### Security

- 现行树生产标识占位符化：compose 镜像仓库、nginx 域名与证书路径、发布脚本默认域、`gradle.properties.example` 服务域名、`bridge_ssh_target` 默认值、全部测试样例学号（2026 段 → 9999 段合成值）；部署侧真实值由服务器私有配置持有。

## [0.7.5] - 2026-11-17

### Changed

- **配置面精简**：86 键 → 47 键、24 组 → 12 组（生产实测 52 键从未被改动）。删诊断调参 10、一次性基建 11、遮罩 5、守护 4、未用布局 5、桥接超时 3、后台任务阈值 7（`renew_wait_threshold_s` 270、`renew_session_threshold_s` 1140、自动结算暂停 90 等钉为消费模块常量，取值均有物理出处）；`bridge_ssh_target` 移入部署侧 `.env`（与镜像仓库同类：部署期事实不是运维旋钮）；`notice_enabled` 保留（业主设置的关闭状态，不改语义）。所有删除值 = 现行默认值，生产行为仅两处可见：`auto_settle` 暂停结算阈值 60 → 90（真实 63 秒回归案例留余量）、`bath_db_archive_enabled` 默认开（自愈即默认）。
- **配置修订号语义闭环**（AWS Shadow version 同型）：设备端拒应用 ≤ 已应用修订号的 config.update（乱序守卫）；服务端校验 config.ack 回执的修订号与推送一致，不符按失败处理并留 WARNING。
- **配置页覆盖可见性**：设备视图按 scope 过滤（服务端键不再渲染为存不了值的空输入）、被覆盖键显示「已覆盖」徽标与「恢复全局」按钮、修订号真实显示；设备清单改走统一 api client。

### Fixed

- 快速开始补 `alembic upgrade head`：fresh 库直接 uvicorn 启动即崩（lifespan 的未完成命令收尾查询先于 schema 存在），README 与 CONTRIBUTING 已按实测路径修正。

## [0.7.4] - 2026-11-15

### Changed

- 协议帧 `session.command` / `session.command_result` 更名 `device.command` / `device.command_result`：24 种设备操作里只有 2 种与 session 有关，session 一词无语义；与 `device.log_upload` 统一设备域 namespace（帧内 `action` 字段取值不变，连接侧零配置改动）。
- 失败码 `door.no_occupied` → `door.unoccupied`（语法规范化）。两端契约生成物同步重生成，versionCode 122 → 123，全部设备经 OTA 自动升级。

## [0.7.3] - 2026-11-15

### Added

- **管理端全部重写**：Vite 8 + React 19 + TypeScript + HeroUI 3.2.7（毛玻璃暗色设计体系）+ TanStack Query + React Router history 模式；9 视图全功能；TS 契约类型对账；Vitest 冒烟；ESLint 10 + typescript-eslint 8 接入门禁与 CI web job。
- **HTTP 契约重排（12 处）**：单设备资源 `GET /devices/{id}` 取代 `/devices/runtime`；集合重排 `PATCH /devices` 取代 `PUT /devices/order`；动作端点扁平化（clock-set / restart-app / restart-bath / ui-dump / ui-touch / ui-key / ui-input）；`water/status` 改 GET `water-status`；ota rearm 目标设备入请求体；删除与 `/users/me` 重复的 `/auth/me`（login 改 204）。
- **SPA fallback**：非 API 未登记路径回应用入口（history 路由刷新）；API/网关/OTA/健康检查前缀保持 problem+json 404。

### Removed

- 旧前端（零构建 JS）与配套门禁三件套（test_web_layout / test_web_api_surface / test_water_terms），由 TS 严格类型 + eslint + vitest 承接。

## [0.7.2] - 2026-11-15

### Added

- **用户与密钥管理域**（ADR-0023）：`/api/v1/users` 登记用户、签发/列表/吊销 API 密钥（`ntushower_` 前缀，明文一次性下发）；权限与设备作用域都附在密钥上，密钥默认永不过期、多枚共存；管理台新增「用户」页。
- **对外业务调用**：外部系统凭最小权限密钥即可调分配/结算/拍照（`sessions:write` / `devices:photo` × 设备作用域）；`/openapi.json` 渲染 securitySchemes 与每操作 scopes，即对接文档。

### Changed

- **权限模型重写**：`principals` 改名 `users`（删除 kind——账户不分类），权限从三个能力位改为 `resource:action` scopes 字典（16+1 项，`*` 通配）；守卫走 FastAPI 官方 SecurityScopes，失败码对齐 RFC 6750（`invalid-token` / `insufficient-scope`）。
- **会话生命周期**：12 小时固定过期改为 30 天滑动 + 90 天绝对（OWASP Session CS），管理台不再每天重登。
- **限速按调用方分桶**（nginx）：持钥按密钥一桶、匿名按 IP 一桶，登录端点专用 1 r/s；`/docs` `/redoc` 调试面关闭。

### Removed

- `principals` 表与 `X-Identity` 后的旧凭据形态：一次性迁移脚本把旧凭据摘要原样搬入 `api_keys`（旧令牌不断手），身份 kind、主体级能力位删除。

### Security

- 登录限速、按凭据分桶限速（OWASP API4）、调试面关闭（OWASP API8）；决策与备选见 `docs/adr/0023-permission-rewrite.md`。

## [0.7.1] - 2026-11-14

### Changed

- **设备域表归位**：设备相关 6 张表集中到 `models/device.py`（devices/credentials/
  configs/logs/link_events/clock_shifts），`clock_shifts` 更名 `device_clock_shifts`
  （设备表统一 `device_` 前缀）；`identity.py` 仅剩 Principal，`settings.py` 仅剩
  AppSettings；`persistence/device_credentials.py` 并入 `devices.py`。
- **凭据无过期语义**：`device_credentials.expires_at` 列删除（生命周期=签发→吊销，
  enroll 轮换即吊销重发，无"到点失效"第三态）；authenticate 不再收 now 参数。
- **保留期声明=实现**：清理任务从 2 张表扩到 6 张——api_calls/command_audits 90 天
  （可配）、job_runs 180 天、device_link_events 90 天、door_snapshots 30 天、
  voice_deliveries 90 天（天数与各表 docstring 声明一致，
  tests/test_retention.py 钉住）；device_logs 声明修正为实际机制（每设备每类滚 5 份）。
- **索引整备**：删 3 个被复合索引覆盖的冗余单列（door_states/door_snapshots/
  usage_sessions 的 device_id）与 4 个无消费者索引（devices.last_seen_at、
  api_calls.trace_id、usage_sessions.ended_at、voice_deliveries 的两个）；补
  ota_release_installs.device_id 单列索引；`api_calls.kind` 闭集枚举化
  （ApiCallKind，http/gateway）。
- 看板信封键与前端消费统一 `jobs` 词根（#59 遗留的 `row.task` 静默 bug 一并修复）。

### Changed

- **设备双 ID 消灭**（业主裁定：全库实体标识统一，外键 `device_id` int 与业务键
  `device_id` str 同名异型的唯一漂移根除）：`devices` 表主键直接采用设备业务键
  （ANDROID_ID，ADR-0008 的设备身份），12 张子表外键改 str 直引；审计三表
  （api_calls/command_audits/job_runs）与 voice_deliveries 的 `device_id` 改为
  无外键的业务键留底（审计事实不因设备除档失联），`device_key` 冗余列全部删除；
  persistence 层 `_device_pk` 换算层整片移除。对外契约（API 字段、协议 payload、
  三端代码）**零改动**——`device_id` 名称与值不变。未登记设备的状态/配置写入由
  外键当场拒绝（原静默跳过），存储层落不变量。迁移为基线再重置 + 一次性脚本
  （子表 int→str 经 join 值改写，仓库外，用后即删）。

### Changed

- 后台任务域全链更名为 **jobs**（业主裁定全新重写式整改，旧名不作为锚点）：模块
  `tasks`→`jobs`、接口 `/api/v1/jobs/status`、数据库表 `background_task_runs`→`job_runs`
  （列 `feature`→`task`→`job`）、日志事件 `background-task.*`→`job.*`、审计端点
  `/audit/job-runs`、契约模型 `Job*`、管理台字段与页签。迁移方式为**基线重置**：
  迁移链回归单一初始 schema（与模型零漂移），生产库由一次性脚本原地改名并盖版本戳
  （脚本用后即删，不进仓库）；旧迁移链（含 task 中间态）不再保留。
- 名单读取函数 `roster`→`students_of`、看板字段 `roster`→`students_count`、事件
  `roster-changed`→`students-changed`：roster/students 双词并存指同一份名单，统一到
  `students` 词根（freelist 对外契约已在用）；配置键 `*_students` 不动。
- 看板响应模型 BaseModel→TypedDict，对齐项目"响应用 TypedDict、请求用 Pydantic"分工。

### Added

- import-linter 契约扩到 5 条：业务模块全族禁环（acyclic_siblings）、核心层零向上依赖、
  持久化层禁业务模块整包、持久化层禁认证层，加上原有两条——分层纪律全部机器化。
- 网关对入站帧校验协议版本：hello 与会话帧的 `v` 不符回 `proto.version_mismatch` 并断开
  （RFC 6455 1002），信封版本字段首次有了运行时语义。
- CI server job 补 ESLint 门禁（与本地 check.sh 同参）：全仓唯一"本地有门、CI 无门"的检查补齐。

### Changed

- 破除 config↔sessions↔devices 三个包级循环依赖：命令传输下沉 `devices/actions.py`、
  会话领域入口下沉 `sessions/service.py`、设备在线守卫落到 `access.dependencies`、
  共享形状 `CommandOutcome` 上移 `app/contracts.py`、配置推送改依赖倒置
  （`config/notifier` 通知槽 + `main.py` 组合根装配）。
- `core/audit.py` 按归属拆分：采集与装配 → `app/audit_middleware.py`，存储 →
  `persistence/api_calls.py`；令牌与口令哈希工具从 auth 下沉 `core/tokens.py`/`core/passwords.py`。
- `semgrep` 文档引用规则封堵三种真实逃逸形态（裸文件名、反引号分隔、书名号章节、行号），
  现存文档清单补全。
- 语音素材上传只接受 **44.1kHz 单声道 MP3**（设备端拼流的硬性要求），时长/采样率/声道
  由 mutagen 校验；服务端不再转码，ffmpeg 与 ffprobe 移出运行时镜像（其静态层约占镜像
  六成），转换收敛到上传方（`ffmpeg -i in.wav -ac 1 -ar 44100 -b:a 128k out.mp3`）。
  `GET /voice/status` 不再返回 `ffmpeg_available` 字段。

### Removed

- 后台任务命名全链统一为 task（此前 task/feature/「服务端业务」三词并存）：判据表
  FeatureSpec/FEATURES→TaskSpec/TASKS、`background_task_runs.feature` 列改名 `task`
  （迁移，列值不变）、看板响应字段与配置页页签同步；接口收敛为 `/api/v1/tasks/status`
  （原 /background-tasks/status，唯一消费者是自有管理台）。
- 小程序自助登记接口删除（业主裁定 2026-11-14：名单配置的唯一入口是管理台）：
  GET /tasks、PUT /tasks/{task}、用户面文案模板与取值机器、`tasks` 能力位与
  require_capability/DEVICE_SCOPED 守卫机制（最后一个消费者消失后随之移除）；
  概览页后台任务看板保留，ADR 不涉及。
- 小程序统计功能全链删除（业主裁定 2026-11-14，功能早已下线）：`/api/v1/stats/*` 三条
  路由、stats 模块（1710 行）、`stats` 能力位、persistence 层 4 个 stats 专属取数函数；
  ADR-0004 标 Deprecated 留档。
- 无消费者的死面整体移除：`ui.scroll` 动作三端全链（含唯一配置键 `ui_swipe_duration_ms`）、
  `power.schedule` HTTP 端点（动作保留，延长服务在用）、`DELETE /devices/{id}/config` 全链、
  `SOURCE_IMPORT`、`SnapshotSource` 枚举、`adhoc_problem`、v1 遗留裸 sqlite3 层 `core/db.py`。

### Fixed

- CHANGELOG 0.7.0 的 Added/Changed 重复块与"systemd 冻结保留"失实描述；
  12 篇 ADR 的悬空引用（不存在的模块/路由/日志文件、单数表名、备份机制描述）以后记修正。

### Changed

- main 分支保护上线：五个门禁为 required checks、线性历史强制、管理员不豁免（应急旁路保留）；android 门禁按 PR 变更路径裁剪，纯服务端改动不再等待安卓全量构建（push main 仍全量）。
- 基镜像按 digest 钉版（python 构建两级与 litestream），tag 保留供人读；升版即换 digest 走评审。

### Added

- ADR-0022：安卓依赖冻结 API 19 兼容线的裁定、备选方案与重启条件（useAndroidX=false、okhttp 3.12.x、conscrypt 2.5.x、dependabot ignore 编码上限）。

## [0.7.0] - 2026-11-13

### Added

- 服务端切换为 Docker Compose 部署：镜像由 CI 构建并双推 GHCR/ACR，服务器 `docker pull` +
  `compose up -d`；容器以非 root（UID 997）运行，根文件系统只读、能力全丢弃、端口仅绑回环。
  旧 systemd 单元与 venv 已于 2026-11-12 删除：回滚只有指回上一个镜像 tag 一条路。
- compose 为 ADB 调试桥补回环端口段映射（15655-15664）：桥接监听随服务进入容器后，
  运维的 SSH 隧道需要这段映射才能到达。

- 根目录 `LICENSE`：此前许可证缺位（按默认版权处理），现以专有声明显式登记。
- Docker 构建资产（`Dockerfile`、`compose.yaml`、`.dockerignore`）入库：此前仅存在于本机工作区，
  `docker.yml` 工作流在远端按可复用流程构建时缺少 Dockerfile。
- 持续集成新增安全门禁：gitleaks 秘密扫描（全历史）与 pip-audit 依赖漏洞扫描
  （OSV，高危未修复即红灯），对应执行矩阵两项"待建立"转"已执行"。
- compose 增加 `migrate` 一次性服务：业务容器启动前先 `alembic upgrade head`，迁移
  失败业务容器不拉起；新增 `tests/test_migrations.py` 看守模型与迁移的一致性
  （`alembic check` 无差异），改模型忘生成迁移在门禁里当场红。
- Dependabot 依赖更新（pip / gradle / github-actions，每周）：patch 与 minor 升级在
  CI 全绿后自动 squash 合并，major 与非 semver 升级留人工审阅；设备 ROM 冻结
  Android 4.4（API 19），okhttp 与 conscrypt-android 的升级上限以 ignore 规则钉死。

### Changed

- TLS 策略（纯 AEAD）由服务器全局 nginx 配置移入仓库 `deploy/nginx/ntu-shower.conf` 的
  server 块，部署资产恢复对安全关键配置的唯一事实源地位。
- GitHub Actions 全部按完整 commit SHA 固定（此前按主版本 tag）。
- 设备 OTA 进度帧（`ota.progress`）服务端开始接收并记日志：协议此前只有 Schema 没有
  分支，设备的每帧进度都会收到 `proto.unknown_type` 错误帧。
- 服务端代码完成 ruff 0.16.9 修复与 `StrEnum` 迁移；`main` 上的质量门恢复全绿
  （本地曾三处红灯：lint 27 项、禁词 2 处、覆盖率 95.91%）。
- ffmpeg/ffprobe 改为静态二进制内置（wader/static-ffmpeg，按 manifest digest 钉版，
  含 libmp3lame），替代 apt 全量编解码库：镜像 222.0MB → 166.5MB，最大层 164.1MB →
  108.5MB，跨境推送冷启动时长显著下降；构建时以生产同参数实转音频作冒烟门禁。

### Fixed

- 修复 CI 依赖解析失败：`jsonschema==4.26.0` 与 `semgrep==1.184.0`（要求 `~=4.25.1`）
  冲突，Linux 上 pip 无法安装 server 依赖集；版本降至 4.25.1。
- 修复测试对 caplog 的顺序偶发：应用 logger 生产态关闭冒泡（避免与 uvicorn 重复输出），
  caplog 靠冒泡收记录——同一 worker 里是否先跑过 `client` 决定记录是否可见。
  测试夹具统一恢复冒泡，用例语义不再依赖执行顺序。
- 运维手册「表结构变更的落地」重写为 Alembic 流程：原步骤引用已删除的
  `tools/rebuild_derived` 与不存在的 `db.py` DDL，是 v1 残留。
- 修复后台任务跨线程使用 ORM Session 导致的 SQLite access violation：楼层状态汇总
  在线程池里读库，但会话建在事件循环线程上，两条线程并发使用同一连接，进程直接
  段错误——pytest 并行时的「worker crashed」即此。会话边界改归任务自己：会话在
  哪个线程里生就在哪个线程里关（`app/core/background.py`）；CI 的 pytest 不再用
  `-n 0` 串行规避。
- 修复镜像运行时工作目录：`uvicorn app.main:app` 在容器内无法解析 `app` 包
  （systemd 时代的 `WorkingDirectory` 依赖被带进了 Dockerfile），CI 只构建不运行故未暴露；
  同批把 ffmpeg/ffprobe 纳入镜像（语音素材转码依赖，slim 基镜像不带）。
- 网关对接收方不认识的时段格式坏的语音规则行，跳过并留痕，不再中断整帧投递
  （此前非数组形状的坏行会以 TypeError 冒泡）。
- 清理指向已废弃 `server-config` 项目的全部引用（两份文档横幅与两个 systemd 单元的
  `Documentation=`），以及 detekt 配置与构建脚本中两处不存在的小节编号。
- 运维手册拓扑表与部署说明对齐 FHS 迁移后的实际路径（配置 `/etc/ntu-shower`、
  数据 `/var/lib/ntu-shower`）；实时推送路径在架构与参考文档中更正为 `/api/v1/stream`。
- 修复桥接测试的并行帧串位：xdist 多 worker 共用固定桥端口，A worker 的 TCP 客户端
  会连进 B worker 的桥，表现为 nonce 随机错乱——端口按 worker 分段后，
  `pytest -n auto` 在两个 pytest 版本下各 5 轮全绿（此前 9.1.1 与 9.2.0 均随机失败）。

### Removed

- 删除 `docs/glossary.md`：源自已删除的自创术语扫描器，无执行机制，且代码仍引用其已不存在的 `CANONICAL`。
- 删除 `docs/testing.md`：通用条款与自创的 R0-R3 变更分级，与 `CONTRIBUTING.md` 的质量门重复。
- `docs/runbook.md` 删除「发布与部署规则」「运行与维护规则」两章：已删除的自创工程标准残页，
  其中「备份必须加密」与 ADR-0018 直接冲突。
- `docs/reference.md` 的字段表、端点表与典型流程移出：可从 `server/app/models/`、
  `/openapi.json` 与代码读出；字段的特殊语义改写入模型字段注释。
- 清理代码与配置中指向已删除文档的引用（`STANDARDS.md`、`docs/DECISIONS.md`、
  `docs/runbook.md` 的章节号）。

## [0.6.5] - 2026-11-04

### Changed

- 设备端内置 Conscrypt（BoringSSL）：API 19 系统 TLS 栈实测无任何 AEAD 套件，
  仅靠系统栈无法与纯 AEAD 服务端握手。APK 现在提供 TLS1.2 AES-GCM/CHACHA20，
  服务端同步收敛为纯 AEAD，不再提供 CBC 套件。

### Fixed

- 修复设备离线期间喷头状态变化数据丢失：采集入队此前被错误地绑定在连接状态上，
  未连接（`trusted=false`）时喷头变化在写入发件箱前就被丢弃。现改为无论连接与否都
  采集入队，未连接时帧留在本地发件箱，重连后按序补传，断网不再丢历史原始数据。

## [0.6.3] - 2026-10-30

### Added

- 新增 `STANDARDS.md`：覆盖生命周期、文档、代码、文本、审阅、发布、运行、安全与 AI 协作的工程标准。
- 新增 `CONTRIBUTING.md`：开发环境、质量门、分支、提交、审阅与发布准备步骤。
- 新增 `docs/security.md` 与 `docs/testing.md`：安全控制与测试策略的所有者文档。

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
