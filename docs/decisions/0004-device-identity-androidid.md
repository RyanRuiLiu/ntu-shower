# 0004. 设备入网身份：ANDROID_ID 加后台人工信任

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

设备必须能自标识且重装 APK 不丢失身份。

## 考虑过的选项

* 配对码（屏上输一次码）
* ANDROID_ID 加后台点信任
* 预烧录凭据

## 决策结果

device_id = ANDROID_ID；后台人工点信任才通信。重装不变，恢复出厂等于重新认领一次。

### 正面后果

* 零现场零输入，无窗口期风险

### 负面后果

* device_id 即身份无第二凭据，伪造需物理读取目标设备（自有设备，接受）

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
