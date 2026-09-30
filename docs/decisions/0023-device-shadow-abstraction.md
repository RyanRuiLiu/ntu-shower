# 0023. 设备影子抽象

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

扩展功能需要统一的设备状态读写入口。

## 考虑过的选项

* 各功能直查表
* Device Shadow 模式：reported 加 desired 加 metadata

## 决策结果

对齐 AWS IoT Device Shadow 模式建 modules/shadow；预约等扩展只读写影子。

### 正面后果

* 行业标准抽象，扩展只面向影子编程

### 负面后果

* 多一层读模型维护

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
