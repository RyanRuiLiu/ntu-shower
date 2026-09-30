# 0002. 服务端技术栈 FastAPI 模块化单体

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

服务端是 I/O 密集的设备长连接聚合器；延续现有运维习惯。

## 考虑过的选项

* FastAPI 单进程单事件循环模块化单体
* 多进程/多 worker
* Go/Java 重写

## 决策结果

FastAPI 模块化单体，单进程单事件循环。

### 正面后果

* 异步长连接模型简单正确，运维延续性好

### 负面后果

* 垂直扩展受限，未来需按事件总线接口替换为 Redis

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
