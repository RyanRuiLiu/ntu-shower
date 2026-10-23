"""语音合成：音色注册表（Melo / Kokoro）、sherpa-onnx 引擎、分段拼装与内容寻址。

## 音色与引擎

音色是**规则级字段**：每条规则自己选（`voice_rule.voice`，取值见 [VOICES]）。合成
没有其它可调参数——上一版曾把语速/起伏/抖动/停顿做成全局配置，实测既难听出差别、
又接连引入参数名与默认值的坑（见 [recipe] 的版本注释），全部移除，回归引擎自身的
默认朗读。

引擎走 sherpa-onnx（进程内加载并常驻——163MB 的模型每次现加载要数秒，不可接受）。
Melo 的中文词典不认阿拉伯数字，进引擎前把数字串归一成中文数量读法
（[normalize_numerals]）。说话人的正式名字等现场试听后
定，先按序号上线。

## 分段拼装

一条播报是「语音段 / 音效 / 停顿」的序列（来自模板渲染，见 render.py）。合成按段
拼 WAV：语音段交给引擎、音效从库里重采样对齐、停顿补零；全部串起来一次性编码成
MP3——设备端拿到的仍是一段普通 MP3，零改动。音效库里的文件统一归一化为
22050/单声道/16bit WAV（[normalize_to_wav]）。
"""

import hashlib
import importlib.util
import re
import shutil
import subprocess
import threading
import uuid
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Protocol

from app.core.config import settings

DEFAULT_VOICE = "melo"

DEFAULT_VOICE = "melo"


@dataclass(frozen=True)
class VoiceSpec:
    """一个可选音色：引擎、模型资产与发布校验、一句话说明。"""

    key: str
    engine: str
    note: str
    files: tuple[tuple[str, int, str], ...]
    """发布校验：相对 voice 目录的文件名、字节数、SHA-256。"""


#: 音色目录（暂时只有 Melo——现场试听淘汰了 Piper 两个音色，Kokoro 待补）。
#: `fetch_voice_model` 按它全量下载校验；网页的音色下拉也由它驱动。
VOICES: dict[str, VoiceSpec] = {
    "melo": VoiceSpec(
        key="melo",
        engine="melo",
        note="MeloTTS：中英混读最稳",
        files=(
            (
                "melo/model.onnx",
                170_429_550,
                "bf30582eb1b012250a35b1a4a80e7dfbcf8485e7bb9de0d95efbbeef0e4ad86d",
            ),
            (
                "melo/lexicon.txt",
                6_837_671,
                "7236884b02435ac5d10cf69b4be40a61b45aa676b5300f0e412f185748fee528",
            ),
            (
                "melo/tokens.txt",
                655,
                "d18664a7e12bd7ea1022ddaf951e534e136815016c5a809d6b64156bffb4369d",
            ),
        ),
    ),
}


class VoiceSynthesisError(RuntimeError):
    """模型缺失、引擎失败或产物不合格。"""


class VoiceTextTooLongError(ValueError):
    """合成结果超过一条播报的物理上限。"""


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
class VoiceStatusItem:
    key: str
    engine: str
    note: str
    available: bool


def _fail(reason: str) -> NoReturn:
    raise VoiceSynthesisError(reason)


def _too_long(reason: str) -> NoReturn:
    raise VoiceTextTooLongError(reason)


def _fail_from(reason: str, cause: BaseException) -> NoReturn:
    raise VoiceSynthesisError(reason) from cause


def voice_root() -> Path:
    return Path(settings.voice_dir)


def clips_dir() -> Path:
    return voice_root() / "clips"


def effects_dir() -> Path:
    return voice_root() / "effects"


def tmp_dir() -> Path:
    return voice_root() / "tmp"


def clip_path(clip_id: str) -> Path:
    return clips_dir() / f"{clip_id}.mp3"


def effect_path(effect_id: str) -> Path:
    return effects_dir() / f"{effect_id}.wav"


MAX_CLIP_DURATION_MS = 30_000
MAX_CLIP_BYTES = 5 * 1024 * 1024
#: 动态占位符会生成很多最终文本；缓存必须有上限，按最近使用淘汰。
MAX_CLIP_ROWS = 500
MAX_CLIP_TOTAL_BYTES = 200 * 1024 * 1024

#: 音效的物理上限：它是提示音，不是音乐。
MAX_EFFECT_DURATION_MS = 10_000
MAX_EFFECT_UPLOAD_BYTES = 10 * 1024 * 1024
EFFECT_SAMPLE_RATE = 22_050

FFMPEG_TIMEOUT_S = 30


def recipe(voice: str) -> str:
    """音频的内容身份之一：引擎 + 模型指纹 + 说话人。

    v6：huayan/Piper 全部下线，引擎统一为 sherpa-onnx，音色成为规则级字段、合成
    参数全部移除（v4/v5 曾把参数显式传给 Piper——参数名传错与默认值设错两个事故
    都出自那里）。
    """
    spec = VOICES[voice]
    model_hash = next(h for name, _size, h in spec.files if name.endswith(".onnx"))
    return f"{spec.engine}|v6|{spec.key}|{model_hash[:16]}"


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


def clip_id_for(plan: Plan, voice: str) -> str:
    return hashlib.sha256(f"{recipe(voice)}\n{plan_identity(plan)}".encode()).hexdigest()


def probe(voice: str) -> bool:
    """这个音色现在能不能用：文件齐全且 sherpa-onnx 包在。"""
    spec = VOICES[voice]
    if importlib.util.find_spec("sherpa_onnx") is None:
        return False
    return all((voice_root() / name).is_file() for name, _size, _h in spec.files)


def voice_statuses() -> list[VoiceStatusItem]:
    return [
        VoiceStatusItem(key=spec.key, engine=spec.engine, note=spec.note, available=probe(spec.key))
        for spec in VOICES.values()
    ]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── 引擎：实例按引擎缓存，加载一次常驻 ────────────────────────────────────


class _Generated(Protocol):
    """sherpa 引擎一次合成的产物（它没有类型存根，这里只声明用到的那两样）。"""

    samples: "Iterable[float]"
    sample_rate: int


class _Engine(Protocol):
    def generate(self, text: str, sid: int, speed: float) -> _Generated: ...


_engine_lock = threading.Lock()
_engines: dict[str, "_Engine"] = {}


def _melo_engine() -> "_Engine":
    import sherpa_onnx

    base = voice_root() / "melo"
    engine: _Engine = sherpa_onnx.OfflineTts(
        sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(base / "model.onnx"),
                    lexicon=str(base / "lexicon.txt"),
                    tokens=str(base / "tokens.txt"),
                    data_dir=str(base / "dict"),
                ),
            ),
        )
    )
    return engine


def _engine_of(spec: VoiceSpec) -> "_Engine":
    with _engine_lock:
        engine = _engines.get(spec.engine)
        if engine is None:
            try:
                engine = _melo_engine()
            except Exception as exc:  # noqa: BLE001 —— 引擎装载失败要说人话
                _fail_from(f"引擎 {spec.engine} 装载失败：{exc}", exc)
            _engines[spec.engine] = engine
        return engine


def _say(text: str, wav_path: Path, voice: str) -> None:
    spec = VOICES[voice]
    engine = _engine_of(spec)
    # 两个引擎的中文词典都直接丢阿拉伯数字（实测 OOV skipped），进引擎前统一归一
    say_text = normalize_numerals(text)
    try:
        audio = engine.generate(say_text, sid=spec.sid, speed=1.0)
    except Exception as exc:  # noqa: BLE001 —— 引擎报错原样带上
        _fail_from(f"{spec.engine} 合成失败：{exc}", exc)
    import array

    # 钳制到 int16 范围：引擎偶发输出略超 ±1.0 的样本，直接转整型会回绕成电音
    frames = array.array("h", (max(-32768, min(32767, int(sample * 32767))) for sample in audio.samples))
    with wave.open(str(wav_path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(audio.sample_rate)
        target.writeframes(frames.tobytes())


_NUM_UNITS = "零一二三四五六七八九"


def _int_to_zh(value: int) -> str:
    """0–9999 的数量读法（十二、三十四百以内都覆盖；更大的位数直接逐位念）。"""
    if value >= 10_000:
        return "".join(_NUM_UNITS[int(ch)] for ch in str(value))
    if value < 10:
        return _NUM_UNITS[value]
    units = ["", "十", "百", "千"]
    digits = str(value)
    parts: list[str] = []
    for index, ch in enumerate(digits):
        digit = int(ch)
        position = len(digits) - index - 1
        if digit == 0:
            if parts and not parts[-1].endswith("零"):
                parts.append("零")
        elif position == 1 and digit == 1 and len(digits) == 2:
            parts.append("十")
        else:
            parts.append(_NUM_UNITS[digit] + units[position])
    return "".join(parts).rstrip("零") or "零"


def normalize_numerals(text: str) -> str:
    """把文本里的阿拉伯数字串换成中文数量读法（12→十二）。

    Melo 的词典不认阿拉伯数字（直接丢字）；学号在渲染层已是逐位中文，这里兜住
    模板里手写的数量（「3 号喷头」→「三号喷头」）。
    """
    return re.sub(r"\d+", lambda match: _int_to_zh(int(match.group())), text)


def synthesize(plan: Plan, clip_id: str, voice: str) -> ClipFile:
    """按拼装计划合成 MP3，返回内容身份与文件路径。

    计划来自模板渲染（say/fx/pause）；语音段交给该音色的引擎，音效重采样对齐，
    停顿补零，串成一个 WAV 后一次性编码。调用前模板已由 render 校验。
    """
    if voice not in VOICES:
        _fail(f"未知音色：{voice}")
    if not probe(voice):
        _fail(f"音色 {voice} 不可用（文件或 sherpa-onnx 包缺失），请先运行 tools.fetch_voice_model")

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
                assert isinstance(value, str)
                _say(value, say_wav, voice)
                with wave.open(str(say_wav), "rb") as source:
                    rate = source.getframerate()
                    channels = source.getnchannels()
                    width = source.getsampwidth()
                    frames.append(source.readframes(source.getnframes()))
            elif kind == "fx":
                if rate is None:
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

        # 首尾各垫静音：起播瞬间的首字吞音与收播截尾都在这里统一消掉
        frames.insert(0, b"\x00" * (int(rate * 0.12) * channels * width))
        frames.append(b"\x00" * (int(rate * 0.25) * channels * width))
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
