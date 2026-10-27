"""固定音频素材：目录、转码、时长与校验。

语音不再合成，素材全部是上传/导入的固定文件。这里只负责把任意常见音频归一成设备
能播的 MP3，并给出时长、字节数与内容哈希；规则与投递不关心源格式。

文件身份就是归一化后的 SHA-256：同一段音频换名字上传不会生成第二份文件，设备端
也按这个哈希做缓存命中。素材目录在 `data/voice/assets`，随 data 一起保留。
"""

import hashlib
import subprocess
from pathlib import Path
from typing import NoReturn

from app.core.config import settings

#: 与设备端 VoiceClipSpec 的上限同源：单段音频不超过 5MB / 30 秒。
MAX_ASSET_BYTES = 5 * 1024 * 1024
MAX_ASSET_DURATION_MS = 30_000
MIN_ASSET_DURATION_MS = 1

FFMPEG_TIMEOUT_S = 30


class AudioError(RuntimeError):
    """上传文件无法归一化或产物不合格。"""


def _fail(reason: str) -> NoReturn:
    raise AudioError(reason)


def assets_dir() -> Path:
    return Path(settings.voice_dir) / "assets"


def tmp_dir() -> Path:
    return Path(settings.voice_dir) / "tmp"


def legacy_effects_dir() -> Path:
    """旧音效库的文件目录；只在一次性重建时读取。"""
    return Path(settings.voice_dir) / "effects"


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


def probe_duration_ms(path: Path) -> int:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(f"无法读取音频时长：{exc}")
    if result.returncode != 0:
        _fail(f"无法读取音频时长：{result.stderr.strip()[:200]}")
    try:
        seconds = float(result.stdout.strip())
    except ValueError:
        _fail("音频时长不是有效数字")
    duration_ms = round(seconds * 1000)
    if not (MIN_ASSET_DURATION_MS <= duration_ms <= MAX_ASSET_DURATION_MS):
        _fail(f"音频时长 {duration_ms}ms 超出 {MIN_ASSET_DURATION_MS}–{MAX_ASSET_DURATION_MS}ms")
    return duration_ms


def normalize_to_mp3(source: Path, target: Path) -> int:
    """把任意常见音频转成 44.1k 单声道 MP3，返回时长。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part.mp3")
    tmp.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-b:a",
                "128k",
                str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        tmp.unlink(missing_ok=True)
        _fail(f"音频转换失败：{exc}")
    if result.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        _fail(f"音频转换失败：{result.stderr.strip()[:200]}")
    try:
        duration_ms = probe_duration_ms(tmp)
        if tmp.stat().st_size > MAX_ASSET_BYTES:
            _fail(f"音频超过 {MAX_ASSET_BYTES // (1024 * 1024)}MB")
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return duration_ms
