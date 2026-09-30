# 0020. OTA 实时推送与 force 调试发布

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

用户要求只让某台设备更新、及时更新、调试期反复推同一版本。

## 考虑过的选项

* 只靠轮询
* 发布即向在线目标设备推送 ota.upgrade_available；force 定向发布无视版本号

## 决策结果

推送加轮询双通道；target 支持逗号分隔定向列表。

### 正面后果

* 调试迭代快，离线设备靠轮询自然补齐

### 负面后果

* 离线设备收不到即时通知（设计如此）

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
