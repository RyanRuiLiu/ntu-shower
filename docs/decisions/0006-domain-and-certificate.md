# 0006. 域名与证书

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

需要稳定的网络锚点。

## 考虑过的选项

* 自签证书
* 单张 ECDSA 通配符证书

## 决策结果

域名 ntu-shower.iseekdream.com，单张 ECDSA 通配符证书。

### 正面后果

* 4.4 设备实测支持 ECDHE-ECDSA，旧系统为迁就 4.4 维护的 RSA 证书不再需要

### 负面后果

* 证书续期依赖 Let's Encrypt

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
