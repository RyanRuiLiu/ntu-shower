# protocol — 协议单一事实源

- `schema/`：设备 ↔ 服务端全部消息类型的 JSON Schema（draft 2020-12），手写维护；
  每新增/变更一个消息类型必须同步三处：schema、`docs/PROTOCOL.md`、双端实现。
- 服务端 `tests/test_protocol_schemas.py` 对每个类型的样例消息做 schema 校验（CI 强制）。
- 设备端编解码单一来源：`android/.../transport/MessageCodec.kt`；
  服务端信封单一来源：`server/app/core/protocol.py`——两者与 schema 一一对应。

消息清单见 `docs/PROTOCOL.md`。
