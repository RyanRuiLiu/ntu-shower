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
# 必须逐 id 指定：不指定即连 project-checks 一起跑，而它调用的就是本脚本——递归。
.venv/Scripts/pre-commit.exe run semgrep-sast --hook-stage manual
.venv/Scripts/pre-commit.exe run semgrep-project --hook-stage manual
.venv/Scripts/python.exe -m pytest -q --cov=app --cov-report=term-missing
# 网页端门禁：eslint no-undef（漏导入即红——2026-11-10 生产事故的教训）+ stylelint
# CSS 令牌纪律。零构建前端，工具链自持（devDependencies 仅 eslint/stylelint，npm ci）；
# 从 web/ 目录内跑，ESLint v10 要求 CLI 目标落在 config 所在目录之内。与 CI 的 web
# job 逐条一致。
( cd web && npx eslint . --config eslint.config.mjs && npx stylelint "style/*.css" --config .stylelintrc.json )
( cd .. && npx --yes markdownlint-cli2@0.20.0 "*.md" "docs/*.md" "docs/adr/*.md" "deploy/*.md" ".github/*.md" --config .markdownlint.json )
