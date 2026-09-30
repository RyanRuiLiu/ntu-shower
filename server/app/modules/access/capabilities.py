"""访问授权：谁可以用哪些能力。

## 模型

- **身份（identity）**：小程序用户是微信 openid；网页用户是我方生成的不透明令牌。
- **能力（capability）**：`ops`（分配/结算）、`photo`（拍照/截图）。
- **授予（grant）**：给某个身份授予一组能力。只读查看不需要授予。

## 与 status-panel 的关系

沿用其已验证的语义（openid 白名单 + 能力位），差异有两点：

1. 授权名单存**数据库**而非配置文件——要在后台随时增删；
2. 小程序登录后签发**会话令牌**，而不是每个请求都拿 code 去调微信
   （`jscode2session` 有频率限制，且每次请求多一次外部往返）。
"""

from enum import Enum


class Capability(str, Enum):
    """可授予的能力。

    用 `str, Enum` 而非 `StrEnum`：运行时是 Python 3.10，后者 3.11 才有。
    """

    OPS = "ops"
    """分配 / 结算：会真实操作浴室 App。"""

    PHOTO = "photo"
    """拍照 / 截图：会取回现场画面。"""


ALL_CAPABILITIES = frozenset(cap.value for cap in Capability)
