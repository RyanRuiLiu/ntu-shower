"""语音播报的接口契约：固定音频素材库 + 自定义分段规则。

一条规则不再保存文本或音色，而是一串**分段**：
- `asset`：素材库里的一段固定音频；
- `digits`：把事件里的喷头号/学号拆成数字，逐位取素材库标记过的 0–9 素材；
- `pause`：静音停顿。

事件目录仍是七个状态迁移；匹配沿用「设备 × 学号 × 事件，具体压过通配」，并新增
每周重复的生效时段。
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import DoorState
from app.modules.doors.events import DoorEventKind
from app.modules.doors.schemas import state_label

from . import time_windows as time_rules

# ── 边界 ──

VOLUME_MIN = 0
VOLUME_MAX = 5
VOLUME_DEFAULT = 3

MAX_SEGMENTS_PER_RULE = 32
MAX_PAUSE_MS = 5_000
MIN_PAUSE_MS = 100
MAX_NAME_LENGTH = 40
MAX_DIGITS_LENGTH = 10

#: 目标维度的通配值：设备维度表示「全部设备」，学号维度表示「全部学号」。
WILDCARD = "*"

MAX_DEVICE_TARGETS_PER_CREATE = 50
MAX_CERT_TARGETS_PER_CREATE = 500
MAX_EXEMPT_PER_RULE = 500

ASSET_ID = r"^[0-9a-f]{64}$"


@dataclass(frozen=True)
class VoiceEventSpec:
    value: DoorEventKind
    label: str
    description: str
    phase: str


def _state(state: DoorState) -> str:
    return state_label(state)


VOICE_EVENTS: tuple[VoiceEventSpec, ...] = (
    VoiceEventSpec(
        DoorEventKind.ASSIGNED,
        f"{_state(DoorState.FREE)} / {_state(DoorState.SELECTED)} → {_state(DoorState.WAITING)}",
        "分配到喷头，等待开阀",
        "分配",
    ),
    VoiceEventSpec(
        DoorEventKind.SHOWER_STARTED,
        f"{_state(DoorState.WAITING)} → {_state(DoorState.BATHING)}",
        "开阀出水",
        "用水",
    ),
    VoiceEventSpec(
        DoorEventKind.PAUSED,
        f"{_state(DoorState.BATHING)} → {_state(DoorState.PAUSED)}",
        "中途关阀暂停",
        "用水",
    ),
    VoiceEventSpec(
        DoorEventKind.RESUMED,
        f"{_state(DoorState.PAUSED)} → {_state(DoorState.BATHING)}",
        "暂停后恢复出水",
        "用水",
    ),
    VoiceEventSpec(
        DoorEventKind.RELEASED_UNUSED,
        f"{_state(DoorState.WAITING)} → {_state(DoorState.FREE)}",
        "没开阀就被释放",
        "释放",
    ),
    VoiceEventSpec(
        DoorEventKind.RELEASED_AFTER_SHOWER,
        f"{_state(DoorState.BATHING)} → {_state(DoorState.FREE)}",
        "正常结算收尾",
        "释放",
    ),
    VoiceEventSpec(
        DoorEventKind.RELEASED_WHILE_PAUSED,
        f"{_state(DoorState.PAUSED)} → {_state(DoorState.FREE)}",
        "暂停中被释放",
        "释放",
    ),
)

# ── 分段 ──


class AssetSegment(BaseModel):
    kind: Literal["asset"]
    asset_id: str = Field(pattern=ASSET_ID)


class DigitsSegment(BaseModel):
    kind: Literal["digits"]
    source: Literal["door_no", "cert_no"]
    length: int | None = Field(default=None, ge=1, le=MAX_DIGITS_LENGTH)


class PauseSegment(BaseModel):
    kind: Literal["pause"]
    ms: int = Field(ge=MIN_PAUSE_MS, le=MAX_PAUSE_MS)


Segment = Annotated[AssetSegment | DigitsSegment | PauseSegment, Field(discriminator="kind")]


# ── 素材 ──


class VoiceAssetView(BaseModel):
    """素材库里的一段固定音频；`asset_id` 是归一化后的内容哈希。"""

    asset_id: str
    name: str
    digit: int | None
    unit: str | None
    duration_ms: int
    size_bytes: int
    sha256: str
    url: str
    created_at: str


class VoiceAssetRename(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class VoiceStatusView(BaseModel):
    asset_count: int
    digits: list[int]
    units: list[str]
    ffmpeg_available: bool


# ── 规则 ──


class TimeWindow(BaseModel):
    """每周重复的生效时段；`days` 指开始日，`start > end` 表示跨天。"""

    days: list[int] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=time_rules.TIME_PATTERN)
    end: str = Field(pattern=time_rules.TIME_PATTERN)

    @field_validator("days")
    @classmethod
    def _normalize_days(cls, value: list[int]) -> list[int]:
        return list(time_rules.normalize_days(value))

    @model_validator(mode="after")
    def _check_span(self) -> "TimeWindow":
        if time_rules.parse_time(self.start) == time_rules.parse_time(self.end):
            raise ValueError(time_rules.ERR_SPAN)
        return self

    def to_window(self) -> time_rules.Window:
        return time_rules.Window(
            days=tuple(self.days),
            start_minute=time_rules.parse_time(self.start),
            end_minute=time_rules.parse_time(self.end),
        )


class VoiceRuleCreate(BaseModel):
    devices: list[str] = Field(min_length=1, max_length=MAX_DEVICE_TARGETS_PER_CREATE)
    certs: list[str] = Field(min_length=1, max_length=MAX_CERT_TARGETS_PER_CREATE)
    events: list[DoorEventKind] = Field(min_length=1)
    time_windows: list[TimeWindow] = Field(default_factory=list, max_length=time_rules.MAX_TIME_WINDOWS_PER_RULE)
    segments: list[Segment] = Field(min_length=1, max_length=MAX_SEGMENTS_PER_RULE)
    volume: int = Field(default=VOLUME_DEFAULT, ge=VOLUME_MIN, le=VOLUME_MAX)
    enabled: bool = True
    exempt_cert_nos: list[str] = Field(default_factory=list, max_length=MAX_EXEMPT_PER_RULE)


class VoiceRuleUpdate(BaseModel):
    devices: list[str] = Field(min_length=1, max_length=MAX_DEVICE_TARGETS_PER_CREATE)
    certs: list[str] = Field(min_length=1, max_length=MAX_CERT_TARGETS_PER_CREATE)
    events: list[DoorEventKind] = Field(min_length=1)
    time_windows: list[TimeWindow] = Field(default_factory=list, max_length=time_rules.MAX_TIME_WINDOWS_PER_RULE)
    segments: list[Segment] = Field(min_length=1, max_length=MAX_SEGMENTS_PER_RULE)
    volume: int = Field(default=VOLUME_DEFAULT, ge=VOLUME_MIN, le=VOLUME_MAX)
    enabled: bool = True
    exempt_cert_nos: list[str] = Field(default_factory=list, max_length=MAX_EXEMPT_PER_RULE)


class VoiceRuleEnabledPatch(BaseModel):
    enabled: bool


class VoiceRuleView(BaseModel):
    id: int
    devices: list[str]
    certs: list[str]
    exempt: list[str]
    events: list[DoorEventKind]
    time_windows: list[TimeWindow]
    segments: list[Segment]
    summary: str
    time_summary: str
    volume: int
    enabled: bool
    created_at: str
    updated_at: str


# ── 预览 / 推送 / 投递 ──


class VoicePreviewRequest(BaseModel):
    segments: list[Segment] = Field(min_length=1, max_length=MAX_SEGMENTS_PER_RULE)
    event: DoorEventKind
    cert_no: str = ""
    door_no: int = 1


class ResolvedClip(BaseModel):
    asset_id: str
    name: str
    url: str
    sha256: str
    size_bytes: int
    duration_ms: int


class ResolvedSegment(BaseModel):
    kind: Literal["clip", "pause"]
    clip: ResolvedClip | None = None
    ms: int | None = None


class VoicePreviewView(BaseModel):
    segments: list[ResolvedSegment]
    total_ms: int
    summary: str


class VoicePushRequest(BaseModel):
    device_id: str


class VoicePushView(BaseModel):
    device_id: str
    status: str
    reason_code: str
    detail: str
    segment_count: int
    duration_ms: int


class VoiceDeliveryView(BaseModel):
    """一次播报投递：事件、设备、解析后的片段摘要与执行结果。"""

    id: int
    source: Literal["auto", "manual"]
    rule_id: int
    device_id: str
    cert_no: str
    event: DoorEventKind
    door_no: int
    segments: str
    segment_count: int
    duration_ms: int
    occurred_at: str
    dispatched_at: str
    done_at: str
    status: str
    detail: str
    downloaded: bool


class VoiceEventView(BaseModel):
    value: DoorEventKind
    label: str
    description: str
    phase: str


class VoiceBoundView(BaseModel):
    minimum: int
    maximum: int
    default: int


class VoiceSpecView(BaseModel):
    events: list[VoiceEventView]
    volume: VoiceBoundView
    digits: list[int]
    asset_count: int
    max_segments: int
    max_time_windows: int
    max_pause_ms: int
    min_pause_ms: int
