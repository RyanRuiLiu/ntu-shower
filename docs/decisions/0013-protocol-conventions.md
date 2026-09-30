# 0013. 协议规范定稿

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

协议字段与类型需要统一规范防漂移。

## 考虑过的选项

* 各自约定
* snake_case 字段加点分类型命名加 RFC3339 加 UUIDv4 加每 type 一份 JSON Schema

## 决策结果

全套规范定稿并落 docs/PROTOCOL.md；schema 存 protocol/schema/。

### 正面后果

* 三端同源无歧义，新增字段不升版

### 负面后果

* 协议变更需三处同步

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
