# 0028. HTTP 错误响应 RFC 9457

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

错误格式不应自创。

## 考虑过的选项

* 裸 detail 结构
* problem 加 json 的 type/title/status/detail

## 决策结果

core/errors.py 统一装配，含 422 校验错误。

### 正面后果

* 前后端与扩展者都好处理

### 负面后果

* 无

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
