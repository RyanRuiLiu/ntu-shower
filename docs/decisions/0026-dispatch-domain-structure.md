# 0026. 服务端结构对齐 Dispatch 领域结构

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

admin 混合体（设备、OTA、配置、调试 163 行）违反领域内聚。

## 考虑过的选项

* 集中式 admin
* 按领域拆分，各模块自带 admin_router 加前缀加守卫

## 决策结果

devices/config/ota/sessions/shadow/auth 各归其位；/admin 只是前缀加鉴权依赖。

### 正面后果

* 单一职责，新功能有明确落位

### 负面后果

* 文件数变多（行业常态）

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
