# ADR-0006: 协议与契约

- 状态：Accepted
- 日期：2026-10-30（整理入档；原始决策日期见 Git 历史）
- 决策者：项目负责人
- 取代：无

## 背景

本条目汇总该领域的有效决策；原始讨论、实测与取舍见相关代码注释、`docs/adr/` 的 evidence 附录 与 Git 历史。

## 决策

- **协议承载在 WSS 上，载荷是 JSON 信封**，类型名点分（`link.hello`、`session.command`）。
  设备量级下 protobuf/MQTT 的复杂度换不来收益，而抓包即读、schema 即文档。
- **字段与类型全套规范化**：字段 `snake_case`、RFC3339 时间、UUIDv4 消息 id、每个 type 一份
  JSON Schema。三端同源无歧义，新增可选字段不升版本号。
- **信封只有一处组装**：服务端 `core/protocol.py`、设备端 `MessageCodec`。所有出站帧经它们
  构造，协议格式改动只改一处；错误帧是扁平的 `{code,message}`。
- **JSON Schema 是协议的唯一事实源**，存 `protocol/schema/`（含 `envelope.json`），CI 按样例
  校验。改协议必须三处同步：schema、服务端实现、设备端实现。
- **HTTP 错误统一为 RFC 9457 Problem Details**：`application/problem+json` 的
  `{type,title,status,detail}`，由 `core/errors.py` 装配（含 422 校验错误）。`type` 是 kebab-case
  的稳定 code，登记在 `core/problems.py` 且一经发布不改；`title` 是中文展示文案、`detail` 承载
  可变信息。客户端按 `type` 分支，不解析自由文本，也不在前端另维护一层文案映射。
- **设备在 `link.hello` 声明自己支持的 action 集合，服务端下发前比对**，只在声明的范围内下发。
  空数组表示未声明（升级前的旧版本），此时不拦截——否则升级服务端会把在网设备锁死。
- **配置经 `link.hello_ack` 与 `config.update` 下发合并后的有效配置，未知键设备忽略**。
  新增配置键不影响旧设备，靠"忽略不认识的键"实现。
- **喷头状态是受约束的枚举而非裸字符串**：`DoorState`（`modules/doors/schemas.py`）由设备上报的
  4 个原始整数派生，定义成 `str, Enum` 让 OpenAPI 生成 `enum`，客户端据此生成联合类型，
  不必手抄一份状态清单。
- **动作名与失败原因码是封闭集合**，登记在 `core/actions.py` 与 `core/reason_codes.py`，
  各有一份测试对账。两端各写一份字符串迟早会对不上，对不上时出错的是现场设备。

## 备选方案

原始记录未保留完整的备选方案；被取代的结论见 Git 历史。

## 后果

见各条决策正文与相关代码注释。

## 验证

由相关测试、`docs/reference.md` 与运行记录验证。
