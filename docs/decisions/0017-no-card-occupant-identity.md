# 0017. 无卡模式占用者标识

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

真机实测 cardno=0（无卡模式），占用者在 certno/accno 字段。

## 考虑过的选项

* 以 cardno 为准
* cardno/accno 组合判定占用

## 决策结果

占用判定为 cardno 不为 0 或 accno 非空非 0；status 字段不可靠。

### 正面后果

* 无卡模式状态派生正确

### 负面后果

* 需持续实测验证字段语义

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
