"""固定音频素材的目录、哈希与 ffmpeg 边界。

外部命令用替身：CI 无需真装 ffmpeg，同时覆盖成功/失败/超限三条出口。
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.modules.voice import audio


@pytest.fixture
def voice_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "voice"
    monkeypatch.setattr(settings, "voice_dir", str(root))
    return root


def _completed(args: list[str], code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, code, stdout, stderr)


def test_paths_and_sha256(voice_root: Path) -> None:
    assert audio.assets_dir() == voice_root / "assets"
    assert audio.tmp_dir() == voice_root / "tmp"
    assert audio.legacy_effects_dir() == voice_root / "effects"
    asset_id = "a" * 64
    assert audio.asset_path(asset_id) == voice_root / "assets" / f"{asset_id}.mp3"

    path = voice_root / "sample.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"hello")
    assert audio.sha256_file(path) == hashlib.sha256(b"hello").hexdigest()


def test_probe_duration_ms(voice_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = voice_root / "a.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda args, **_kwargs: _completed(list(args), stdout="1.234\n"),
    )
    assert audio.probe_duration_ms(path) == 1_234

    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda args, **_kwargs: _completed(list(args), code=1, stderr="bad"),
    )
    with pytest.raises(audio.AudioError, match="无法读取音频时长"):
        audio.probe_duration_ms(path)

    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda args, **_kwargs: _completed(list(args), stdout="not-a-number"),
    )
    with pytest.raises(audio.AudioError, match="不是有效数字"):
        audio.probe_duration_ms(path)

    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda args, **_kwargs: _completed(list(args), stdout="0.000"),
    )
    with pytest.raises(audio.AudioError, match="超出"):
        audio.probe_duration_ms(path)

    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda args, **_kwargs: _completed(list(args), stdout="30.001"),
    )
    with pytest.raises(audio.AudioError, match="超出"):
        audio.probe_duration_ms(path)


def test_normalize_success(voice_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = voice_root / "src.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    target = voice_root / "nested" / "out.mp3"

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        values = list(args)
        if values[0] == "ffprobe":
            return _completed(values, stdout="0.750")
        Path(values[-1]).write_bytes(b"MP3")
        return _completed(values)

    monkeypatch.setattr(audio.subprocess, "run", run)
    assert audio.normalize_to_mp3(source, target) == 750
    assert target.read_bytes() == b"MP3"
    assert not target.with_suffix(".part.mp3").exists()


def test_normalize_failure_cleans_part(voice_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = voice_root / "src.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    target = voice_root / "out.mp3"

    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda args, **_kwargs: _completed(list(args), code=1, stderr="boom"),
    )
    with pytest.raises(audio.AudioError, match="音频转换失败"):
        audio.normalize_to_mp3(source, target)
    assert not target.exists()
    assert not target.with_suffix(".part.mp3").exists()


def test_normalize_rejects_oversize(voice_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = voice_root / "src.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    target = voice_root / "out.mp3"

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        values = list(args)
        if values[0] == "ffprobe":
            return _completed(values, stdout="1.000")
        Path(values[-1]).write_bytes(b"MP3")
        return _completed(values)

    monkeypatch.setattr(audio.subprocess, "run", run)
    monkeypatch.setattr(audio, "MAX_ASSET_BYTES", 2)
    with pytest.raises(audio.AudioError, match="音频超过"):
        audio.normalize_to_mp3(source, target)
    assert not target.exists()
    assert not target.with_suffix(".part.mp3").exists()


def test_normalize_probe_failure_cleans_part(voice_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = voice_root / "src.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    target = voice_root / "out.mp3"

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        values = list(args)
        if values[0] == "ffprobe":
            return _completed(values, code=1, stderr="bad")
        Path(values[-1]).write_bytes(b"MP3")
        return _completed(values)

    monkeypatch.setattr(audio.subprocess, "run", run)
    with pytest.raises(audio.AudioError, match="无法读取音频时长"):
        audio.normalize_to_mp3(source, target)
    assert not target.with_suffix(".part.mp3").exists()


def test_probe_reports_missing_executable(voice_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = voice_root / "a.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    def missing(_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        message = "ffprobe"
        raise FileNotFoundError(message)

    monkeypatch.setattr(audio.subprocess, "run", missing)
    with pytest.raises(audio.AudioError, match="无法读取音频时长"):
        audio.probe_duration_ms(path)


def test_normalize_reports_timeout(voice_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = voice_root / "src.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    target = voice_root / "out.mp3"

    def timeout(_args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["ffmpeg"], audio.FFMPEG_TIMEOUT_S)

    monkeypatch.setattr(audio.subprocess, "run", timeout)
    with pytest.raises(audio.AudioError, match="音频转换失败"):
        audio.normalize_to_mp3(source, target)
    assert not target.with_suffix(".part.mp3").exists()
