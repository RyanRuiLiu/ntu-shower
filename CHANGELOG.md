# Changelog

本文件从 0.6.2 起按 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) 维护。变更分类固定为 Added、Changed、Deprecated、Removed、Fixed、Security。版本号采用 [Semantic Versioning 2.0.0](https://semver.org/)。

> 版本核对（2026-11-05）：`0.6.4` 从未存在于任何源码树或安装包中——版本号由
> 0.6.3 直接升到 0.6.5（提交 d9e29d2），该节原为一次记档性条目。其内容已并入
> 下方 0.6.5 的 Fixed；不保留一个没有对应版本状态的版本号。

## [Unreleased]

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
