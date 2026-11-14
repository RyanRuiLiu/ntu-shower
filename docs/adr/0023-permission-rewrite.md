# ADR-0023: 权限体系重写——用户与密钥分离、resource:action scopes、长期密钥

- 状态：Accepted
- 日期：2026-11-15
- 决策者：项目负责人
- 承接：ADR-0019（认证标准化）——Bearer 通道与摘要存储不变；身份模型与权限模型在此之上重写

## 背景

ADR-0019 的 `principals` 把口令、凭据、能力位、设备作用域压进一行：每主体一枚凭据（编辑即轮换）、权限只有 session/photo/admin 三个能力位、管理面整个由 admin 一位把守。对外开放业务调用（分配/结算给外部系统）时，调用方要么拿全权 admin、要么没门；多枚凭据无法共存，轮换即断手；面板会话 12 小时固定过期，每天都要重新登录。

## 决策

- **用户与密钥分离**（GitHub fine-grained PAT / Stripe restricted key 同型）：`principals` 改名 `users`，删除 `kind`（账户不分类——GitHub 官方的 machine user 同型，登录方式由凭据决定）；新增 `api_keys` 表，每用户多枚密钥，**默认永不过期**（GitHub 官方允许 infinite lifetime），scopes 与设备作用域都附在密钥上；轮换 = 发新枚→切换→吊销旧枚，多枚共存天然提供重叠窗口。
- **权限 = `resource:action` 字符串**（AWS IAM / Google Cloud IAM 官方命名模式，RFC 6749 §3.3 的 scope 语义）：16 个 scope + `*` 通配（AWS 官方 Action 通配写法），字典唯一出处 `auth/permissions.py`，端点映射由测试对账 OpenAPI 渲染。
- **守卫**：FastAPI 官方 `SecurityScopes` 聚合 + 对象级 device_scope 检查（OWASP IDOR 条目）；会话通道视为全权（面板即操作台，权限只存密钥一处）。
- **失败码对齐 RFC 6750 §3.1**：凭据无效 `invalid-token`、权限不足 `insufficient-scope`（取代 unknown-identity / capability-denied）。
- **凭据前缀 `ntushower_`**：GitHub `github_pat_` / GitLab `glpat_` 同型——看一眼知道钥的归属，日志与检索不误伤。
- **会话生命周期**（OWASP Session CS 三机制，全部服务端强制）：空闲超时（`session_idle_seconds`，30 天）+ renewal（使用中每过一天重签 Cookie）+ 绝对超时（`session_absolute_seconds`，90 天）。
- **限速按调用方分桶**（OWASP API4）：nginx `map` 把持钥请求按密钥分桶、匿名按 IP 分桶；登录端点专用 1 r/s 桶。
- **契约面**：`/openapi.json` 公开（oauth2 scheme + scopes 渲染，即外部对接文档）；`/docs` `/redoc` 关闭（OWASP API8）。

## 备选方案

- 保留 admin 单位、只加对外只读位：未选择——管理面内部仍一刀切，最小权限不成立。
- RBAC 角色层 / ABAC 策略引擎 / ReBAC（OpenFGA、Zanzibar 系）：未选择——自用单人系统的空转，违背轻量目标。
- 凭据加过期时间（expires_at）：未选择——外部集成需要长期稳定的钥；吊销已覆盖泄露处置，自动过期只会造成静默断手。
- JWT：未选择——ADR-0019 的理由不变（单机自签、验证方与签发方同进程）。

## 后果

- 正面：外部系统拿一枚最小权限密钥即可调分配/结算，长期不换；泄露处置只吊一枚；扩展新权限=字典加一行，机制不动。
- 负面：`principals` 表消失（迁移一次性脚本处理，旧凭据摘要原样搬入 api_keys，不断手）；管理面不再有单一 admin 位，各域权限在字典中逐条可见。

## 验证

`892 passed`，覆盖率 96.43%；`test_permissions.py` 对账字典↔OpenAPI 映射，`test_api_keys.py` 覆盖签发/列表/吊销/作用域矩阵，`test_auth_endpoints.py` 覆盖会话三机制。
