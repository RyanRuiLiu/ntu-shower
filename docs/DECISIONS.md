# ntu-shower 决策记录

本文件是唯一一份决策记录。原 `docs/DECISIONS.md` 下的 40 份 ADR 已合并到此，编号不再维护：
被后续决策取代的部分只保留当前有效的结论，一次性过程不入档。

每条写"现在约束代码的是什么"与"为什么"。协议字段、配置键、错误类型、动作名的具体值与说明
在代码与 `protocol/schema/` 里，此处不复述。

## 总体取向

- **措辞禁令见 `AGENTS.md`**：被禁的那几个词描述的不是文风，而是一种设计——假定主方案不完整，
  再补一条次等的。本项目取向相反：每条通路各自完整、按场景选。由
  `server/tests/test_language_rules.py` 强制，豁免位置登记在该文件内，新增豁免必须写明理由。
- **不写兼容代码与数据迁移**：项目未上线，数据库直接删表重建，schema 只有最初版本。
  结构变更必须同步改调用方；收益是仓库里不存在只在旧数据上成立的暗角。
- **不留无用物**，但"当前无人读取"不等于无用：`usage_session`、`door_state_event`、
  `command_audit` 是统计口径的原始数据，删了不可复得，必须先攒。
- **一改一验**：改完立刻跑门禁，不攒一批再验。命令超过 10 秒说明有问题——测试有 5 秒单例超时，
  超了就是它在等一个永不到来的事件。

## 架构与模块

- **设备端定性为寄生代理**，只做三件主功能：读浴室 App 数据库的 `t_doorlist`、经无障碍服务操作
  其界面、维持自身存活（自更新/看门狗/崩溃自启）。浴室 App 无导出组件、`uiautomator` 永不空闲
  且自身会崩，替代它没有路径；把主功能定死是为了挡住功能蔓延，其余全部算扩展。
- **服务端是 FastAPI 模块化单体，单进程单事件循环**。I/O 密集的长连接聚合在单事件循环下模型
  最简单；代价是垂直扩展受限，需要拆分时按事件总线接口换实现，业务代码不动。
- **按领域拆模块，各模块自带 `router` 与 `ops_router`**。`/ops` 只是前缀与鉴权依赖，不是
  一处集中的管理面；设备、配置、OTA、会话、影子、身份、桥接各自持有自己的路由，
  新增功能有确定落位。
- **网关平面与领域平面分离**：`gateway/` 只负责设备 WS 长连接的生命周期与分发，业务状态由
  `modules/` 各自持有并落库。
- **实时推送走进程内事件总线**（`core/events.py`）。单进程下零额外依赖；接口按可替换为
  Redis pub/sub 的形状设计，拆分进程时才需要动它。
- **设备影子（`modules/shadow`）是扩展功能的统一读写入口**，reported / desired / metadata 三段，
  对齐 AWS IoT Device Shadow。扩展只面向影子编程，不直接查表。
- **同一 APK 装所有设备，设备档案集中在服务端 `devices` 表**。新设备零构建成本，OTA 只维护
  一个版本；代价是设备身份与档案都在服务端。

## 协议与契约

- **协议承载在 WSS 上，载荷是 JSON 信封**，类型名点分（`link.hello`、`session.command`）。
  设备量级下 protobuf/MQTT 的复杂度换不来收益，而抓包即读、schema 即文档。
- **字段与类型全套规范化**：字段 `snake_case`、RFC3339 时间、UUIDv4 消息 id、每个 type 一份
  JSON Schema。三端同源无歧义，新增可选字段不升版本号。
- **信封只有一处组装**：服务端 `core/protocol.py`、设备端 `MessageCodec`。所有出站帧经它们
  构造，协议格式改动只改一处；错误帧是扁平的 `{code,message}`。
- **JSON Schema 是协议的唯一事实源**，存 `protocol/schema/`（含 `envelope.json`），CI 按样例
  校验。改协议必须三处同步：schema、服务端实现、设备端实现。
- **HTTP 错误统一为 RFC 9457 Problem Details**：`application/problem+json` 的
  `{type,title,status,detail}`，由 `core/errors.py` 装配（含 422 校验错误）。`type` 是 kebab-case
  的稳定 code，登记在 `core/problems.py` 且一经发布不改；`title` 是中文展示文案、`detail` 承载
  可变信息。客户端按 `type` 分支，不解析自由文本，也不在前端另维护一层文案映射。
- **设备在 `link.hello` 声明自己支持的 action 集合，服务端下发前比对**，只在声明的范围内发命令。
  空数组表示未声明（升级前的旧版本），此时不拦截——否则升级服务端会把在网设备锁死。
- **配置经 `link.hello_ack` 与 `config.update` 下发合并后的有效配置，未知键设备忽略**。
  新增配置键不影响旧设备，靠"忽略不认识的键"实现。
- **喷头状态是受约束的枚举而非裸字符串**：`DoorState`（`modules/doors/schemas.py`）由设备上报的
  4 个原始整数派生，定义成 `str, Enum` 让 OpenAPI 生成 `enum`，客户端据此生成联合类型，
  不必手抄一份状态清单。
- **动作名与失败原因码是封闭集合**，登记在 `core/actions.py` 与 `core/reason_codes.py`，
  各有一份测试对账。两端各写一份字符串迟早会对不上，对不上时出错的是现场设备。

## 设备端

- **设备身份 = `ANDROID_ID`，服务端人工点信任后才通信**。重装 APK 身份不变，恢复出厂等于重新
  认领一次——现场零输入、无配对窗口期。代价是 device_id 即身份、没有第二凭据；设备自有，
  伪造需物理接触目标设备，接受。
- **设备零界面、零本地配置**：无 Activity、无桌面图标、无最近任务，一切运行参数由服务端下发。
  现场没有可误配的操作面；代价是设备完全依赖服务端可用性。
- **APK 内置 DNS 解析器，以域名为唯一网络锚点**。设备系统 DNS（8.8.8.8）被校园网封锁，
  114.114.114.114 的 UDP/TCP 53 实测可用；因此不缓存解析结果、不写构建期 IP。
  换服务器只需改 DNS 记录，零现场。
- **TLS 按设备实测能力最小配置**：设备上限 TLSv1.2、可用套件是 ECDHE-ECDSA/RSA 的 CBC 系列，
  因此强制 TLSv1.2 并在 APK 内打包 ISRG 根证书。OkHttp 默认白名单里的 ECDHE-ECDSA 只有 GCM，
  对本设备必然握手失败——`transport/GatewayClient.kt` 显式放行 CBC 套件。
- **占用判定用 `cardno` 与 `accno` 组合**：`cardno != 0` 或 `accno` 非空非 0 即占用，
  `status` 字段实测不可靠。无卡模式（`cardno=0`）的占用者落在 `certno`/`accno`，
  只看 cardno 会漏判。
- **`FileObserver` 掩码必须含 `MODIFY`，事件按 1 秒防抖合并**。浴室 App 长持数据库 fd 不关闭，
  事务写入只触发 MODIFY、不触发 `CLOSE_WRITE`；只监听后者的实现会静默漏报，代价是 1 秒延迟。
- **快照里的设备时间落库前校准为服务器时钟**：周期 ping/pong 测偏差并做 EMA。会话时长要跨端
  可比，信任设备时钟会让结算时序失真；设备离线期间偏差值停在最后一次。
- **崩溃守护是参数化的扩展功能**，不是地基：开关、冷却、阈值经配置下发。浴室 App 会自崩、
  需要拉起，但参数写死在设备端就无法按现场调整。
- **构建工具链能新则新，运行时冻结在 4.4**：Gradle 9.8 + AGP 9.4 + 内置 Kotlin + JDK 25，
  `minSdk`/`targetSdk` 19 不随工具链上升。设备 ROM 无升级路径，但构建侧没有理由停旧；
  代价是 lint 长期豁免 `ExpiredTargetSdkVersion`。

## 身份与能力

- **访问控制按接口面分三档，不设角色**：查看类无需凭据；操作类需 `X-Identity` 标识 + 能力位；
  管理面需 `X-Ops-Token`。权限本身就是能力位，不存在"这个人是什么角色"这一层，也就没有
  按角色下发不同内容的分支。早期的三级角色模型（只读/用户/管理员）已被能力位取代。
- **能力位是 `ops`（分配/结算）与 `photo`（拍照/截图）**，授予记录存数据库、后台可增删，
  撤销即从名单删除。名单里的标识按来源分 `openid`（小程序）与 `web`（我方签发）两种，
  同一种资源、同一份名单。
- **小程序登录返回 openid 本身，不另发会话令牌**：`wx.login` 的 code 换 openid，openid 由微信
  签发且稳定，再包一层只会引入过期与重登。网页标识是管理员签发的不可枚举随机串，本身即身份。
- **不建用户登录体系**：`users` 表、JWT 签发、令牌端点一律不引入。浴室账号密码不落库，
  随操作请求直传设备、由浴室系统验证——真正的凭证校验在学校那侧，自建登录对用户是多余步骤，
  也少了用户数据这份攻击面。
- **密码哈希直接用 `bcrypt` 官方库**（`hashpw`/`checkpw`），不经 `passlib` 包装。
  `passlib` 1.7.4 与 bcrypt 4.1+ 不兼容是已知问题，直接调官方接口没有这层维护成本。
- **资源约束按真实功能设计，不用通用 QPS 限流**：同一设备的第二条在途命令直接 409
  `device.busy`，不排队；OTA 上传体积上限入配置。这样把正确性与资源保护合成一件事。

## 远程配置

- **配置键登记在 `server/app/modules/config/spec.py`**：每个键的类型、范围、单位、归属
  （device/server）与说明都在那里。新增键只改这一处，服务端校验、客户端渲染、接口文档都从它
  派生。
- **只存与默认值不同的项**，`effective` 每次按 `默认 ← 全局 ← 设备` 现算。存合并快照会把当时
  的默认值固化进设备覆盖，此后改全局对已有覆盖的设备完全无效——生产上真实出现过两台设备读
  同一份配置却行为不同，且没有任何提示。
- **入参校验拒绝未知键、类型不符与越界值**（422 `invalid-config`），不再静默丢弃。
  整数校验显式排除 `bool`——它是 `int` 的子类，不排除会让 `true` 混过数值范围检查。
- **`GET /ops/config/spec` 暴露登记表**，客户端据此渲染控件，不各自硬编码键表；两端键集合由
  `test_config_spec.py` 解析 Kotlin 源码对账，防单边漂移。
- **设备端只做失控下限保护，不重复服务端的业务范围**：0 或负的时长会让 `postDelayed` 立即
  触发，一律抬到至少 1。两处各写一份业务范围必然漂移，数值边界只由登记表定义。

## OTA 与版本

- **版本号单一来源是 `server/app/core/version.py` 的 `APP_VERSION`**，与 `server/pyproject.toml`
  的 `version`、`android/app/build.gradle.kts` 的 `versionName` 三处必须一致，由
  `tests/test_version_consistency.py` 强制校验。OTA 覆盖安装要求版本可比，人工同步必然出错。
- **APK 用正式 RSA 2048 证书一步到位签名**（别名与密码 `ntu-shower`，有效期至 2056），
  不用调试签名过渡。自更新要求新旧 APK 签名一致，调试签名一旦上机就切不回来。
  `config/keystore/` 是全系统唯一长期绑定物件，丢失意味着所有在网设备都要物理接触重装，
  必须异地备份并校验指纹。
- **自更新链路全自动**：设备按配置间隔轮询 `/ota/manifest`，下载后校验 sha256 再 `pm install`，
  覆盖安装由 `MY_PACKAGE_REPLACED` 广播自启（该广播在 stopped 状态也送达，真机验证过）。
  升级零现场。
- **发布是推送加轮询双通道**：发布即向在线目标设备推 `ota.upgrade_available`，离线设备靠轮询
  自然补齐；`target` 支持逗号分隔的定向列表。调试期 `force` 发布无视版本号，只更新指定设备。
- **安装状态在服务端跟踪**：设备 `link.hello` 里的 `version_code` 即安装确认，
  `ota_release_install` 表记录到该版本为止；已确认的 `force` 发布不再推，重推同一版本需先调
  `POST /ops/releases/{version}/rearm/{device_id}`，`DELETE /ops/releases/{version}` 用于
  紧急下架。缺少这层跟踪会让 force 发布与设备重启构成无限重装循环——真机发生过一次。
- **发布按个数保留（`ota_release_keep`，默认 5）**：新版登记时裁最旧的，注册行与 APK
  文件一起删。留的是回滚点而不是历史档案——盘上的旧 APK 只有体积没有意义；N=1 会让
  "新版有问题退回上一版"无处可退。
- **删除接口连文件一起删**：`DELETE /ops/releases` 只删注册行会让 APK 变成无人认领的
  孤儿文件，"下架"的语义必须覆盖到盘上那份。

## 可观测性

> 标准依据：OWASP Logging Cheat Sheet（记什么、不记什么）、NIST SP 800-92
> （审计记录的生成/保护/保留）、W3C Trace Context（关联标识）、OpenTelemetry
> Log Data Model（字段命名）。2026-10-01 采纳。

- **三层痕迹，一条链**：`api_call`（HTTP/网关审计）→ `command_audit`（命令级审计）
  → 设备端 `audit.log`（命令痕迹 JSONL），以 `command_id` 与 `trace_id` 贯通——
  "这个命令服务端发给谁、设备收到没有、结果是什么"在三个层面各有一行可查。
- **审计记什么**：全部 /api、/ops、/ota 请求（认证失败、授权失败、参数不合法、
  服务端错误天然落表，OWASP 的必记事件类）；设备网关的连接与断开。**不记**请求体
  与响应体（学号密码、令牌、业务参数一律不落库），不记静态资源与 /healthz。
- **令牌与标识脱敏是唯一实现**：`core.audit.mask_actor`——令牌只落 sha256 指纹
  （前 16 位十六进制），身份标识按"前4后4"脱敏、短标识整体替换。审计与日志共用
  这一份，禁止各写一份。
- **stdout 日志是 JSON 事件流**（12-Factor §XI）：一行一个对象，字段照 OTel
  Log Data Model（`ts`/`level`/`logger`/`message`/`event`），`trace_id`/
  `request_id` 有值才输出。`event` 是给机器检索的稳定代号（kebab-case），与
  中文 `message` 分开——改文案不能破坏检索；属性只收标量。不写文件、不做轮转，
  归档是运行环境的职责。
- **HTTP 与命令的关联**：W3C `traceparent`（调用方带就沿用其 trace-id，否则生成），
  响应回 `X-Request-ID`；问题响应经 `X-Problem-Type` 头把 RFC 9457 type 带进审计行。
  命令派发时把 trace_id 写进 `command_audit`——跨层查一次 SQL 就是全链路。
- **保留期执行而不是声明**：`api_call` 按 `audit_retention_days`（默认 90 天）由
  每日任务删除；OTA 发布按 `ota_release_keep`（默认 5 个）在新版登记时裁剪。
  设备端落盘文件全部有界：crash.log / automation.log / audit.log 256KB 封顶、
  ota-install.log 每次安装截断、OTA 下载包装完即删、outbox 按行数上限裁剪、
  shell pid 文件正常完成与超时两条路径都自清。
- **设备端痕迹是文件而不是 Logcat**：Logcat 环形缓冲重启即空，而"命令收到没有、
  回了什么"是远程排障的第一直觉；`/sdcard/ntu-shower/audit.log` 用 `device.exec`
  直接 `cat` 取回。

## 测试与质量

- **门禁是机器强制，不靠自觉**：服务端 `ruff format` + `ruff check` + `mypy --strict` + `pytest`；
  设备端 `detekt` + JUnit 单测 + JaCoCo 覆盖率门。本地跑绿即 CI 跑绿，两侧命令完全一致。
- **两端都设覆盖率门，并配赦免清单**：能抽成纯逻辑覆盖的必须覆盖，只有真正无法在 JVM 上覆盖
  的类才排除。设备端豁免登记在 `android/app/build.gradle.kts` 的 `frameworkBoundClasses`，
  逐条带理由；门槛只升不降，加豁免要解释为什么抽不出纯逻辑。
- **覆盖率与测试数不写进文档**，由 CI 实时产出。文档里写死的数字必然过期。
- **结构约束也进测试**，而不只测行为：协议 schema、动作名集合、失败原因码、配置键对账、
  静态挂载顺序、版本一致性都有对应的执行机制。约束只写在文档里，等于没有。

## 部署与入口

- **443 统一入口，nginx 按路径分流**，服务器只暴露 80/443/22。证书一张、防火墙规则最少；
  代价是 nginx 进入关键路径。
- **域名 `ntu-shower.iseekdream.com`，单张 ECDSA 通配符证书**。4.4 设备实测支持 ECDHE-ECDSA，
  旧系统为迁就 4.4 维护的 RSA 证书不再需要；续期依赖 Let's Encrypt。
- **网页端是零构建静态文件**（`server/web/`），由服务端本身托管在根路径，合并了旧系统的调试
  面板与画面查看器。改完刷新即可，无编译步骤、无框架版本约束、与 API 同源无跨域。
  曾试过 Taro + React 的跨端方案，两个页面的规模撑不起它的接入成本，工程已删除；
  服务端契约为任何前端保留。
- **`mount("/")` 必须在所有路由之后注册**：Starlette 按注册顺序匹配，提前挂载会让 `/api`、
  `/ops`、`/ota`、`/gateway` 全部变成静态文件查找并返回 404。顺序由 `test_e2e_branches.py`
  锁定。
- **前端资源响应带 `Cache-Control: no-cache`**（`.html`/`.js`/`.css`）：浏览器每次都带 ETag
  回来验，未变即 304。没有它时只有 `Last-Modified`，浏览器会走启发式缓存，改完样式刷新看到的
  还是旧的那一份。
- **真实 ADB 经 443 网关透传，零新端口、零新进程**：管理面
  `POST /ops/devices/{id}/bridge/open` 下发含一次性 nonce 的 `bridge.open`，设备以 root 打开
  adbd 并另开一条二进制 WSS 与本地 adbd 互泵字节流；服务端 nonce 配对后绑 `127.0.0.1:<port>`
  （15655 起，避开旧系统的 15555），开发者经 SSH 转发后 `adb connect`。保留真实 adb 的完整
  能力（shell/install/dumpsys），不用轻量替代。
- **桥的约束**：仅管理面可触发、一次性 nonce、仅绑回环、TCP 断开不拆桥（回到等待下一个连接）、
  空闲 300 秒回收。依据是 Root Bridge 事件（裸 ADB 暴露公网导致大规模失陷）：adb 字节流全程
  在 WSS/TLS 内，不对外网开放任何端口。
- **旧系统组件在线共存是显式决定**：`com.ntu.remoteshower.*` 与旧服务端并行运行，两套系统的库、
  单元、站点、端口完全分开，本项目不读写不依赖旧系统任何资源。卸载由人手动执行，不写自动
  卸载脚本，也不在部署流程里代为清理。
