# 0029. 密码哈希用 bcrypt 官方库

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

passlib 1.7.4 与 bcrypt 4.1 以上不兼容（业界已知）。

## 考虑过的选项

* passlib
* bcrypt 官方库直接用

## 决策结果

core/security 用 bcrypt.hashpw/checkpw。

### 正面后果

* 不背历史包袱

### 负面后果

* 无

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
