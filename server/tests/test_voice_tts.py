"""音色注册表、数字归一、分段拼装与内容寻址的单元测试。

不加载真实引擎：语音段用替身写入，WAV/MP3 由测试生成；sherpa-onnx 包的探测用
假 find_spec 固定结果（CI 里没有这个包）。
"""

import importlib.util
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


def _fake_say(monkeypatch: pytest.MonkeyPatch, seconds: float = 1.0) -> None:
    monkeypatch.setattr(tts, "_say", lambda _text, wav_path, _voice: _write_wav(wav_path, seconds))


def _fake_ffmpeg(monkeypatch: pytest.MonkeyPatch, payload: bytes = b"ID3" + b"x" * 200) -> None:
    monkeypatch.setattr(tts, "_run_ffmpeg", lambda _wav, mp3: mp3.write_bytes(payload))


def _fake_probe_ok(monkeypatch: pytest.MonkeyPatch, voice_dir: Path) -> None:
    """文件齐全 + 引擎包在（假 find_spec），probe 才为真。"""
    for spec in tts.VOICES.values():
        for name, size, _sha in spec.files:
            target = voice_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(b"\x00" * size)
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())


# ── 注册表 ────────────────────────────────────────────────────────────────


def test_registry_single_melo_voice() -> None:
    assert tts.DEFAULT_VOICE == "melo"
    assert list(tts.VOICES) == ["melo"]
    assert tts.VOICES["melo"].engine == "melo"


def test_recipe_and_clip_id_are_stable_and_text_specific() -> None:
    plan: tts.Plan = [("say", "你好")]
    base = tts.clip_id_for(plan, "melo")
    assert base == tts.clip_id_for(plan, "melo")
    assert base != tts.clip_id_for([("say", "你好。")], "melo")
    assert tts.clip_id_for([("fx", Path("e.wav")), *plan], "melo") != base
    assert len(base) == 64


def test_probe_needs_files_and_engine_package(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    assert tts.probe("melo") is False
    _fake_probe_ok(monkeypatch, voice_dir)
    assert tts.probe("melo") is True
    assert [item.key for item in tts.voice_statuses()] == ["melo"]
    assert tts.voice_statuses()[0].available is True


# ── 数字归一 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "零"),
        (5, "五"),
        (10, "十"),
        (12, "十二"),
        (20, "二十"),
        (34, "三十四"),
        (100, "一百"),
        (105, "一百零五"),
        (1122, "一千一百二十二"),
        (25000, "二五零零零"),
    ],
)
def test_int_to_zh(value: int, expected: str) -> None:
    assert tts._int_to_zh(value) == expected


def test_normalize_numerals_covers_template_quantities() -> None:
    assert tts.normalize_numerals("请到 3 号喷头，12分34秒") == "请到 三 号喷头，十二分三十四秒"
    assert tts.normalize_numerals("无数字") == "无数字"


# ── 分段渲染（render）─────────────────────────────────────────────────────


def test_render_segments_splits_fx_and_pause() -> None:
    values = render.sample_values("淋浴中", door_no=5)
    segments = render.render_segments("{fx:叮} 请到 {door_no} 号 {pause:500} 结束", values)
    assert [(seg.kind, seg.text, seg.effect, seg.pause_ms) for seg in segments] == [
        ("fx", "", "叮", 0),
        ("say", " 请到 5 号 ", "", 0),
        ("pause", "", "", 500),
        ("say", " 结束", "", 0),
    ]
    assert render.render_text(segments) == "♪叮 请到 5 号 （停顿0.5秒） 结束"


def test_render_segments_rejects_bad_pause_and_shape() -> None:
    values = render.sample_values("淋浴中")
    with pytest.raises(render.TemplateError):
        render.render_segments("{pause:50} 太短", values)
    with pytest.raises(render.TemplateError):
        render.render_segments("{pause:6000} 太长", values)
    with pytest.raises(render.TemplateError):
        render.render_segments("{fx:} 空名", values)
    with pytest.raises(render.TemplateError):
        render.render_segments("{pause:x} 非数字", values)
    only_fx = render.render_segments("{fx:叮}", values)
    assert [seg.kind for seg in only_fx] == ["fx"]


def test_cert_no_reads_digitwise_with_truncation() -> None:
    values = render.sample_values("等待开阀", cert_no="99990057")
    assert render.render_segments("{cert_no}", values)[0].text == "二五三零一一零零五七"
    assert render.render_segments("{cert_no:4}", values)[0].text == "零零五七"
    mixed = render.sample_values("等待开阀", cert_no="B12a5678")
    assert render.render_segments("{cert_no}", mixed)[0].text == "B一二a五六七八"
    doors = render.sample_values("淋浴中", door_no=5)
    assert render.render_segments("{door_no} 号", doors)[0].text == "5 号"


def test_render_rejects_missing_value_and_empty_result() -> None:
    with pytest.raises(render.TemplateError):
        render.render_segments("{event}", {})
    with pytest.raises(render.TemplateError):
        render.render_segments("{event}", {"event": ""})


def test_validate_template_rejects_unknown_and_raw_braces() -> None:
    with pytest.raises(render.TemplateError):
        render.validate_template("你好 {name}")
    with pytest.raises(render.TemplateError):
        render.validate_template("长" * (render.MAX_TEMPLATE_CHARS + 1))
    with pytest.raises(render.TemplateError):
        render.validate_template("{door_no:3}")
    with pytest.raises(render.TemplateError):
        render.validate_template("{cert_no:0} 太短")
    with pytest.raises(render.TemplateError):
        render.validate_template("{fx:} 空名")


# ── 拼装合成 ──────────────────────────────────────────────────────────────


def test_synthesize_writes_mp3(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_probe_ok(monkeypatch, voice_dir)
    _fake_say(monkeypatch, 1.0)
    _fake_ffmpeg(monkeypatch)

    plan: tts.Plan = [("say", "你好")]
    clip = tts.synthesize(plan, tts.clip_id_for(plan, "melo"), "melo")

    assert clip.path.is_file()
    assert clip.duration_ms == 1249
    assert clip.size_bytes == 203
    assert tts.sha256_of(clip.path) == clip.sha256


def test_synthesize_assembles_plan_in_order(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_probe_ok(monkeypatch, voice_dir)
    _fake_say(monkeypatch, 1.0)
    _fake_ffmpeg(monkeypatch, payload=b"ID3")
    effect = tts.effect_path("e" * 64)
    _write_wav(effect, 0.5)
    monkeypatch.setattr(tts, "decode_effect", lambda _src, _rate, target: _write_wav(target, 0.5))

    plan: tts.Plan = [("fx", effect), ("pause", 300), ("say", "你好")]
    clip = tts.synthesize(plan, tts.clip_id_for(plan, "melo"), "melo")
    # 0.5s 音效 + 0.3s 停顿 + 1.0s 语音
    assert clip.duration_ms in (2048, 2049)


def test_synthesize_plan_without_leading_say(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_probe_ok(monkeypatch, voice_dir)
    effect = tts.effect_path("f" * 64)
    _write_wav(effect, 0.25)
    monkeypatch.setattr(tts, "decode_effect", lambda _src, _rate, target: _write_wav(target, 0.25))
    _fake_ffmpeg(monkeypatch, payload=b"ID3")

    plan: tts.Plan = [("fx", effect), ("pause", 250)]
    clip = tts.synthesize(plan, tts.clip_id_for(plan, "melo"), "melo")
    # 0.25 音效 + 0.25 停顿 + 语音段（替身 0.25s：这段用例的替身时长由该用例自定）
    assert clip.duration_ms == 749

    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize([], tts.clip_id_for([], "melo"), "melo")
    bad: tts.Plan = [("bogus", "x")]  # type: ignore[list-item]
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize(bad, "0" * 64, "melo")


def test_synthesize_rejects_too_long(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_probe_ok(monkeypatch, voice_dir)
    _fake_say(monkeypatch, 31.0)
    plan: tts.Plan = [("say", "很长")]
    with pytest.raises(tts.VoiceTextTooLongError):
        tts.synthesize(plan, tts.clip_id_for(plan, "melo"), "melo")


def test_synthesize_rejects_empty_mp3(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_probe_ok(monkeypatch, voice_dir)
    _fake_say(monkeypatch)
    _fake_ffmpeg(monkeypatch, payload=b"")
    plan: tts.Plan = [("say", "空")]
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize(plan, tts.clip_id_for(plan, "melo"), "melo")


def test_synthesize_requires_available_voice(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    plan: tts.Plan = [("say", "你好")]
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize(plan, tts.clip_id_for(plan, "melo"), "melo")
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize(plan, "0" * 64, "不存在")


# ── 音效归一与 ffmpeg 守卫 ────────────────────────────────────────────────


def test_normalize_to_wav_reports_duration(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = voice_dir / "src.bin"
    source.write_bytes(b"fake-audio")
    target = voice_dir / "normalized.wav"
    monkeypatch.setattr(tts, "_run_ffmpeg_raw", lambda _args, _timeout: _write_wav(target, 0.5))
    assert tts.normalize_to_wav(source, target) == 500


def test_run_ffmpeg_failure(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "boom")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_ffmpeg(voice_dir / "a.wav", voice_dir / "a.mp3")


def test_run_ffmpeg_missing_executable(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts.shutil, "which", lambda _name: None)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_ffmpeg(voice_dir / "a.wav", voice_dir / "b.mp3")


def test_wav_duration_rejects_bad_file(voice_dir: Path) -> None:
    bad = voice_dir / "bad.wav"
    bad.write_bytes(b"not a wav")
    with pytest.raises(tts.VoiceSynthesisError):
        tts._wav_duration_ms(bad)
