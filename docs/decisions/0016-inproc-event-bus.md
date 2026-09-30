# 0016. 实时推送进程内事件总线

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

前端需要设备状态实时推送。

## 考虑过的选项

* 直接起 Redis
* 进程内 asyncio 总线，接口预留替换

## 决策结果

单进程单事件循环下用最小实现，接口可平滑替换为 Redis pub/sub。

### 正面后果

* 零额外依赖

### 负面后果

* 拆分进程时需替换实现

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
