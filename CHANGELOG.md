# Changelog

本文件从 0.6.2 起按 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) 维护。变更分类固定为 Added、Changed、Deprecated、Removed、Fixed、Security。版本号采用 [Semantic Versioning 2.0.0](https://semver.org/)。

> 版本核对（2026-11-05）：`0.6.4` 从未存在于任何源码树或安装包中——版本号由
> 0.6.3 直接升到 0.6.5（提交 d9e29d2），该节原为一次记档性条目。其内容已并入
> 下方 0.6.5 的 Fixed；不保留一个没有对应版本状态的版本号。

## [Unreleased]

### Changed

- main 分支保护上线：五个门禁为 required checks、线性历史强制、管理员不豁免（应急旁路保留）；android 门禁按 PR 变更路径裁剪，纯服务端改动不再等待安卓全量构建（push main 仍全量）。
- 基镜像按 digest 钉版（python 构建两级与 litestream），tag 保留供人读；升版即换 digest 走评审。

### Added

- ADR-0022：安卓依赖冻结 API 19 兼容线的裁定、备选方案与重启条件（useAndroidX=false、okhttp 3.12.x、conscrypt 2.5.x、dependabot ignore 编码上限）。

## [0.7.0] - 2026-11-13

### Added

- 服务端切换为 Docker Compose 部署：镜像由 CI 构建并双推 GHCR/ACR，服务器 `docker pull` +
  `compose up -d`；容器以非 root（UID 997）运行，根文件系统只读、能力全丢弃、端口仅绑回环。
  旧 systemd 单元与 venv 冻结保留作应急回滚；数据库备份仍在宿主机 timer 上执行。
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

- 服务端切换为 Docker Compose 部署：镜像由 CI 构建并双推 GHCR/ACR，服务器 `docker pull` +
  `compose up -d`；容器以非 root（UID 997）运行，根文件系统只读、能力全丢弃、端口仅绑回环。
  旧 systemd 单元与 venv 冻结保留作应急回滚；数据库备份仍在宿主机 timer 上执行。
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
