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
# 网页 CSS 的令牌纪律由 stylelint 执行（官方规则 declaration-property-value-disallowed-list，
# 配置在 web/.stylelintrc.json；tokens.css 经 overrides 豁免）——与 CI 的 stylelint step 一致。
( cd .. && npx --yes stylelint@16 "server/web/style/*.css" --config server/web/.stylelintrc.json )
( cd .. && npx --yes markdownlint-cli2@0.20.0 "*.md" "docs/*.md" "docs/adr/*.md" "deploy/*.md" --config .markdownlint.json )
