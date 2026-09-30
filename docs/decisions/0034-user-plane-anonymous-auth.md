# 0034. 用户面操作接口匿名，废除用户登录体系

* 状态：accepted（取代 0025 中用户面 JWT 登录的部分）
* 日期：2026-09-30

## 背景与问题

0025 引入了用户表 + internal provider + JWT 登录，把"学号+密码"当成了登录凭证。
对照旧项目（ntu-remote-shower）发现偏差：旧系统的 assign/settle 完全匿名，
学号+密码只是转发给浴室系统的业务凭证，真正的凭证验证发生在浴室服务器端。
自建登录对用户是多余步骤，且密码登录与未来微信小程序的 openid 体系不兼容。

## 考虑过的选项

* 保留 JWT 用户体系（0025 现状）
* 匿名对齐旧项目，auth 模块整体删除
* 保留 auth 模块但前端不接

## 决策结果

用户面操作接口（分配/结算/拍照）**匿名**；学号+密码仅作为操作参数直传设备、
由浴室系统验证。auth 模块（users 表、JWT 签发、/admin/users、/api/v1/auth/token）
**整体删除**，不留无用代码。管理面 X-Admin-Token 不变。
未来的调用方身份体系按行业标准 provider 模式另行引入：
微信小程序 = wx.login code → 服务端 code2Session 换 openid → 服务端自签令牌
（官方文档 developers.weixin.qq.com）；
独立 APP = 专属凭证 provider。届时按 STANDARDS.md 重新建模块并回归质量门。

### 正面后果

* 与旧系统模型一致，用户零多余步骤
* 无用户数据存储，攻击面缩小
* 小程序接入路径清晰（provider 签发 + 本系统 JWT 校验），不受密码体系拖累

### 负面后果

* 操作接口对公网开放（有门态预检 + 每设备单在途命令约束）；如出现滥用，
  在 nginx 层加 IP 级限流即可，不改应用协议
* 用户表、令牌端点将来需要时重建

## 相关

- docs/decisions/0025-role-model-rbac.md（用户面部分被本决策取代）
- docs/STANDARDS.md §3
