"""固定音频素材：目录、校验、时长与哈希。

语音素材是上传的固定文件，设备端解码拼流硬性要求 44.1kHz 单声道 PCM
（见设备端 VoicePcmAssembler），服务端不转码——素材以 **44.1kHz 单声道 MP3**
上传，这里用 mutagen（纯 Python 的音频元数据事实标准）做入闸校验并给出时长。
归一化收敛到上传方：`ffmpeg -i in.wav -ac 1 -ar 44100 -b:a 128k out.mp3`。

文件身份就是素材的 SHA-256：同一段音频换名字上传不会生成第二份文件，设备端
也按这个哈希做缓存命中。素材目录在 `data/voice/assets`，随 data 一起保留。
"""

import hashlib
from pathlib import Path
from typing import NoReturn, cast

from mutagen import MutagenError
from mutagen.mp3 import MP3, MPEGInfo

from app.core.config import settings

#: 与设备端 VoiceClipSpec 的上限同源：单段音频不超过 5MB / 30 秒。
MAX_ASSET_BYTES = 5 * 1024 * 1024
MAX_ASSET_DURATION_MS = 30_000
MIN_ASSET_DURATION_MS = 1

#: 与设备端 VoicePcmAssembler 的常量同源：拼流只接受这个采样率与声道数。
SAMPLE_RATE_HZ = 44_100
CHANNELS = 1


class AudioError(RuntimeError):
    """上传文件不是合格的素材音频。"""


def _fail(reason: str) -> NoReturn:
    raise AudioError(reason)


def assets_dir() -> Path:
    return Path(settings.voice_dir) / "assets"


def tmp_dir() -> Path:
    return Path(settings.voice_dir) / "tmp"


def asset_path(asset_id: str) -> Path:
    return assets_dir() / f"{asset_id}.mp3"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _probe_mp3(path: Path) -> tuple[int, int, float]:
    """mutagen 的 MPEGInfo 属性为动态赋值（无类型标注）、MP3.__init__ 未标注——
    唯一的未类型化边界收口在此。返回 (采样率 Hz, 声道数, 时长秒)。"""
    audio_info = cast("MPEGInfo", MP3(str(path)).info)  # type: ignore[no-untyped-call]
    # MPEGInfo 的属性是运行期动态赋值，类型标注层面不存在——动态属性用 getattr 取
    values = {name: getattr(audio_info, name) for name in ("sample_rate", "channels", "length")}
    return int(values["sample_rate"]), int(values["channels"]), float(values["length"])


def validate_mp3(path: Path) -> int:
    """校验素材是 44.1kHz 单声道 MP3 且时长/体积在限内，返回时长毫秒数。

    不转码、不修复：不合格就地拒绝，把转换责任留在上传方（模块注释有现成命令）。
    """
    try:
        sample_rate, channels, seconds = _probe_mp3(path)
    except MutagenError as exc:
        # mutagen 的全部异常（坏头、截断、不认识的结构）都派生自 MutagenError
        _fail(f"不是有效的 MP3 文件（仅接受 44.1kHz 单声道 MP3）：{exc}")
    duration_ms = round(seconds * 1000)
    if sample_rate != SAMPLE_RATE_HZ:
        _fail(f"采样率必须为 {SAMPLE_RATE_HZ}Hz（当前 {sample_rate}Hz）")
    if channels != CHANNELS:
        _fail(f"必须为单声道（当前 {channels} 声道）")
    if not (MIN_ASSET_DURATION_MS <= duration_ms <= MAX_ASSET_DURATION_MS):
        _fail(f"音频时长 {duration_ms}ms 超出 {MIN_ASSET_DURATION_MS}–{MAX_ASSET_DURATION_MS}ms")
    if path.stat().st_size > MAX_ASSET_BYTES:
        _fail(f"音频超过 {MAX_ASSET_BYTES // (1024 * 1024)}MB")
    return duration_ms
