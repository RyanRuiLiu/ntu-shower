"""语音播报的接口契约与事件/占位符目录。

事件中文名不另造：取 `DoorState` 的目标状态正名。`{event}` 的值就是目标状态，
选择框靠 `description` 里的转换关系区分两个「淋浴中」。
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
VOLUME_DEFAULT = 4

PRIORITY_MIN = 0
PRIORITY_MAX = 999
PRIORITY_DEFAULT = 100


@dataclass(frozen=True)
class VoiceEventSpec:
    """一个事件的中文正名与转换关系。"""

    value: DoorEventKind
    label: str
    description: str


def _state(state: DoorState) -> str:
    return state_label(state)


#: 事件目录：值与 `DoorEventKind` 一一对应，中文名全部来自 `DoorState` 的正名。
VOICE_EVENTS: tuple[VoiceEventSpec, ...] = (
    VoiceEventSpec(
        DoorEventKind.ASSIGNED,
        _state(DoorState.WAITING),
        f"{_state(DoorState.FREE)} / {_state(DoorState.SELECTED)} → {_state(DoorState.WAITING)}",
    ),
    VoiceEventSpec(
        DoorEventKind.SHOWER_STARTED,
        _state(DoorState.BATHING),
        f"{_state(DoorState.WAITING)} → {_state(DoorState.BATHING)}",
    ),
    VoiceEventSpec(
        DoorEventKind.PAUSED,
        _state(DoorState.PAUSED),
        f"{_state(DoorState.BATHING)} → {_state(DoorState.PAUSED)}",
    ),
    VoiceEventSpec(
        DoorEventKind.RESUMED,
        _state(DoorState.BATHING),
        f"{_state(DoorState.PAUSED)} → {_state(DoorState.BATHING)}",
    ),
    VoiceEventSpec(
        DoorEventKind.RELEASED,
        _state(DoorState.FREE),
        f"{_state(DoorState.WAITING)} / {_state(DoorState.BATHING)} / {_state(DoorState.PAUSED)}"
        f" → {_state(DoorState.FREE)}",
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


#: 可靠占位符的封闭集合。时间两个是秒级口播文本，不是裸数字。
PLACEHOLDERS: tuple[PlaceholderSpec, ...] = (
    PlaceholderSpec("door_no", "喷头号", "事件里的门号。"),
    PlaceholderSpec("cert_no", "学号", "事件里的学号。"),
    PlaceholderSpec("event", "事件目标状态", "等待开阀 / 淋浴中 / 已暂停 / 空闲。"),
    PlaceholderSpec("assigned_duration", "分配时长", "秒级；读作 12分34秒 / 12分钟。"),
    PlaceholderSpec("shower_duration", "淋浴时长", "秒级；读作 12分34秒 / 12分钟。"),
)


class VoiceEventView(BaseModel):
    value: DoorEventKind
    label: str
    description: str


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
    delay: VoiceDelayView
    volume: VoiceBoundView
    priority: VoiceBoundView


class VoiceRuleCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    cert_no: str = Field(min_length=1, max_length=64)
    event: DoorEventKind
    text: str = Field(min_length=1, max_length=120)
    delay_ms: int = Field(default=DELAY_DEFAULT_MS, ge=DELAY_MIN_MS, le=DELAY_MAX_MS)
    volume: int = Field(default=VOLUME_DEFAULT, ge=VOLUME_MIN, le=VOLUME_MAX)
    enabled: bool = True
    priority: int = Field(default=PRIORITY_DEFAULT, ge=PRIORITY_MIN, le=PRIORITY_MAX)


class VoiceRuleUpdate(BaseModel):
    cert_no: str = Field(min_length=1, max_length=64)
    event: DoorEventKind
    text: str = Field(min_length=1, max_length=120)
    delay_ms: int = Field(default=DELAY_DEFAULT_MS, ge=DELAY_MIN_MS, le=DELAY_MAX_MS)
    volume: int = Field(default=VOLUME_DEFAULT, ge=VOLUME_MIN, le=VOLUME_MAX)
    enabled: bool = True
    priority: int = Field(default=PRIORITY_DEFAULT, ge=PRIORITY_MIN, le=PRIORITY_MAX)


class VoicePreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=120)
    event: DoorEventKind
    cert_no: str = Field(default="示例学号", min_length=1, max_length=64)
    door_no: int = Field(default=1, ge=1, le=48)
    assigned_seconds: int = Field(default=1122, ge=0, le=86400)
    shower_seconds: int = Field(default=965, ge=0, le=86400)


class VoiceRuleView(BaseModel):
    id: int
    device_id: str
    cert_no: str
    event: DoorEventKind
    text: str
    delay_ms: int
    volume: int
    enabled: bool
    priority: int
    sample_text: str
    created_at: str
    updated_at: str


class VoicePreviewView(BaseModel):
    rendered_text: str
    clip_id: str
    clip_url: str
    size_bytes: int
    duration_ms: int


class VoiceStatusView(BaseModel):
    ready: bool
    model_present: bool
    piper_available: bool
    espeak_available: bool
    ffmpeg_available: bool
    clip_count: int


class VoiceDeliveryView(BaseModel):
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
    status: str
    detail: str
