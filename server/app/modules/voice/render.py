"""播报模板的校验与分段渲染。

模板是「语音段 / 音效 / 停顿」的序列，三种记法：

- `{key}`：可靠占位符（封闭集合，用事件值替换后照常朗读）；
- `{fx:名称}`：在该位置插入音效库里这个音效（不朗读，只响）；
- `{pause:毫秒}`：在该位置插入静音（不朗读）。

渲染产出分段序列（[render_segments]），合成按段拼装 WAV；展示文本（[render_text]）
把音效写成 ♪名称、停顿写成 （停顿1.5秒），给人看、不进 TTS。
"""

import dataclasses
import re
from collections.abc import Mapping
from typing import NoReturn

MAX_TEMPLATE_CHARS = 200
MAX_RENDERED_CHARS = 160

#: 记法的统一文法：可靠占位符（学号可带 `:N` 截断）/ 音效 / 停顿。
TOKEN_PATTERN = re.compile(r"\{(fx:[^{}]+|pause:[0-9]+|[a-z_]+(?::[0-9]+)?)\}")

#: 学号逐位念的数字表——显式换成中文数字，不把"怎么念一串数字"交给 TTS 猜。
DIGIT_WORDS = "零一二三四五六七八九"

#: 支持带 `:N` 截断的占位符（目前只有学号：标识常念末几位）。
PARAMETERIZED_KEYS = frozenset({"cert_no"})
TRUNCATE_MIN = 1
TRUNCATE_MAX = 10

#: 模板里音效名的提取器（校验与守卫用，与 TOKEN_PATTERN 的 fx 分支同一形状）。
FX_NAME_PATTERN = re.compile(r"\{fx:([^{}]+)\}")
PLACEHOLDER_KEYS = frozenset({"door_no", "cert_no", "event"})

#: 停顿的边界（毫秒）：太短的停顿听不出来，太长的该拆成两条规则。
PAUSE_MIN_MS = 100
PAUSE_MAX_MS = 5_000

SAY = "say"
FX = "fx"
PAUSE = "pause"


class TemplateError(ValueError):
    """模板不是合法形状，或渲染结果超出上限。"""


def _reject(reason: str) -> NoReturn:
    raise TemplateError(reason)


@dataclasses.dataclass(frozen=True)
class Segment:
    """一段要拼进音频的东西：朗读文本 / 音效名 / 停顿毫秒。"""

    kind: str
    text: str = ""
    effect: str = ""
    pause_ms: int = 0


def spoken_digits(value: str) -> str:
    """一串字符逐位念：数字显式换成中文数字，非数字字符原样保留。"""
    return "".join(DIGIT_WORDS[int(char)] if char.isdigit() else char for char in value)


def validate_template(text: str) -> tuple[str, ...]:
    """校验模板，返回出现过的可靠占位符基础名（去重、保持首次出现顺序）。

    只接受三种记法；未知 key、裸 `{`/`}`、停顿/截断越界一律拒绝。
    """
    if not text.strip():
        _reject("播报内容不能为空")
    if len(text) > MAX_TEMPLATE_CHARS:
        _reject(f"播报内容最多 {MAX_TEMPLATE_CHARS} 个字符")
    tokens = TOKEN_PATTERN.findall(text)
    unknown = sorted({token.split(":", 1)[0] for token in tokens if ":" not in token} - PLACEHOLDER_KEYS)
    if unknown:
        names = ", ".join("{" + token + "}" for token in unknown)
        _reject(f"未知占位符：{names}")
    for token in tokens:
        if token.startswith("fx:"):
            if not token.split(":", 1)[1].strip():
                _reject("音效占位符里没有名称")
            continue
        if token.startswith("pause:"):
            ms = int(token.split(":", 1)[1])
            if not PAUSE_MIN_MS <= ms <= PAUSE_MAX_MS:
                _reject(f"停顿须在 {PAUSE_MIN_MS}–{PAUSE_MAX_MS} 毫秒（收到 {ms}）")
            continue
        if ":" in token:
            key, _, digits = token.partition(":")
            if key not in PARAMETERIZED_KEYS:
                _reject(f"{{{key}}} 不支持 :位数 截断（只有学号支持）")
            if not TRUNCATE_MIN <= int(digits) <= TRUNCATE_MAX:
                _reject(f"学号截断须在 {TRUNCATE_MIN}–{TRUNCATE_MAX} 位（收到 {digits}）")
    remainder = TOKEN_PATTERN.sub("", text)
    if "{" in remainder or "}" in remainder:
        _reject("占位符格式不正确，只允许 {key}、{key:位数}、{fx:名称}、{pause:毫秒} 形式")
    return tuple(dict.fromkeys(token.split(":", 1)[0] for token in tokens if TOKEN_PREFIX.match(token)))


#: 可靠占位符 token 的前缀形状（与 `:N` 参数化形式区分）。
TOKEN_PREFIX = re.compile(r"^[a-z_]+$")


def has_placeholders(text: str) -> bool:
    """模板渲染后是否仍是固定音频：只要含任一种记法就不是。"""
    return bool(TOKEN_PATTERN.search(text))


def render_segments(text: str, values: Mapping[str, str]) -> list[Segment]:
    """把模板渲染成分段序列；可靠占位符的值必须齐全。

    总朗读文本（各语音段之和）受渲染上限约束——上限管的是「要说的话」的长度，
    音效与停顿不计入。
    """
    keys = validate_template(text)
    for key in keys:
        if key not in values:
            _reject(f"缺少占位符的值：{{{key}}}")

    segments: list[Segment] = []
    spoken: list[str] = []

    def flush(buffer: list[str]) -> None:
        joined = "".join(buffer)
        if joined.strip():
            segments.append(Segment(SAY, text=joined))
            spoken.append(joined)

    pending: list[str] = []
    index = 0
    for match in TOKEN_PATTERN.finditer(text):
        pending.append(text[index : match.start()])
        token = match.group(1)
        if token.startswith("fx:"):
            flush(pending)
            pending = []
            segments.append(Segment(FX, effect=token.split(":", 1)[1].strip()))
        elif token.startswith("pause:"):
            flush(pending)
            pending = []
            segments.append(Segment(PAUSE, pause_ms=int(token.split(":", 1)[1])))
        else:
            key, _, digits = token.partition(":")
            if key == "cert_no":
                # 学号是标识不是数量：逐位念（数字换中文数字，字母按字母名念）；
                # 带 :N 只念末 N 位。其余占位符（喷头号、时长等）保持数量原样
                value = values[key]
                pending.append(spoken_digits(value[-int(digits) :] if digits else value))
            else:
                pending.append(values[key])
        index = match.end()
    pending.append(text[index:])
    flush(pending)

    if not segments:
        _reject("播报内容渲染后为空")
    rendered_spoken = "".join(spoken)
    if len(rendered_spoken) > MAX_RENDERED_CHARS:
        _reject(f"播报内容渲染后最多 {MAX_RENDERED_CHARS} 个字符（不含音效与停顿）")
    return segments


def render_text(segments: list[Segment]) -> str:
    """分段序列 → 展示文本：音效与停顿写成人能读的记号，不进 TTS。"""
    parts: list[str] = []
    for segment in segments:
        if segment.kind == SAY:
            parts.append(segment.text)
        elif segment.kind == FX:
            parts.append(f"♪{segment.effect}")
        else:
            parts.append(f"（停顿{segment.pause_ms / 1000:g}秒）")
    return "".join(parts)


def render_template(text: str, values: Mapping[str, str]) -> str:
    """旧名保留给只关心展示文本的调用方（规则示例、试听文案）。"""
    return render_text(render_segments(text, values))


def sample_values(
    event_label: str,
    *,
    door_no: int = 1,
    cert_no: str = "示例学号",
) -> dict[str, str]:
    """试听/示例文本的上下文；实际事件用真实事件值替换。"""
    return {
        "door_no": str(door_no),
        "cert_no": cert_no,
        "event": event_label,
    }
