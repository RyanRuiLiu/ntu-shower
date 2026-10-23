"""Piper 中文合成、MP3 编码、内容寻址与可用性探测。

Piper 只通过子进程调用（`python -m piper`），模块导入时不依赖它：部署脚本在装依赖
之前会先 `import app.main` 做预检，语音包没装也不该把预检卡住。

模型与配置文件的字节数/SHA-256 是发布资产校验，不是运行期猜测；`fetch_voice_model`
按这里的常量核对。
"""

import ctypes.util
import hashlib
import importlib.util
import shutil
import subprocess
import sys
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from app.core.config import settings

MODEL_STEM = "zh_CN-huayan-medium"
MODEL_NAME = f"{MODEL_STEM}.onnx"
CONFIG_NAME = f"{MODEL_STEM}.onnx.json"
MODEL_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/{MODEL_NAME}"
CONFIG_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/{CONFIG_NAME}"
MODEL_SIZE = 63_201_294
MODEL_SHA256 = "9929917bf8cabb26fd528ea44d3a6699c11e87317a14765312420be230be0f3d"
CONFIG_SIZE = 4_822
CONFIG_SHA256 = "d521dc45504a8ccc99e325822b35946dd701840bfb07e3dbb31a40929ed6a82b"

#: 合成配方版本：模型/语音/参数变化时改这里或模型哈希，旧规则不会指向变了的文件。
CLIP_RECIPE = f"piper-tts-1.4.2|{MODEL_STEM}|{MODEL_SHA256}|v1"

MAX_CLIP_DURATION_MS = 30_000
MAX_CLIP_BYTES = 5 * 1024 * 1024
#: 动态占位符会生成很多最终文本；缓存必须有上限，按最近使用淘汰。
MAX_CLIP_ROWS = 500
MAX_CLIP_TOTAL_BYTES = 200 * 1024 * 1024

PIPER_TIMEOUT_S = 60
FFMPEG_TIMEOUT_S = 30


class VoiceSynthesisError(RuntimeError):
    """模型缺失、Piper/ffmpeg 失败或产物不合格。"""


class VoiceTextTooLongError(ValueError):
    """合成结果超过一条播报的物理上限。"""


@dataclass(frozen=True)
class ClipFile:
    clip_id: str
    sha256: str
    size_bytes: int
    duration_ms: int
    path: Path


@dataclass(frozen=True)
class ClipStatus:
    model_present: bool
    piper_available: bool
    espeak_available: bool
    ffmpeg_available: bool


def _fail(reason: str) -> NoReturn:
    raise VoiceSynthesisError(reason)


def _too_long(reason: str) -> NoReturn:
    raise VoiceTextTooLongError(reason)


def _fail_from(reason: str, cause: BaseException) -> NoReturn:
    raise VoiceSynthesisError(reason) from cause


def voice_root() -> Path:
    return Path(settings.voice_dir)


def models_dir() -> Path:
    return voice_root() / "models"


def clips_dir() -> Path:
    return voice_root() / "clips"


def tmp_dir() -> Path:
    return voice_root() / "tmp"


def model_paths() -> tuple[Path, Path]:
    return models_dir() / MODEL_NAME, models_dir() / CONFIG_NAME


def clip_path(clip_id: str) -> Path:
    return clips_dir() / f"{clip_id}.mp3"


def clip_id_for_text(text: str) -> str:
    return hashlib.sha256(f"{CLIP_RECIPE}\n{text}".encode()).hexdigest()


def probe() -> ClipStatus:
    model, config = model_paths()
    return ClipStatus(
        model_present=model.is_file() and config.is_file(),
        piper_available=importlib.util.find_spec("piper") is not None,
        espeak_available=shutil.which("espeak-ng") is not None or ctypes.util.find_library("espeak-ng") is not None,
        ffmpeg_available=shutil.which("ffmpeg") is not None,
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def synthesize(text: str, clip_id: str) -> ClipFile:
    """把最终文本合成 MP3，返回内容身份与文件路径。

    调用前文本已由 render 校验；这里只负责子进程、时长/大小上限与原子落盘。
    """
    model, config = model_paths()
    if not model.is_file() or not config.is_file():
        _fail("语音模型缺失，请先运行 tools.fetch_voice_model")

    clips_dir().mkdir(parents=True, exist_ok=True)
    tmp_dir().mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    wav_path = tmp_dir() / f"{clip_id}.{token}.wav"
    mp3_path = tmp_dir() / f"{clip_id}.{token}.mp3"
    try:
        _run_piper(text, wav_path)
        duration_ms = _wav_duration_ms(wav_path)
        if duration_ms > MAX_CLIP_DURATION_MS:
            _too_long(f"播报音频超过 {MAX_CLIP_DURATION_MS // 1000} 秒")
        _run_ffmpeg(wav_path, mp3_path)
        size_bytes = mp3_path.stat().st_size
        if size_bytes <= 0 or size_bytes > MAX_CLIP_BYTES:
            _fail("播报音频大小超出上限")
        digest = sha256_of(mp3_path)
        final_path = clip_path(clip_id)
        mp3_path.replace(final_path)
        return ClipFile(
            clip_id=clip_id,
            sha256=digest,
            size_bytes=size_bytes,
            duration_ms=duration_ms,
            path=final_path,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _fail_from(f"语音合成失败：{exc}", exc)
    finally:
        wav_path.unlink(missing_ok=True)
        mp3_path.unlink(missing_ok=True)


def _run_piper(text: str, wav_path: Path) -> None:
    model, _config = model_paths()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "piper",
            "--model",
            str(model),
            "--output-file",
            str(wav_path),
        ],
        input=text,
        text=True,
        capture_output=True,
        timeout=PIPER_TIMEOUT_S,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        _fail(f"Piper 失败：{detail or completed.returncode}")
    if not wav_path.is_file() or wav_path.stat().st_size <= 0:
        _fail("Piper 没有生成音频文件")


def _run_ffmpeg(wav_path: Path, mp3_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        _fail("缺少 ffmpeg")
    completed = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            "-ac",
            "1",
            "-ar",
            "22050",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT_S,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        _fail(f"ffmpeg 失败：{detail or completed.returncode}")
    if not mp3_path.is_file():
        _fail("ffmpeg 没有生成音频文件")


def _wav_duration_ms(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as source:
            frames = source.getnframes()
            rate = source.getframerate()
    except (wave.Error, EOFError) as exc:
        _fail_from(f"WAV 读取失败：{exc}", exc)
    if rate <= 0:
        _fail("WAV 采样率无效")
    return int(frames / rate * 1000)
