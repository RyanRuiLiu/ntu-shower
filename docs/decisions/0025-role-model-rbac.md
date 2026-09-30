# 0025. 角色模型：只读用户管理员三级

* 状态：superseded（用户面 JWT 登录部分被 [0034](0034-user-plane-anonymous-auth.md) 取代；管理面守卫与只读/管理分层仍然有效）
* 日期：2026-09-30

## 背景与问题

系统只服务两类客户：普通用户与管理员；身份来自外部（微信小程序、独立 APP、自写验证）。

## 考虑过的选项

* 注册制用户体系
* 无注册：管理员预置账户加 provider 抽象加 JWT 角色守卫

## 决策结果

anonymous 只读，user 可操作设备，admin 全功能；JWT HS256 短期令牌。

### 正面后果

* 对齐 OAuth2 令牌行业惯例，外部身份源可逐步接入

### 负面后果

* wechat 与 app 断言校验待接入时实现

## 相关

- docs/ARCHITECTURE.md · docs/PROTOCOL.md · docs/STANDARDS.md
