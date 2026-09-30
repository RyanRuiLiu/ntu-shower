# 0027. 协议信封单一来源

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

信封构造散落 5 处、版本常量 3 处。

## 考虑过的选项

* 各自维护
* core/protocol.py（服务端）加 MessageCodec（设备端）单一来源

## 决策结果

所有出站帧只经一处组装；错误帧扁平 {code,message}。

### 正面后果

* 协议格式改动只动一处

### 负面后果

* 无

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
