"""微信小程序 API 客户端：只用到一个能力——用 wx.login 的 code 换 openid。

不引入 SDK：整个交互是一次 GET + 一次 JSON 解析，依赖一个库不划算。
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from .config import settings

logger = logging.getLogger("ntu-shower.wechat")

JSCODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


class WeChatNotConfiguredError(RuntimeError):
    """未配置小程序的 appid/secret：登录功能不可用。"""


def is_configured() -> bool:
    return bool(settings.wechat_appid) and bool(settings.wechat_secret)


def code_to_openid(code: str) -> str | None:
    """用 wx.login 的 code 换 openid；失败返回 None。

    失败原因（code 已用过、过期、appid 不符）都只记日志不外抛：
    调用方只需知道"换不到"，无需区分微信侧的具体原因。
    """
    if not is_configured():
        raise WeChatNotConfiguredError
    query = urllib.parse.urlencode(
        {
            "appid": settings.wechat_appid,
            "secret": settings.wechat_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
    )
    try:
        with urllib.request.urlopen(f"{JSCODE2SESSION_URL}?{query}", timeout=settings.wechat_api_timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("jscode2session failed: %s", exc)
        return None

    openid = payload.get("openid")
    if not openid:
        logger.info("jscode2session rejected: errcode=%s", payload.get("errcode"))
        return None
    return str(openid)
