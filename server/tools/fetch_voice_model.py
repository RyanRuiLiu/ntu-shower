"""下载 Piper 中文模型到 `settings.voice_dir/models/` 并校验字节数与 SHA-256。

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


def main() -> int:
    files = (
        (tts.MODEL_NAME, tts.MODEL_URL, tts.MODEL_SIZE, tts.MODEL_SHA256),
        (tts.CONFIG_NAME, tts.CONFIG_URL, tts.CONFIG_SIZE, tts.CONFIG_SHA256),
    )
    for name, url, size, sha256 in files:
        target = tts.models_dir() / name
        if _matches(target, size, sha256):
            print(f"已就绪：{name}")
            continue
        _download(url, target, size, sha256)
        print(f"已校验：{name}")
    print("语音模型已就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
