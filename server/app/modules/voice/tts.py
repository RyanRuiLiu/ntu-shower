"""Piper 中文合成、分段拼装（语音/音效/停顿）、MP3 编码、内容寻址与可用性探测。

Piper 只通过子进程调用（`python -m piper`），模块导入时不依赖它：部署脚本在装依赖
之前会先 `import app.main` 做预检，语音包没装也不该把预检卡住。

## 音色与参数

音色是模型（[VOICES] 登记，含字节数/SHA-256 发布校验，`fetch_voice_model` 据此
核对）。语速、语调起伏、音长抖动与句间停顿全部经 [SynthParams] 进入合成，并共同
构成音频的内容身份（[SynthParams.recipe]）：改任何一项都会算出新的 clip_id、生成
新文件，旧缓存按最近使用淘汰，不存在"新参数读到旧音频"的组合。

## 分段拼装

一条播报是「语音段 / 音效 / 停顿」的序列（来自模板渲染，见 render.py）。合成按段
拼 WAV：语音段各跑一次 Piper（采样率由模型决定），音效从库里重采样对齐，停顿补零；
全部串起来一次性编码成 MP3——设备端拿到的仍是一段普通 MP3，零改动。音效库里的
文件统一归一化为 22050/单声道/16bit WAV（[normalize_to_wav]）。
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


@dataclass(frozen=True)
class VoiceModel:
    """一个可选音色：模型资产的大小与 SHA-256 是发布校验，不是运行期猜测。"""

    key: str
    """配置里写的音色名（`voice_model` 的取值）。"""

    stem: str
    model_size: int
    model_sha256: str
    config_size: int
    config_sha256: str
    note: str
    """配置页给运维看的一句话取舍说明。"""


#: 中文音色目录。`fetch_voice_model` 按 key 全量下载校验。
VOICES: dict[str, VoiceModel] = {
    "huayan-medium": VoiceModel(
        "huayan-medium",
        "zh_CN-huayan-medium",
        63_201_294,
        "9929917bf8cabb26fd528ea44d3a6699c11e87317a14765312420be230be0f3d",
        4_822,
        "d521dc45504a8ccc99e325822b35946dd701840bfb07e3dbb31a40929ed6a82b",
        "常速音色（默认）：自然度最好，单次合成约几秒",
    ),
    "huayan-x-low": VoiceModel(
        "huayan-x-low",
        "zh_CN-huayan-x_low",
        20_628_813,
        "d30b143fac66d821a1285aa013295adf5cd129d3cc11d70334e51c7b20662c37",
        4_164,
        "5521dcb09adf68a9bee289032f7f5af18d29bff020953429b5d223ec1f881816",
        "轻量音色：合成更快、声音更硬，适合播报很短或高峰排队明显的场景",
    ),
}
DEFAULT_VOICE = "huayan-medium"

#: 语速的边界与默认（百分比）。与 `config/spec.py` 的 `voice_speech_rate` 同一取值域，
#: 这里是合成侧的唯一换算点。
SPEECH_RATE_MIN = 50
SPEECH_RATE_MAX = 200
SPEECH_RATE_DEFAULT = 100

#: 语调起伏 / 音长抖动的边界与默认（Piper 的 noise-scale × 100，整数配置）。
NOISE_SCALE_MIN = 0
NOISE_SCALE_MAX = 100
NOISE_SCALE_DEFAULT = 67
NOISE_SCALE_W_DEFAULT = 80

MAX_CLIP_DURATION_MS = 30_000
MAX_CLIP_BYTES = 5 * 1024 * 1024
#: 动态占位符会生成很多最终文本；缓存必须有上限，按最近使用淘汰。
MAX_CLIP_ROWS = 500
MAX_CLIP_TOTAL_BYTES = 200 * 1024 * 1024

#: 音效的物理上限：它是提示音，不是音乐。
MAX_EFFECT_DURATION_MS = 10_000
MAX_EFFECT_UPLOAD_BYTES = 10 * 1024 * 1024
EFFECT_SAMPLE_RATE = 22_050

PIPER_TIMEOUT_S = 60
FFMPEG_TIMEOUT_S = 30


class VoiceSynthesisError(RuntimeError):
    """模型缺失、Piper/ffmpeg 失败或产物不合格。"""


class VoiceTextTooLongError(ValueError):
    """合成结果超过一条播报的物理上限。"""


@dataclass(frozen=True)
class SynthParams:
    """一次合成的全部参数；音频的内容身份由它们共同决定。

    语速/起伏/抖动是百分比整数（配置键的形状），换算成 Piper 的浮点在 [_piper_args]
    一处完成。
    """

    voice: str
    speech_rate: int
    noise_scale: int
    noise_scale_w: int
    sentence_silence_ms: int

    def recipe(self) -> str:
        # v3：修 `--noise-w-scale` 参数名（v2 时代名字传错，产出又慢又怪的音频，
        # 缓存按内容身份寻址，升版本让那批音频全部失效重造）
        model = VOICES[self.voice]
        return (
            f"piper-tts-1.4.2|v3|{self.voice}|{model.model_sha256}"
            f"|rate={self.speech_rate}|noise={self.noise_scale}|noise-w={self.noise_scale_w}"
            f"|silence={self.sentence_silence_ms}"
        )


#: 拼装计划的一个段：("say", 文本) / ("fx", 音效文件路径) / ("pause", 毫秒)。
PlanItem = tuple[str, "str | Path | int"]
Plan = list[PlanItem]


@dataclass(frozen=True)
class ClipFile:
    clip_id: str
    sha256: str
    size_bytes: int
    duration_ms: int
    path: Path


@dataclass(frozen=True)
class ClipStatus:
    voice: str
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


def effects_dir() -> Path:
    return voice_root() / "effects"


def tmp_dir() -> Path:
    return voice_root() / "tmp"


def model_paths(voice: str) -> tuple[Path, Path]:
    stem = VOICES[voice].stem
    return models_dir() / f"{stem}.onnx", models_dir() / f"{stem}.onnx.json"


def clip_path(clip_id: str) -> Path:
    return clips_dir() / f"{clip_id}.mp3"


def effect_path(effect_id: str) -> Path:
    return effects_dir() / f"{effect_id}.wav"


def length_scale(speech_rate: int) -> float:
    """语速百分比 → Piper 的 `--length-scale`（时长缩放，越小越快）。"""
    return 100.0 / speech_rate


def plan_identity(plan: Plan) -> str:
    """拼装计划的规范串，进内容身份：say 用原文、fx 用音效的内容身份、pause 用毫秒。"""
    parts = []
    for kind, value in plan:
        if kind == "fx":
            assert isinstance(value, (str, Path))
            parts.append(f"fx:{Path(value).stem}")
        else:
            parts.append(f"{kind}:{value}")
    return "\x1f".join(parts)


def clip_id_for(plan: Plan, params: SynthParams) -> str:
    return hashlib.sha256(f"{params.recipe()}\n{plan_identity(plan)}".encode()).hexdigest()


def probe(voice: str) -> ClipStatus:
    model, config = model_paths(voice)
    return ClipStatus(
        voice=voice,
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


def _piper_args(params: SynthParams) -> list[str]:
    """百分比整数 → Piper 的浮点参数，换算只在这里发生。"""
    return [
        "--length-scale",
        f"{length_scale(params.speech_rate):.4f}",
        "--noise-scale",
        f"{params.noise_scale / 100:.4f}",
        "--noise-w-scale",
        f"{params.noise_scale_w / 100:.4f}",
        "--sentence-silence",
        f"{params.sentence_silence_ms / 1000:.4f}",
    ]


def synthesize(plan: Plan, clip_id: str, params: SynthParams) -> ClipFile:
    """按拼装计划合成 MP3，返回内容身份与文件路径。

    计划来自模板渲染（say/fx/pause）；语音段各跑一次 Piper，音效重采样对齐，
    停顿补零，串成一个 WAV 后一次性编码。调用前模板已由 render 校验。
    """
    voice = params.voice if params.voice in VOICES else DEFAULT_VOICE
    model, config = model_paths(voice)
    if not model.is_file() or not config.is_file():
        _fail(f"音色 {voice} 的模型缺失，请先运行 tools.fetch_voice_model")

    clips_dir().mkdir(parents=True, exist_ok=True)
    tmp_dir().mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    work: list[Path] = []
    final_wav = tmp_dir() / f"{clip_id}.{token}.final.wav"
    mp3_path = tmp_dir() / f"{clip_id}.{token}.mp3"

    def temp_path(role: str) -> Path:
        path = tmp_dir() / f"{clip_id}.{token}.{role}.wav"
        work.append(path)
        return path

    try:
        rate: int | None = None
        channels = 1
        width = 2
        frames: list[bytes] = []
        for kind, value in plan:
            if kind == "say":
                say_wav = temp_path("say")
                _run_piper(str(value), say_wav, voice, _piper_args(params))
                with wave.open(str(say_wav), "rb") as source:
                    rate = source.getframerate()
                    channels = source.getnchannels()
                    width = source.getsampwidth()
                    frames.append(source.readframes(source.getnframes()))
            elif kind == "fx":
                if rate is None:
                    # 前面还没有语音段：以音效库的采样率为基准（Piper 模型同为 22050
                    # 或 16000，首个语音段会带着自己的采样率来对齐后面的音效）。
                    rate = EFFECT_SAMPLE_RATE
                fx_wav = temp_path("fx")
                assert isinstance(value, (str, Path))
                decode_effect(Path(value), rate, fx_wav)
                with wave.open(str(fx_wav), "rb") as source:
                    frames.append(source.readframes(source.getnframes()))
            elif kind == "pause":
                if rate is None:
                    rate = EFFECT_SAMPLE_RATE
                assert isinstance(value, int)
                frames.append(b"\x00" * (int(rate * value / 1000) * channels * width))
            else:
                _fail(f"未知拼装段：{kind}")
        if rate is None:
            _fail("拼装计划为空")

        with wave.open(str(final_wav), "wb") as target:
            target.setnchannels(channels)
            target.setsampwidth(width)
            target.setframerate(rate)
            target.writeframes(b"".join(frames))

        duration_ms = _wav_duration_ms(final_wav)
        if duration_ms > MAX_CLIP_DURATION_MS:
            _too_long(f"播报音频超过 {MAX_CLIP_DURATION_MS // 1000} 秒")
        _run_ffmpeg(final_wav, mp3_path)
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
        for stale in [*work, final_wav, mp3_path]:
            stale.unlink(missing_ok=True)


def decode_effect(effect: Path, sample_rate: int, target: Path) -> None:
    """把库里的音效重采样到给定采样率的单声道 16bit WAV（拼接前的对齐步骤）。"""
    _run_ffmpeg_raw(
        [
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(effect),
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-codec:a",
            "pcm_s16le",
            str(target),
        ],
        FFMPEG_TIMEOUT_S,
    )


def normalize_to_wav(source: Path, target: Path) -> int:
    """任意常见音频 → 22050/单声道/16bit WAV，返回时长毫秒。

    上传音效的收口：格式归我、时长可测、内容身份（SHA-256）按归一化后的字节算，
    同一段声音换个容器再传也不会变成两个音效。
    """
    _run_ffmpeg_raw(
        [
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ar",
            str(EFFECT_SAMPLE_RATE),
            "-ac",
            "1",
            "-codec:a",
            "pcm_s16le",
            str(target),
        ],
        FFMPEG_TIMEOUT_S,
    )
    return _wav_duration_ms(target)


def _run_piper(text: str, wav_path: Path, voice: str, args: list[str]) -> None:
    model, _config = model_paths(voice)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "piper",
            "--model",
            str(model),
            *args,
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
    _run_ffmpeg_raw(
        [
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
        FFMPEG_TIMEOUT_S,
    )
    if not mp3_path.is_file():
        _fail("ffmpeg 没有生成音频文件")


def _run_ffmpeg_raw(args: list[str], timeout_s: int) -> None:
    """跑一条 ffmpeg 命令；退出码非零或输出缺失由调用方判定。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        _fail("缺少 ffmpeg")
    completed = subprocess.run(
        [ffmpeg, *args],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        _fail(f"ffmpeg 失败：{detail or completed.returncode}")


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
