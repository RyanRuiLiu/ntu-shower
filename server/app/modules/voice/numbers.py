"""喷头号的按数值读法。

学号是标识，逐位读；喷头号是数值，要按中文数词读。数字本身仍用 0–9 素材，
十/百/千/万用素材库里的单位素材。这里只负责把整数拆成字符序列，不碰音频。

门牌号不会到十万级；超过 99999 直接拒绝，避免悄悄退化成逐位读。
"""

_DIGITS = "零一二三四五六七八九"
DIGIT_SYMBOLS = frozenset(_DIGITS)
_DIGIT_VALUES = {char: index for index, char in enumerate(_DIGITS)}
_UNITS = ("十", "百", "千", "万")
ERR_RANGE = "门牌号超出可读范围"


def cardinal_chars(value: int) -> list[str]:
    """把 0–99999 转成中文数词字符序列；0 读「零」。"""
    if value < 0 or value > 99999:
        raise ValueError(ERR_RANGE)
    if value == 0:
        return ["零"]
    if value < 10:
        return [_DIGITS[value]]
    if value < 20:
        return ["十"] + ([_DIGITS[value % 10]] if value % 10 else [])
    if value < 100:
        return [_DIGITS[value // 10], "十"] + ([_DIGITS[value % 10]] if value % 10 else [])
    if value < 1000:
        result = [_DIGITS[value // 100], "百"]
        rest = value % 100
        if rest == 0:
            return result
        if rest < 10:
            return [*result, "零", _DIGITS[rest]]
        if rest < 20:
            return result + ["一", "十"] + ([_DIGITS[rest % 10]] if rest % 10 else [])
        return [*result, *cardinal_chars(rest)]
    if value < 10000:
        result = [_DIGITS[value // 1000], "千"]
        rest = value % 1000
        if rest == 0:
            return result
        if rest < 10:
            return [*result, "零", _DIGITS[rest]]
        if rest < 20:
            return [*result, "零", "一", "十"] + ([_DIGITS[rest % 10]] if rest % 10 else [])
        if rest < 100:
            return [*result, "零", *cardinal_chars(rest)]
        return [*result, *cardinal_chars(rest)]
    result = [*cardinal_chars(value // 10000), "万"]
    rest = value % 10000
    if rest == 0:
        return result
    if rest < 10:
        return [*result, "零", _DIGITS[rest]]
    if rest < 20:
        return [*result, "零", "一", "十"] + ([_DIGITS[rest % 10]] if rest % 10 else [])
    if rest < 1000:
        return [*result, "零", *cardinal_chars(rest)]
    return [*result, *cardinal_chars(rest)]


def digit_value(symbol: str) -> int:
    return _DIGIT_VALUES[symbol]


def unit_chars() -> tuple[str, ...]:
    return _UNITS


def digit_chars(cert_no: str) -> list[str]:
    """学号只读数字字符；结尾的字母（以及其它非数字）自然被忽略。"""
    return [char for char in cert_no if char.isdigit()]
