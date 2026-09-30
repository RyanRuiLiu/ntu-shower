# 0010. 构建工具链能新则新

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

设备运行时冻结 4.4，但构建工具链没有理由不新。

## 考虑过的选项

* 旧 Gradle/AGP
* Gradle 9.8 加 AGP 9.4 加内置 Kotlin 加 JDK 25

## 决策结果

能新则新；minSdk/targetSdk 19 冻结运行时。

### 正面后果

* 工具链现代，lint 与标准最新

### 负面后果

* 旧运行时 lint 豁免项需长期挂着

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
