# 0030. 资源约束按真实功能设计

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

OWASP API Security Top 10 要求限制资源消耗，但通用登录限流不符合本项目形态。

## 考虑过的选项

* 通用 QPS 限流
* 按真实功能：每设备单在途命令加 OTA 上传 50MB 上限

## 决策结果

服务端拒绝同设备第二条在途命令（409 device.busy）而非排队；上传体积入配置。

### 正面后果

* 正确性与资源约束合一

### 负面后果

* 无

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
