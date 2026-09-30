# 0035. 真实 ADB 经 443 网关透传（零新端口）

* 状态：accepted
* 日期：2026-09-30

## 背景与问题

旧项目 adb-tunnel + bridge-server 提供真 ADB（开 7001/7002/7003 三端口）。
新系统入口拓扑只暴露 443/22（ADR-0005），需要在不破坏拓扑的前提下
提供同等的真实 ADB 能力（用户明确要求"真实的 adb"，拒绝轻量替代）。

## 考虑过的选项

* 照搬旧三端口架构
* 轻量替代（logcat 快照/小文件 base64）
* 复用现有 443 网关 + 22 SSH 透传 ADB 字节流

## 决策结果

- 设备端：管理面下发 `bridge.open`（含一次性 nonce）→ root 开启 adbd TCP
  （setprop service.adb.tcp.port 5555）→ 另开一条二进制 WSS `/gateway/bridge`
  与本地 adbd 做字节互泵；
- 服务端：nonce 配对后绑定 127.0.0.1:<port>（默认 15655 起，避开旧系统 15555），
  开发者经 SSH 转发后 `adb connect 127.0.0.1:<port>`；
- 安全：仅管理面触发 + 一次性 nonce + 仅绑回环 + TCP 断开不拆桥
  （回到等待下一个 adb 连接，对齐旧项目隧道语义）+ 空闲 300s 回收；
- 行业依据：Root Bridge 事件（裸 ADB 暴露公网致大规模失陷）——本方案
  adb 流全程包裹在 WSS TLS 内且不对外网暴露任何端口；
  OpenSTF/DeviceFarmer 的生产隧道模式（认证 WS 会话承载 adb 流）。

### 正面后果

* 真 ADB 全功能（shell/install/dumpsys/...），Android Studio/adb 生态直连
* 零新端口、零新进程、多设备端口自动分配

### 负面后果

* 设备侧 bridge 期间 adbd 以 TCP 模式运行（close 后自动关闭）
* 桥接经 WSS 有一定带宽开销（调试流量可接受）

## 相关

- docs/PROTOCOL.md（bridge.open/close）· docs/STANDARDS.md §3
