# 0033. 版本与发布规范

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

SemVer 加 Conventional Commits 加 Keep a Changelog 是发布管理行业组合。

## 考虑过的选项

* 无版本规范
* SemVer 2.0 版本号加 CHANGELOG.md；git 仓库建立后启用提交规范

## 决策结果

版本号遵循 SemVer（0.x 阶段 MINOR 为功能 PATCH 为修复）；CHANGELOG.md 建立。

### 正面后果

* 版本含义有约定

### 负面后果

* git 仓库暂缓，提交规范未启用

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
