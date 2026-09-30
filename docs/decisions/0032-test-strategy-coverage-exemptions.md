# 0032. 测试策略：全面覆盖加赦免制

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

用户要求全面覆盖，测不了的单独报告并赦免。

## 考虑过的选项

* 硬性全量覆盖
* 全面覆盖加赦免清单（框架耦合与真机依赖项）

## 决策结果

服务端 pytest-cov fail_under 95；设备端 JaCoCo 统计纯逻辑类 fail_under 80；赦免清单集中登记于 STANDARDS.md。

### 正面后果

* 覆盖只升不降有机器强制，赦免透明可审计

### 负面后果

* 赦免项无自动化回归保障，依赖真机验证

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
