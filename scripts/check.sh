#!/bin/sh
# 项目质量门的唯一本地入口。与 .github/workflows/ci.yml、CONTRIBUTING「质量门」逐条对应。
# 用法：sh scripts/check.sh
set -eu
cd "$(dirname "$0")/../server"
.venv/Scripts/python.exe -m ruff format --check app tests
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m mypy app --strict
./.venv/Scripts/lint-imports.exe
# semgrep 在 pre-commit 的隔离环境里跑（见 server/requirements-dev.txt 的说明）：
# 两段分别对应 SAST 规则集与项目自有规则，与 CI 的 semgrep job 逐条一致。
.venv/Scripts/pre-commit.exe run semgrep-sast --hook-stage manual
.venv/Scripts/pre-commit.exe run semgrep-project --hook-stage manual
.venv/Scripts/python.exe -m pytest -q --cov=app --cov-report=term-missing
# 网页端门禁由 pnpm 执行：新版（React/TS）走 tsc + eslint + vitest，构建在 docker
# 镜像里跑。旧版（web-legacy/，零构建 JS）走 eslint no-undef + stylelint CSS 令牌
# 纪律——检查写了没人跑等于没有（2026-11-10 漏导入事故的教训）；两条都从 server/
# 起跑，因为 ESLint v10 要求 CLI 目标落在 config 所在目录之内，从 web/ 指向兄弟
# 目录会被整体忽略。与 CI 的 web job 逐条一致。
( cd web && pnpm exec tsc -b && pnpm exec eslint src --config eslint.config.mjs && pnpm exec vitest run )
node web/node_modules/eslint/bin/eslint.js --config web-legacy/eslint.config.mjs web-legacy
node web/node_modules/stylelint/bin/stylelint.mjs "web-legacy/style/*.css" --config web-legacy/.stylelintrc.json
( cd .. && npx --yes markdownlint-cli2@0.20.0 "*.md" "docs/*.md" "docs/adr/*.md" "deploy/*.md" ".github/*.md" --config .markdownlint.json )
