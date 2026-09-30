# 0007. 设备寻址：内置 DNS 解析器

* 状态：accepted
* 日期：2026-09-29

## 背景与问题

实测设备系统 DNS（8.8.8.8）被校园网封锁，而 114.114.114.114 的 UDP/TCP 53 可用。

## 考虑过的选项

* 构建期写死 IP
* 系统 DNS
* APK 内置 DNS 解析器直查公共 DNS

## 决策结果

内置 DNS 解析器，以域名为唯一网络锚点；禁止缓存 IP、构建期 IP、任何兜底。

### 正面后果

* 换服务器只需改 DNS 记录，零现场

### 负面后果

* 自带 DNS 实现需自维护

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
