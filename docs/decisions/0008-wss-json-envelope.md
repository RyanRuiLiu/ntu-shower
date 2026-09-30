# 0008. 协议编码：WSS 加 JSON 信封

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

协议要可读可调试、三端同源。

## 考虑过的选项

* 二进制 protobuf
* MQTT
* WSS 加 JSON 信封 {v,type,id,ts,payload}

## 决策结果

WSS 加 JSON 信封，点分类型命名；设备量级下 protobuf/MQTT 复杂度无收益。

### 正面后果

* 抓包即读，schema 即文档

### 负面后果

* 报文体量大于二进制协议（设备量级可忽略）

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
