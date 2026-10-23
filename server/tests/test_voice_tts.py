"""播报模板、秒级口播格式、Piper/ffmpeg 子进程与模型文件的单元测试。

不加载真实模型、不启动 Piper：子进程用 CompletedProcess 替身，WAV/MP3 由测试写入。
"""

import subprocess
import wave
from pathlib import Path

import pytest

from app.core.config import settings
from app.modules.voice import render, tts


@pytest.fixture
def voice_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "voice"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "voice_dir", str(root))
    return root


def _write_wav(path: Path, seconds: float, rate: int = 22050) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(b"\x00\x00" * int(rate * seconds))


def _touch_models() -> None:
    model, config = tts.model_paths()
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model")
    config.write_bytes(b"{}")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0秒"),
        (35, "35秒"),
        (60, "1分钟"),
        (61, "1分1秒"),
        (754, "12分34秒"),
        (1122, "18分42秒"),
        (965, "16分5秒"),
    ],
)
def test_spoken_duration(seconds: int, expected: str) -> None:
    assert render.spoken_duration(seconds) == expected


def test_validate_template_accepts_closed_placeholders() -> None:
    assert render.validate_template("{event}，{door_no} 号喷头") == ("event", "door_no")
    assert render.has_placeholders("{door_no}") is True
    assert render.has_placeholders("固定内容") is False


@pytest.mark.parametrize(
    "text",
    ["", "   ", "{name}", "{door_no", "door_no}", "{" + "x" * 120 + "}"],
)
def test_validate_template_rejects_bad_shapes(text: str) -> None:
    with pytest.raises(render.TemplateError):
        render.validate_template(text)


def test_render_template_uses_values_and_limits() -> None:
    values = render.sample_values("淋浴中", door_no=5, assigned_seconds=1122, shower_seconds=965)
    assert (
        render.render_template("{event}，{door_no} 号，分配 {assigned_duration}", values)
        == "淋浴中，5 号，分配 18分42秒"
    )
    with pytest.raises(render.TemplateError):
        render.render_template("{event} {missing}", values)
    with pytest.raises(render.TemplateError):
        render.render_template("长" * 200, values)


def test_render_rejects_missing_value() -> None:
    with pytest.raises(render.TemplateError):
        render.render_template("{event}", {})


def test_render_rejects_too_long_rendered() -> None:
    with pytest.raises(render.TemplateError):
        render.render_template("x" * 110 + "{cert_no}", {"cert_no": "C" * 60})


def test_render_rejects_empty_rendered() -> None:
    with pytest.raises(render.TemplateError):
        render.render_template("{event}", {"event": ""})


def test_clip_id_is_stable_and_text_specific() -> None:
    first = tts.clip_id_for_text("你好")
    assert first == tts.clip_id_for_text("你好")
    assert first != tts.clip_id_for_text("你好。")
    assert len(first) == 64


def test_sha256_of(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc")
    assert tts.sha256_of(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_synthesize_writes_mp3(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_piper(_text: str, wav_path: Path) -> None:
        _write_wav(wav_path, 1.0)

    def fake_ffmpeg(_wav: Path, mp3: Path) -> None:
        mp3.write_bytes(b"ID3" + b"x" * 200)

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    monkeypatch.setattr(tts, "_run_ffmpeg", fake_ffmpeg)

    clip = tts.synthesize("你好", tts.clip_id_for_text("你好"))

    assert clip.path.is_file()
    assert clip.duration_ms == 1000
    assert clip.size_bytes == 203
    assert tts.sha256_of(clip.path) == clip.sha256


def test_synthesize_rejects_too_long(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_piper(_text: str, wav_path: Path) -> None:
        _write_wav(wav_path, 31.0)

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    with pytest.raises(tts.VoiceTextTooLongError):
        tts.synthesize("很长", tts.clip_id_for_text("很长"))


def test_synthesize_rejects_empty_mp3(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_piper(_text: str, wav_path: Path) -> None:
        _write_wav(wav_path, 1.0)

    def fake_ffmpeg(_wav: Path, mp3: Path) -> None:
        mp3.write_bytes(b"")

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    monkeypatch.setattr(tts, "_run_ffmpeg", fake_ffmpeg)
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize("空", tts.clip_id_for_text("空"))


def test_synthesize_wraps_os_error(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_piper(_text: str, _wav_path: Path) -> None:
        message = "disk"
        raise OSError(message)

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize("你好", tts.clip_id_for_text("你好"))


def test_synthesize_requires_models(voice_dir: Path) -> None:
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize("你好", tts.clip_id_for_text("你好"))


def test_run_piper_uses_stdin_and_declares_output(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()
    calls: list[list[str]] = []
    inputs: list[str | None] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        inputs.append(kwargs.get("input") if isinstance(kwargs.get("input"), str) else None)
        output = Path(args[args.index("--output-file") + 1])
        _write_wav(output, 0.2)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    wav = voice_dir / "tmp" / "out.wav"
    tts._run_piper("你好", wav)

    assert calls[0][1:3] == ["-m", "piper"]
    assert "--model" in calls[0]
    assert calls[0][calls[0].index("--output-file") + 1] == str(wav)
    assert inputs == ["你好"]


def test_run_piper_failure(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "boom")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_piper("你好", voice_dir / "tmp" / "out.wav")


def test_run_piper_missing_output(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_piper("你好", voice_dir / "out.wav")


def test_run_ffmpeg_failure(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "boom")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_ffmpeg(voice_dir / "a.wav", voice_dir / "a.mp3")


def test_run_ffmpeg_missing_output(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_ffmpeg(voice_dir / "a.wav", voice_dir / "a.mp3")


def test_run_ffmpeg_success_and_missing(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        Path(args[-1]).write_bytes(b"ID3")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    tts._run_ffmpeg(voice_dir / "a.wav", voice_dir / "a.mp3")
    assert calls
    assert calls[0][-1].endswith("a.mp3")
    assert "libmp3lame" in calls[0]

    monkeypatch.setattr(tts.shutil, "which", lambda _name: None)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_ffmpeg(voice_dir / "a.wav", voice_dir / "b.mp3")


def test_wav_duration_rejects_bad_file(voice_dir: Path) -> None:
    bad = voice_dir / "bad.wav"
    bad.write_bytes(b"not a wav")
    with pytest.raises(tts.VoiceSynthesisError):
        tts._wav_duration_ms(bad)


class _FakeWave:
    def __enter__(self) -> "_FakeWave":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def getnframes(self) -> int:
        return 10

    def getframerate(self) -> int:
        return 0


def test_wav_duration_rejects_zero_rate(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts.wave, "open", lambda *_args, **_kwargs: _FakeWave())
    with pytest.raises(tts.VoiceSynthesisError):
        tts._wav_duration_ms(voice_dir / "zero.wav")


def test_probe_reads_model_presence(voice_dir: Path) -> None:
    status = tts.probe()
    assert status.model_present is False
    _touch_models()
    assert tts.probe().model_present is True
