# 0018. FileObserver 掩码必须含 MODIFY

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

真机实测：浴室 App 长持 db fd 不关闭，事务写入只触发 MODIFY 不触发 CLOSE_WRITE。

## 考虑过的选项

* 仅 CLOSE_WRITE
* CLOSE_WRITE or MODIFY or DELETE or MOVED_TO

## 决策结果

掩码含 MODIFY，1 秒防抖合并事件。

### 正面后果

* 不漏报

### 负面后果

* 防抖引入 1 秒延迟（可接受）

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
