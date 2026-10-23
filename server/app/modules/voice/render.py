"""播报模板的校验、渲染与时长口播格式。

模板只允许封闭的五个占位符；渲染结果有独立上限。时间不从裸数字直接交给 TTS：
先格式化成「35秒 / 12分钟 / 12分34秒」，避免被读成「十二比三十四」。
"""

import re
from collections.abc import Mapping
from typing import NoReturn

MAX_TEMPLATE_CHARS = 120
MAX_RENDERED_CHARS = 160

PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")
PLACEHOLDER_KEYS = frozenset({"door_no", "cert_no", "event", "assigned_duration", "shower_duration"})


class TemplateError(ValueError):
    """模板不是合法形状，或渲染结果超出上限。"""


def _reject(reason: str) -> NoReturn:
    raise TemplateError(reason)


def validate_template(text: str) -> tuple[str, ...]:
    """校验模板，返回出现过的占位符（去重、保持首次出现顺序）。

    只接受 `{key}`；未知 key、裸 `{`/`}` 一律拒绝。
    """
    if not text.strip():
        _reject("播报内容不能为空")
    if len(text) > MAX_TEMPLATE_CHARS:
        _reject(f"播报内容最多 {MAX_TEMPLATE_CHARS} 个字符")
    keys = PLACEHOLDER_PATTERN.findall(text)
    unknown = sorted(set(keys) - PLACEHOLDER_KEYS)
    if unknown:
        names = ", ".join("{" + key + "}" for key in unknown)
        _reject(f"未知占位符：{names}")
    remainder = PLACEHOLDER_PATTERN.sub("", text)
    if "{" in remainder or "}" in remainder:
        _reject("占位符格式不正确，只允许 {key} 形式")
    return tuple(dict.fromkeys(keys))


def has_placeholders(text: str) -> bool:
    return bool(validate_template(text))


def render_template(text: str, values: Mapping[str, str]) -> str:
    """把模板渲染成最终文本；值必须齐全，结果受渲染上限约束。"""
    keys = validate_template(text)
    for key in keys:
        if key not in values:
            _reject(f"缺少占位符的值：{{{key}}}")

    rendered = PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], text)
    if len(rendered) > MAX_RENDERED_CHARS:
        _reject(f"播报内容渲染后最多 {MAX_RENDERED_CHARS} 个字符")
    if not rendered.strip():
        _reject("播报内容渲染后为空")
    return rendered


def spoken_duration(seconds: int) -> str:
    """秒数 → 中文口播文本。

    - 不足一分钟：`35秒`
    - 整分钟：`12分钟`
    - 其余：`12分34秒`（秒数不补零，TTS 读作十二分三十四秒）
    """
    total = max(0, int(seconds))
    minutes, rest = divmod(total, 60)
    if total < 60:
        return f"{total}秒"
    if rest == 0:
        return f"{minutes}分钟"
    return f"{minutes}分{rest}秒"


def sample_values(
    event_label: str,
    *,
    door_no: int = 1,
    cert_no: str = "示例学号",
    assigned_seconds: int = 1122,
    shower_seconds: int = 965,
) -> dict[str, str]:
    """试听/示例文本的上下文；实际事件用真实会话值替换。"""
    return {
        "door_no": str(door_no),
        "cert_no": cert_no,
        "event": event_label,
        "assigned_duration": spoken_duration(assigned_seconds),
        "shower_duration": spoken_duration(shower_seconds),
    }
