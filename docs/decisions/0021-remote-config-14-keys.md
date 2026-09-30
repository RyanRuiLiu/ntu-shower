# 0021. 远程配置 14 项全集中下发

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

用户要求设备端一切可远程改。

## 考虑过的选项

* 写死
* 全局默认加设备覆盖两层，14 个运行时键

## 决策结果

经 hello_ack 与 config.update 下发合并后的有效配置；未知键设备忽略。

### 正面后果

* 每台设备可单独调参，前向兼容新键

### 负面后果

* 键集合两端各一份映射（schema 固定）

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
