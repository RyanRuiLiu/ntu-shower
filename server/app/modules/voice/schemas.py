"""语音播报的接口契约与事件/占位符目录。

事件中文名不另造：取 `DoorState` 的目标状态正名。`{event}` 的值就是目标状态，
选择框靠 `description` 里的转换关系区分两个「淋浴中」。

## 目标模型

设备与学号两个维度各自可以是具体值或 [WILDCARD]（「全部」）。一次事件匹配时
**具体压过通配**：先比设备维度（具体设备在全部设备之前），再比学号维度，两边都
具体的那条规则胜出。同一个「设备 × 学号 × 事件」只允许一条规则（数据库唯一约束），
所以匹配结果是确定的，不需要优先级字段裁决——要临时换播报内容，停用具体规则让
通配规则顶上，或直接改文本，不存在第二条同目标规则。

「全部学号」的规则可带**排除名单**（`exempt_cert_nos`）：名单里的学号不命中这条
规则（往下找次特异的候选，没有就不播）。排除只对「全部学号」有意义——具体学号的
规则把目标自己排除掉等于停用，那种形状直接拒绝。
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.modules.doors.events import DoorEventKind
from app.modules.doors.schemas import DoorState, state_label

# ── 边界 ──

DELAY_MIN_MS = 0
DELAY_MAX_MS = 10_000
DELAY_DEFAULT_MS = 0

VOLUME_MIN = 0
VOLUME_MAX = 5
VOLUME_DEFAULT = 3

#: 目标维度的通配值：设备维度表示「全部设备」，学号维度表示「全部学号」。
WILDCARD = "*"

#: 单次提交里设备与学号列表各自的上限。
MAX_DEVICE_TARGETS_PER_CREATE = 50
MAX_CERT_TARGETS_PER_CREATE = 500

#: 一条规则的排除名单上限。排除是「全部学号」里挑几个人出去，量级不该超过创建名单。
MAX_EXEMPT_PER_RULE = 500


@dataclass(frozen=True)
class VoiceEventSpec:
    """一个事件的中文正名、转换关系与所属阶段。

    `phase` 是选择界面的分组（分配 / 用水 / 释放），不是新概念——同一阶段的
    事件回答同一类问题，勾选时放在一起找。
    """

    value: DoorEventKind
    label: str
    description: str
    phase: str


def _state(state: DoorState) -> str:
    return state_label(state)


#: 事件目录：值与 `DoorEventKind` 一一对应，中文名全部来自 `DoorState` 的正名。
#: 释放按前一占用状态拆成三种——「本次淋浴 X 分钟」只对淋浴后释放成立。
VOICE_EVENTS: tuple[VoiceEventSpec, ...] = (
    VoiceEventSpec(
        DoorEventKind.ASSIGNED,
        _state(DoorState.WAITING),
        f"{_state(DoorState.FREE)} / {_state(DoorState.SELECTED)} → {_state(DoorState.WAITING)}",
        "分配",
    ),
    VoiceEventSpec(
        DoorEventKind.SHOWER_STARTED,
        _state(DoorState.BATHING),
        f"{_state(DoorState.WAITING)} → {_state(DoorState.BATHING)}",
        "用水",
    ),
    VoiceEventSpec(
        DoorEventKind.PAUSED,
        _state(DoorState.PAUSED),
        f"{_state(DoorState.BATHING)} → {_state(DoorState.PAUSED)}",
        "用水",
    ),
    VoiceEventSpec(
        DoorEventKind.RESUMED,
        _state(DoorState.BATHING),
        f"{_state(DoorState.PAUSED)} → {_state(DoorState.BATHING)}",
        "用水",
    ),
    VoiceEventSpec(
        DoorEventKind.RELEASED_UNUSED,
        _state(DoorState.FREE),
        f"{_state(DoorState.WAITING)} → {_state(DoorState.FREE)}：没开阀就被释放",
        "释放",
    ),
    VoiceEventSpec(
        DoorEventKind.RELEASED_AFTER_SHOWER,
        _state(DoorState.FREE),
        f"{_state(DoorState.BATHING)} → {_state(DoorState.FREE)}：正常结算收尾",
        "释放",
    ),
    VoiceEventSpec(
        DoorEventKind.RELEASED_WHILE_PAUSED,
        _state(DoorState.FREE),
        f"{_state(DoorState.PAUSED)} → {_state(DoorState.FREE)}：暂停中被释放",
        "释放",
    ),
)

VOICE_EVENTS_BY_VALUE: dict[DoorEventKind, VoiceEventSpec] = {event.value: event for event in VOICE_EVENTS}


def event_spec(value: DoorEventKind) -> VoiceEventSpec:
    return VOICE_EVENTS_BY_VALUE[value]


@dataclass(frozen=True)
class PlaceholderSpec:
    key: str
    label: str
    description: str


#: 可靠占位符的封闭集合。播报以预热的固定音频为主，不提供随会话变化的量——
#: 曾有时长两个占位符：每次取值都不同、永不命中缓存、每场都要现场合成，已移除。
PLACEHOLDERS: tuple[PlaceholderSpec, ...] = (
    PlaceholderSpec("door_no", "喷头号", "事件里的喷头号。"),
    PlaceholderSpec("cert_no", "学号", "事件里的学号，逐位念（数字念数字、字母念字母名），不会读成一个大数。"),
    PlaceholderSpec(
        "cert_no:4", "学号后 4 位", "只念学号末 4 位，同样逐位念；4 可改成 1–10 的其它位数，如 {cert_no:2}。"
    ),
    PlaceholderSpec("event", "事件目标状态", "等待开阀 / 淋浴中 / 已暂停 / 空闲。"),
)


class VoiceInfoView(BaseModel):
    """音色目录里的一项：引擎、说明、当前是否可用。"""

    key: str
    engine: str
    note: str
    available: bool


class VoiceEventView(BaseModel):
    value: DoorEventKind
    label: str
    description: str
    phase: str


class VoicePlaceholderView(BaseModel):
    key: str
    label: str
    description: str


class VoiceBoundView(BaseModel):
    minimum: int
    maximum: int
    default: int


class VoiceDelayView(BaseModel):
    minimum_ms: int
    maximum_ms: int
    default_ms: int


class VoiceSpecView(BaseModel):
    events: list[VoiceEventView]
    placeholders: list[VoicePlaceholderView]
    voices: list[VoiceInfoView]
    delay: VoiceDelayView
    volume: VoiceBoundView


class VoiceEffectRename(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class VoiceEffectView(BaseModel):
    """音效库里的一段提示音：内容身份是归一化 WAV 的 SHA-256。"""

    effect_id: str
    name: str
    size_bytes: int
    duration_ms: int
    created_at: str


class VoiceRuleCreate(BaseModel):
    """一次创建：一条逻辑规则，目标与事件都按提交的作用域整体存储，不展开。

    两个列表里 `*` 与具体值不得混填——「全部」再挑几个具体的，语义上说不通。
    """

    devices: list[str] = Field(min_length=1, max_length=MAX_DEVICE_TARGETS_PER_CREATE)
    certs: list[str] = Field(min_length=1, max_length=MAX_CERT_TARGETS_PER_CREATE)
    events: list[DoorEventKind] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=120)
    delay_ms: int = Field(default=DELAY_DEFAULT_MS, ge=DELAY_MIN_MS, le=DELAY_MAX_MS)
    volume: int = Field(default=VOLUME_DEFAULT, ge=VOLUME_MIN, le=VOLUME_MAX)
    enabled: bool = True
    exempt_cert_nos: list[str] = Field(default_factory=list, max_length=MAX_EXEMPT_PER_RULE)
    voice: str = Field(default="melo", min_length=1, max_length=32)


class VoiceRuleUpdate(BaseModel):
    """整体编辑：目标（设备/学号/排除）与事件集合都是规则的一部分，一并提交。"""

    devices: list[str] = Field(min_length=1, max_length=MAX_DEVICE_TARGETS_PER_CREATE)
    certs: list[str] = Field(min_length=1, max_length=MAX_CERT_TARGETS_PER_CREATE)
    events: list[DoorEventKind] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=120)
    delay_ms: int = Field(default=DELAY_DEFAULT_MS, ge=DELAY_MIN_MS, le=DELAY_MAX_MS)
    volume: int = Field(default=VOLUME_DEFAULT, ge=VOLUME_MIN, le=VOLUME_MAX)
    enabled: bool = True
    exempt_cert_nos: list[str] = Field(default_factory=list, max_length=MAX_EXEMPT_PER_RULE)
    voice: str = Field(default="melo", min_length=1, max_length=32)


class VoiceRuleEnabledPatch(BaseModel):
    """只改启用位；启用静态模板时服务端会照创建路径预热音频。"""

    enabled: bool


class VoicePreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=120)
    event: DoorEventKind
    cert_no: str = Field(default="示例学号", min_length=1, max_length=64)
    door_no: int = Field(default=1, ge=1, le=48)
    voice: str = Field(default="melo", min_length=1, max_length=32)


class VoicePushRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)


class VoicePushView(BaseModel):
    device_id: str
    rendered_text: str
    status: str
    reason_code: str
    detail: str
    duration_ms: int


class VoiceRuleView(BaseModel):
    id: int
    devices: list[str]
    certs: list[str]
    exempt: list[str]
    events: list[DoorEventKind]
    text: str
    delay_ms: int
    volume: int
    enabled: bool
    voice: str
    created_at: str
    updated_at: str


class VoicePreviewView(BaseModel):
    rendered_text: str
    clip_id: str
    clip_url: str
    size_bytes: int
    duration_ms: int


class VoiceStatusView(BaseModel):
    voices: list[VoiceInfoView]
    ffmpeg_available: bool
    clip_count: int


class VoiceDeliveryView(BaseModel):
    """一次播报投递：时刻之外带各阶段耗时，现场据此调延迟与语速。

    - `clip_ms`：认领之后备齐音频的耗时（含等待合成锁与缓存检查；命中缓存时接近 0）。
    - `dispatch_offset_ms`：事件发生 → 下发 `voice.play`。
    - `finish_offset_ms`：下发 → 设备收尾（含下载与播放；未下发即为 None）。
    - `downloaded`：设备是否现场下载（False 表示命中设备缓存）。
    """

    id: int
    rule_id: int
    device_id: str
    cert_no: str
    event: DoorEventKind
    door_no: int
    text: str
    delay_ms: int
    occurred_at: str
    dispatched_at: str
    done_at: str
    status: str
    detail: str
    clip_ms: int
    dispatch_offset_ms: int | None
    finish_offset_ms: int | None
    downloaded: bool
