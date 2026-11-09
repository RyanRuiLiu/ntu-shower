# ADR-0019: 认证标准化——删除自定 `X-Identity` 头，统一 Bearer 凭据与会话

- 状态：Accepted
- 日期：2026-11-09
- 决策者：项目负责人

- 决策者：项目负责人
- 取代：身份通道由 `X-Identity` 头改为标准凭据；`access_identity` 与 `app_users` 两张身份表合并为 `principals`

## 背景

此前的「统一身份制」让调用方在自定请求头 `X-Identity` 里携带明文标识，服务端按标识查库得能力位。它不标准（非 Authorization 通道）、无签名（可伪造）、明文可枚举。管理台账号表 `app_users` 与名单表 `access_identity` 字段几乎重复，是同一概念的两次实现。

## 决策

- 身份通道：管理台走签名会话 Cookie（`/api/v1/auth/login`），其余调用方走 `Authorization: Bearer` 凭据；凭据只存 SHA-256 摘要，登记主体时一次性返回明文，编辑即轮换，删除即失效。
- 单一主体表 `principals`：口令哈希、凭据哈希、能力位、设备作用域同一张表；表数 23 → 22。
- 设备接入令牌默认强制校验（`require_device_token=True`），仅测试夹具关闭。
- 审计不再记录明文身份，改记凭据摘要前 12 位。

## 备选方案

- 保留 `X-Identity` 并只加令牌：未选择——自定头本身就是要消除的对象。
- 引入 JWT：未选择——单机自签服务的验证方与签发方是同一进程，HMAC 签名 Cookie 已足够，少一个依赖与一套密钥轮换。

## 后果

- 正面：所有身份通道都是标准 Authorization 通道；凭据不可从库或审计中还原。
- 负面：切换期由 status-panel 转发的 openid 调用方需改为携带 Bearer 凭据。

## 验证

`1055 passed`；`test_auth_endpoints.py` 覆盖签发、轮换、篡改、过期、禁用与 WS 握手拒绝。
