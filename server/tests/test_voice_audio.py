"""固定音频素材的校验、哈希与目录。

mutagen 是纯 Python 解析：测试用入库的真实 MP3 fixture（tests/data/voice/，
44.1kHz 单声道与 48kHz 立体声各一段，各约 1.6KB），不需要 ffmpeg 或替身。
"""

import hashlib
import shutil
from pathlib import Path

import pytest

from app.core.config import settings
from app.modules.voice import audio

FIXTURES = Path(__file__).resolve().parent / "data" / "voice"
MONO44 = FIXTURES / "mono44.mp3"
STEREO48 = FIXTURES / "stereo48.mp3"


@pytest.fixture
def voice_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "voice"
    monkeypatch.setattr(settings, "voice_dir", str(root))
    return root


def test_paths_and_sha256(voice_root: Path) -> None:
    assert audio.assets_dir() == voice_root / "assets"
    assert audio.tmp_dir() == voice_root / "tmp"
    asset_id = "a" * 64
    assert audio.asset_path(asset_id) == voice_root / "assets" / f"{asset_id}.mp3"

    path = voice_root / "sample.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"hello")
    assert audio.sha256_file(path) == hashlib.sha256(b"hello").hexdigest()


def test_validate_accepts_mono_44100(voice_root: Path, tmp_path: Path) -> None:
    """合格素材：44.1kHz 单声道、时长在限内，返回毫秒时长。"""
    target = tmp_path / "mono.mp3"
    shutil.copyfile(MONO44, target)
    duration_ms = audio.validate_mp3(target)
    # fixture 是 0.3 秒正弦音；mutagen 按 CBR 帧数计算，允许解码器误差
    assert 250 <= duration_ms <= 450


def test_validate_rejects_not_mp3(voice_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "bad.mp3"
    target.write_bytes(b"not-an-mp3")
    with pytest.raises(audio.AudioError, match="不是有效的 MP3"):
        audio.validate_mp3(target)


def test_validate_rejects_wrong_sample_rate(tmp_path: Path) -> None:
    target = tmp_path / "stereo.mp3"
    shutil.copyfile(STEREO48, target)
    with pytest.raises(audio.AudioError, match="采样率必须为 44100Hz"):
        audio.validate_mp3(target)


def test_validate_rejects_stereo(voice_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """设备端只认单声道。48kHz 立体声 fixture 先撞采样率；把采样率常量放宽后验证声道闸。"""
    target = voice_root / "mono-but-stereo.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(STEREO48.read_bytes())
    with pytest.raises(audio.AudioError, match="采样率"):
        audio.validate_mp3(target)
    monkeypatch.setattr(audio, "SAMPLE_RATE_HZ", 48_000)
    with pytest.raises(audio.AudioError, match="单声道"):
        audio.validate_mp3(target)


def test_validate_rejects_duration_over_limit(
    voice_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "mono.mp3"
    shutil.copyfile(MONO44, target)
    monkeypatch.setattr(audio, "MAX_ASSET_DURATION_MS", 100)
    with pytest.raises(audio.AudioError, match="超出"):
        audio.validate_mp3(target)


def test_validate_rejects_duration_under_limit(
    voice_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "mono.mp3"
    shutil.copyfile(MONO44, target)
    monkeypatch.setattr(audio, "MIN_ASSET_DURATION_MS", 500)
    with pytest.raises(audio.AudioError, match="超出"):
        audio.validate_mp3(target)


def test_validate_rejects_oversize(voice_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "mono.mp3"
    shutil.copyfile(MONO44, target)
    monkeypatch.setattr(audio, "MAX_ASSET_BYTES", 2)
    with pytest.raises(audio.AudioError, match="超过"):
        audio.validate_mp3(target)
