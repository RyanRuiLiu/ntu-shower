# 0009. APK 签名一步到位

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

自更新要求新旧 APK 签名一致。

## 考虑过的选项

* 调试签名过渡
* 正式 RSA 2048 证书 30 年一步到位

## 决策结果

RSA 2048，别名与密码 ntu-shower，有效期至 2056。

### 正面后果

* OTA 链路永久可用

### 负面后果

* 密钥文件成为全系统唯一长期绑定物件，必须异地备份

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
