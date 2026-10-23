"""下载 Piper 中文模型（全部音色，见 `tts.VOICES`）到 `settings.voice_dir/models/`
并校验字节数与 SHA-256。

部署时手动执行一次：

    cd server
    .venv/bin/python -m tools.fetch_voice_model

模型与音频都在 `data/` 下，部署脚本保留该目录；脚本可重复执行，已匹配就跳过。
"""

import hashlib
import sys
import urllib.request
from pathlib import Path
from typing import NoReturn

from app.modules.voice import tts


def _fail(reason: str) -> NoReturn:
    raise RuntimeError(reason)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches(path: Path, size: int, sha256: str) -> bool:
    return path.is_file() and path.stat().st_size == size and _sha256(path) == sha256


def _download(url: str, target: Path, size: int, sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.part")
    temp.unlink(missing_ok=True)
    written = 0
    print(f"下载 {target.name}（{size} 字节）…")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temp.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > size:
                    _fail(f"{target.name} 超过声明的字节数")
                out.write(chunk)
        if written != size:
            _fail(f"{target.name} 长度不符：{written} != {size}")
        actual = _sha256(temp)
        if actual != sha256:
            _fail(f"{target.name} 校验失败：{actual} != {sha256}")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def _files() -> list[tuple[str, str, int, str]]:
    files: list[tuple[str, str, int, str]] = []
    for voice in tts.VOICES.values():
        # stem 形如 zh_CN-huayan-medium → 仓库路径 zh/zh_CN/{speaker}/{quality}/{stem}
        _lang, speaker, quality = voice.stem.split("-")
        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/{speaker}/{quality}/{voice.stem}"
        files.append((f"{voice.stem}.onnx", f"{base}.onnx", voice.model_size, voice.model_sha256))
        files.append((f"{voice.stem}.onnx.json", f"{base}.onnx.json", voice.config_size, voice.config_sha256))
    return files


def main() -> int:
    for name, url, size, sha256 in _files():
        target = tts.models_dir() / name
        if _matches(target, size, sha256):
            print(f"已就绪：{name}")
            continue
        _download(url, target, size, sha256)
        print(f"已校验：{name}")
    print(f"语音模型已就绪（{len(tts.VOICES)} 个音色）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
