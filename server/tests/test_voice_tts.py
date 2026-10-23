"""播报模板、秒级口播格式、合成参数（音色/语速/起伏/停顿/音效）与子进程的单元测试。

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


def _touch_models(voice: str = tts.DEFAULT_VOICE) -> None:
    model, config = tts.model_paths(voice)
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model")
    config.write_bytes(b"{}")


def _params(**overrides: object) -> tts.SynthParams:
    values: dict[str, object] = {
        "voice": tts.DEFAULT_VOICE,
        "speech_rate": 100,
        "noise_scale": 67,
        "noise_scale_w": 80,
        "sentence_silence_ms": 0,
    }
    values.update(overrides)
    return tts.SynthParams(**values)


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


def test_cert_no_reads_digitwise_with_truncation() -> None:
    values = render.sample_values("等待开阀", cert_no="2530110057")
    # 学号是标识不是数量:逐位念;:N 只念末 N 位;字母按字母名念原样保留
    assert render.render_segments("{cert_no}", values)[0].text == "二五三零一一零零五七"
    assert render.render_segments("{cert_no:4}", values)[0].text == "零零五七"
    mixed = render.sample_values("等待开阀", cert_no="B12a5678")
    assert render.render_segments("{cert_no}", mixed)[0].text == "B一二a五六七八"


def test_render_segments_splits_fx_and_pause() -> None:
    values = render.sample_values("淋浴中", door_no=5)
    segments = render.render_segments("{fx:叮} 请到 {door_no} 号 {pause:500} 结束", values)
    assert [(seg.kind, seg.text, seg.effect, seg.pause_ms) for seg in segments] == [
        ("fx", "", "叮", 0),
        ("say", " 请到 5 号 ", "", 0),
        ("pause", "", "", 500),
        ("say", " 结束", "", 0),
    ]
    # 展示文本把音效与停顿写成人能读的记号,不进 TTS
    assert render.render_text(segments) == "♪叮 请到 5 号 （停顿0.5秒） 结束"


def test_validate_template_rejects_too_long_and_raw_braces() -> None:
    with pytest.raises(render.TemplateError):
        render.validate_template("长" * (render.MAX_TEMPLATE_CHARS + 1))
    with pytest.raises(render.TemplateError):
        render.validate_template("你好 {name}")


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
    # 只有效果没有语音也是一条合法播报(纯提示音)
    only_fx = render.render_segments("{fx:叮}", values)
    assert [seg.kind for seg in only_fx] == ["fx"]


def test_clip_id_covers_params_and_plan() -> None:
    plan = [("say", "你好")]
    base = tts.clip_id_for(plan, _params())
    assert base == tts.clip_id_for(plan, _params())
    assert base != tts.clip_id_for([("say", "你好。")], _params())
    # 任何一项参数变化都是新的内容身份——不存在"新参数读到旧音频"
    changed = {
        "speech_rate": 150,
        "noise_scale": 30,
        "noise_scale_w": 50,
        "sentence_silence_ms": 400,
        "voice": "huayan-x-low",
    }
    for key, value in changed.items():
        assert base != tts.clip_id_for(plan, _params(**{key: value})), key
    # 计划里的音效与停顿同样进内容身份
    assert base != tts.clip_id_for([("fx", Path("e.wav")), *plan], _params())
    assert base != tts.clip_id_for([("pause", 300), *plan], _params())
    assert len(base) == 64


def test_length_scale_maps_speech_rate() -> None:
    assert tts.length_scale(100) == 1.0
    assert tts.length_scale(50) == 2.0
    assert tts.length_scale(200) == 0.5


def test_speech_rate_bounds_match_config_spec() -> None:
    from app.modules.config.spec import SPECS_BY_KEY

    entry = SPECS_BY_KEY["voice_speech_rate"]
    assert entry.minimum == tts.SPEECH_RATE_MIN
    assert entry.maximum == tts.SPEECH_RATE_MAX
    assert entry.default == tts.SPEECH_RATE_DEFAULT


def test_voice_model_choices_match_registry() -> None:
    from app.modules.config.spec import SPECS_BY_KEY

    entry = SPECS_BY_KEY["voice_model"]
    assert entry.choices == tuple(tts.VOICES.keys())
    assert entry.default == tts.DEFAULT_VOICE


def test_sha256_of(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc")
    assert tts.sha256_of(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_synthesize_writes_mp3(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_piper(_text: str, wav_path: Path, _voice: str, _args: list[str]) -> None:
        _write_wav(wav_path, 1.0)

    def fake_ffmpeg(_wav: Path, mp3: Path) -> None:
        mp3.write_bytes(b"ID3" + b"x" * 200)

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    monkeypatch.setattr(tts, "_run_ffmpeg", fake_ffmpeg)

    plan: tts.Plan = [("say", "你好")]
    clip = tts.synthesize(plan, tts.clip_id_for(plan, _params()), _params())

    assert clip.path.is_file()
    assert clip.duration_ms == 1000
    assert clip.size_bytes == 203
    assert tts.sha256_of(clip.path) == clip.sha256


def test_synthesize_plan_without_leading_say(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()
    effect = tts.effect_path("f" * 64)
    _write_wav(effect, 0.25)
    monkeypatch.setattr(tts, "decode_effect", lambda _src, _rate, target: _write_wav(target, 0.25))
    monkeypatch.setattr(tts, "_run_ffmpeg", lambda _wav, mp3: mp3.write_bytes(b"ID3"))

    # 音效与停顿先于任何语音段:以库的采样率为基准(0.25s 音效 + 0.25s 静音)
    plan: tts.Plan = [("fx", effect), ("pause", 250)]
    clip = tts.synthesize(plan, tts.clip_id_for(plan, _params()), _params())
    assert clip.duration_ms in (499, 500)

    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize([], tts.clip_id_for([], _params()), _params())
    bad: tts.Plan = [("bogus", "x")]  # type: ignore[list-item]
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize(bad, "0" * 64, _params())


def test_synthesize_assembles_plan_in_order(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()
    effect = tts.effect_path("e" * 64)
    _write_wav(effect, 0.5)

    def fake_piper(_text: str, wav_path: Path, _voice: str, _args: list[str]) -> None:
        _write_wav(wav_path, 1.0)

    def fake_ffmpeg(_wav: Path, mp3: Path) -> None:
        mp3.write_bytes(b"ID3")

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    monkeypatch.setattr(tts, "_run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(tts, "decode_effect", lambda _src, _rate, target: _write_wav(target, 0.5))

    plan: tts.Plan = [("fx", effect), ("pause", 300), ("say", "你好")]
    clip = tts.synthesize(plan, tts.clip_id_for(plan, _params()), _params())

    # 0.5s 音效 + 0.3s 停顿 + 1.0s 语音
    assert clip.duration_ms == 1800


def test_synthesize_rejects_too_long(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_piper(_text: str, wav_path: Path, _voice: str, _args: list[str]) -> None:
        _write_wav(wav_path, 31.0)

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    plan: tts.Plan = [("say", "很长")]
    with pytest.raises(tts.VoiceTextTooLongError):
        tts.synthesize(plan, tts.clip_id_for(plan, _params()), _params())


def test_synthesize_rejects_empty_mp3(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_piper(_text: str, wav_path: Path, _voice: str, _args: list[str]) -> None:
        _write_wav(wav_path, 1.0)

    def fake_ffmpeg(_wav: Path, mp3: Path) -> None:
        mp3.write_bytes(b"")

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    monkeypatch.setattr(tts, "_run_ffmpeg", fake_ffmpeg)
    plan: tts.Plan = [("say", "空")]
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize(plan, tts.clip_id_for(plan, _params()), _params())


def test_synthesize_wraps_os_error(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_piper(_text: str, _wav_path: Path, _voice: str, _args: list[str]) -> None:
        message = "disk"
        raise OSError(message)

    monkeypatch.setattr(tts, "_run_piper", fake_piper)
    plan: tts.Plan = [("say", "你好")]
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize(plan, tts.clip_id_for(plan, _params()), _params())


def test_synthesize_requires_models(voice_dir: Path) -> None:
    plan: tts.Plan = [("say", "你好")]
    with pytest.raises(tts.VoiceSynthesisError):
        tts.synthesize(plan, tts.clip_id_for(plan, _params()), _params())


def test_run_piper_passes_all_synth_args(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    params = _params(speech_rate=200, noise_scale=30, noise_scale_w=50, sentence_silence_ms=400)
    tts._run_piper("你好", wav, tts.DEFAULT_VOICE, tts._piper_args(params))

    args = calls[0]
    assert args[1:3] == ["-m", "piper"]
    assert "--model" in args
    assert args[args.index("--length-scale") + 1] == "0.5000"
    assert args[args.index("--noise-scale") + 1] == "0.3000"
    assert args[args.index("--noise-w-scale") + 1] == "0.5000"
    assert args[args.index("--sentence-silence") + 1] == "0.4000"
    assert args[args.index("--output-file") + 1] == str(wav)
    assert inputs == ["你好"]


def test_run_piper_failure(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "boom")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_piper("你好", voice_dir / "tmp" / "out.wav", tts.DEFAULT_VOICE, tts._piper_args(_params()))


def test_run_piper_missing_output(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch_models()

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.VoiceSynthesisError):
        tts._run_piper("你好", voice_dir / "out.wav", tts.DEFAULT_VOICE, tts._piper_args(_params()))


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


def test_probe_reads_model_presence_per_voice(voice_dir: Path) -> None:
    assert tts.probe(tts.DEFAULT_VOICE).model_present is False
    _touch_models()
    assert tts.probe(tts.DEFAULT_VOICE).model_present is True
    # 只备了默认音色的文件：另一个音色仍是缺失
    assert tts.probe("huayan-x-low").model_present is False
    _touch_models("huayan-x-low")
    assert tts.probe("huayan-x-low").model_present is True


def test_normalize_to_wav_reports_duration(voice_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = voice_dir / "src.bin"
    source.write_bytes(b"fake-audio")
    target = voice_dir / "normalized.wav"
    monkeypatch.setattr(tts, "_run_ffmpeg_raw", lambda _args, _timeout: _write_wav(target, 0.5))
    assert tts.normalize_to_wav(source, target) == 500
