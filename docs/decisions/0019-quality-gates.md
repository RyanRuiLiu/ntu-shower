# 0019. 质量门防代码漂移

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

用户要求防止 AI 写代码漂移。

## 考虑过的选项

* 无强制
* ruff 加 mypy 加 pytest 加 detekt 全零违规

## 决策结果

服务端 ruff/mypy/pytest；设备端 detekt 加 JUnit 加 JaCoCo 覆盖率门。

### 正面后果

* 机器强制，不靠自觉

### 负面后果

* detekt 1.23 不支持 JDK 25，只能在 CI 跑

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
