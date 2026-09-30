# 0005. 入口拓扑：443 统一入口加 nginx 路径分流

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

对外只有一个域名一个端口最简。

## 考虑过的选项

* 服务端多端口
* 443 统一入口 nginx 分流

## 决策结果

443 统一入口，nginx 按路径分流；服务器只暴露 80/443/22。

### 正面后果

* 证书一张，防火墙规则最少

### 负面后果

* nginx 成为关键路径组件

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
