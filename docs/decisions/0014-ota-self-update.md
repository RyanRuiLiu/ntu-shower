# 0014. OTA 全自动自更新链路

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

调试迭代需要远程更新设备而不去现场。

## 考虑过的选项

* 分离 root 进程方案
* 轮询 manifest 下载 sha256 校验 pm install 加 MY_PACKAGE_REPLACED 自启

## 决策结果

设备按配置间隔轮询 /ota/manifest；MY_PACKAGE_REPLACED 在本机实测生效。

### 正面后果

* 0.1.0 到 0.2.0 真机全自动更新验证，后续升级零现场

### 负面后果

* 放弃分离 root 进程方案

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
