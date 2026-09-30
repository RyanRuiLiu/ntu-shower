# 0003. 多设备形态：同一 APK 加服务端设备注册表

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

项目需要支持任意数量的浴室设备。

## 考虑过的选项

* 每设备单独构建 APK
* 同一 APK 加服务端设备注册表管理

## 决策结果

同一 APK 装到所有设备，服务端 devices 表注册管理。

### 正面后果

* 新设备零构建成本，OTA 一套版本

### 负面后果

* 设备档案数据集中在服务端

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
