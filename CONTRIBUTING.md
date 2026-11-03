# 贡献指南

本文件规定开发环境、质量门、提交、审阅与发布准备。工程规则的所有者是 [`STANDARDS.md`](STANDARDS.md)；系统事实的所有者见该文件 §9.3。本文件只写步骤与命令。

## 适用读者

- 修改服务端、设备端、网页端、协议或部署资产的开发者。
- 执行代码审阅的审阅者。
- 准备发布与部署的运维责任人。

## 环境

| 工具 | 要求 |
|---|---|
| Python | 生产运行时 3.13；CI 使用 3.13 |
| JDK | 25；`android/app/build.gradle.kts` 的编译目标为 Java 22 |
| Android SDK | 与 `android/app/build.gradle.kts` 的编译版本一致 |
| Git | 支持 `core.autocrlf` 与 `.gitattributes` 的版本 |
| ffmpeg | 语音素材上传与转码需要 `ffmpeg` 与 `ffprobe`，并带 `libmp3lame` |

密钥、令牌、签名口令与生产配置不得写入仓库、脚本参数或文档。签名材料由密钥保管方提供。

## Git 设置

克隆后执行一次：

```bash
git config core.hooksPath .githooks
git config core.autocrlf input
git config pull.ff only
git config fetch.prune true
```

- `core.hooksPath` 启用 `.githooks/commit-msg`，按 `STANDARDS.md` §4.3 校验提交信息。
- `core.autocrlf input` 让工作区与索引都使用 LF，换行规则仍以 `.gitattributes` 为准。
- `pull.ff only` 禁止拉取时生成合并提交，历史保持线性。
- `fetch.prune true` 在拉取时清理远端已删除的分支。

合并请求由 CI 对基线之后的每个提交与 PR 标题运行同一钩子；本地钩子可被 `--no-verify` 绕过，CI 不会。

## 服务端开发

```bash
cd server
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
cp .env.example .env
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```

`server/.env` 只保存本地配置；生产 `.env` 由 systemd 注入，不进入仓库。本地开发库可以删除重建；生产库不得删除重建，数据变更按 `STANDARDS.md` §4.10 执行。

## 设备端开发

```bash
cd android
cp gradle.properties.example gradle.properties
./gradlew :app:assembleDebug
```

`gradle.properties` 含签名配置，已被 Git 忽略。发布构建使用：

```bash
./gradlew :app:assembleRelease
```

设备端最低 API 版本与目标 API 版本固定在 `android/app/build.gradle.kts`；不得为通过编译而提高 `minSdk`。

## 网页端开发

网页端是 `server/web/` 下的零构建静态文件，由服务端在根路径托管。修改后重启本地服务并刷新页面即可；不需要前端构建步骤。网页端的模块边界、网络访问入口与可访问性要求见 `STANDARDS.md` §7.4 与 §7.5。

## 质量门

在 `server/` 与 `android/` 目录分别执行以下命令。该清单与 `.github/workflows/ci.yml` 逐条对应；本地跑绿即 CI 跑绿。

### 服务端

```bash
cd server
ruff format --check app tests
ruff check app tests
mypy app --strict
pytest -q --cov=app --cov-report=term-missing
```

### 设备端

```bash
cd android
./gradlew detekt :app:lintDebug :app:testDebugUnitTest :app:jacocoUnitTestCoverageVerification
./gradlew assembleDebug
```

### 质量门的解释

- 格式、lint、类型、覆盖率门槛由工具配置定义；本文件不复制阈值。
- 单条测试有 5 秒超时；超过 5 秒的用例在等待一个不会到达的事件。
- 覆盖率与测试数量由 CI 输出；文档不写死。
- 文档登记、入链、标题、链接、不确定措辞与写死版本检查由 `server/tests/test_docs.py` 执行，包含在 pytest 门禁中。
- 任何命令超过 10 秒，先判断是否在等待网络、设备或未结束的事件，不得用增加等待时间掩盖。

## 分支与提交

`main` 始终可发布。R1 以上变更通过短生命周期分支与合并请求进入 `main`：

```text
<类型>/<kebab-case-slug>
```

类型与提交信息格式见 `STANDARDS.md` §4.3。摘要说明做了什么，正文说明为什么与影响；破坏性变更使用 `BREAKING CHANGE:` 页脚。

合并请求必须包含：

- 变更与原因。
- 验证方式与结果。
- 风险级别与影响面。
- 回滚点或还原方案。
- 文档与变更记录更新。

提交前执行自审；合并前完成逐行审阅。审阅范围、严重级别与放行规则见 `STANDARDS.md` §16。作者不得把未解决的 S1 或 S2 标记为已解决。

## 审阅

审阅者从第一行读到最后一行，不以搜索结果代替全文。项目级审阅覆盖全部受版本控制的文本文件；生成物只读头部与差异；二进制只核对来源、校验和与许可证。

每个发现记录位置、规则、影响与修正方案。修复后重新阅读修改行与直接上下文，并运行相关门禁。同一类问题在全仓排查，不只修指出的那一处。

## 发布准备

发布准备在本地完成，生产部署由 `docs/RUNBOOK.md` 规定的步骤执行。

1. 更新版本号；三处版本号的一致性由测试强制。
2. 更新 `CHANGELOG.md`；破坏性变更单列并给出升级步骤。
3. 运行全量质量门。
4. 构建发布物并校验校验和与签名。
5. 按 `docs/RUNBOOK.md` 执行部署、部署后验证与回滚准备。
6. 创建带注释的 `v<版本>` 标签并推送；用 `CHANGELOG.md` 对应章节创建 GitHub Release。

发布准备步骤属于本文件；生产部署与回滚步骤属于 `docs/RUNBOOK.md`。两者不得互相复制命令。

## 文档

修改行为、接口、配置、数据或运维步骤时，同一变更内更新对应所有者文档：

| 变更 | 更新位置 |
|---|---|
| 项目入口与导航 | `README.md` |
| 开发流程与质量门命令 | `CONTRIBUTING.md` |
| 工程规则 | `STANDARDS.md` |
| 系统结构与时间轴定义 | `docs/ARCHITECTURE.md` |
| 数据模型、字段与接口索引 | `docs/REFERENCE.md` |
| 部署、回滚、备份与故障处置 | `docs/RUNBOOK.md` |
| 资产、威胁模型与安全控制 | `docs/SECURITY.md` |
| 测试策略与证据 | `docs/TESTING.md` |
| 决策理由与后果 | `docs/DECISIONS.md` |
| 发布历史 | `CHANGELOG.md` |

文档中的命令必须能执行；代码块必须有语言标记；示例必须标注示例。文档登记、入链、标题、链接与版本号检查按 `STANDARDS.md` §9.8 执行。

## 问题与故障

开发环境问题先查看 `server/.env`、依赖版本与数据库路径。生产故障按 `docs/RUNBOOK.md` 处置；生产机只读，不在生产机上进行测试、演练、重算或临时脚本。
