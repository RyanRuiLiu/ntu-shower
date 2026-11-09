#!/bin/sh
# 项目质量门的唯一本地入口。与 .github/workflows/ci.yml、CONTRIBUTING「质量门」逐条对应。
# 用法：sh scripts/check.sh
set -eu
cd "$(dirname "$0")/../server"
.venv/Scripts/python.exe -m ruff format --check app tests
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m mypy app --strict
./.venv/Scripts/lint-imports.exe
.venv/Scripts/semgrep.exe scan --config p/python --config p/security-audit app --error --quiet
.venv/Scripts/python.exe -m pytest -q --cov=app --cov-report=term-missing
( cd .. && npx --yes markdownlint-cli2@0.20.0 "*.md" "docs/*.md" --config .markdownlint.json )
