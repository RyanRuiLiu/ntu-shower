"""下载语音模型（音色目录 `tts.VOICES` 的全部资产）到 `settings.voice_dir/` 并校验。

部署时手动执行一次（Melo 压缩包约 163MB；解包后按
`tts.VOICES` 的逐文件 SHA-256 精确校验，压缩包本身按字节数预检）：

    cd server
    .venv/bin/pip install sherpa-onnx   # 引擎包，装一次
    .venv/bin/python -m tools.fetch_voice_model

模型与音频都在 `data/` 下，部署脚本保留该目录；脚本可重复执行，已匹配就跳过。
"""

import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import NoReturn

from app.modules.voice import tts

#: 压缩包来源与其字节数（解包目标目录名, 与登记表文件前缀一致）。
ARCHIVES: tuple[tuple[str, str, int, str], ...] = (
    (
        "melo",
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2",
        167_006_755,
        "melo",
    ),
)


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


def _download(url: str, target: Path, size: int) -> None:
    temp = target.with_suffix(target.suffix + ".part")
    temp.unlink(missing_ok=True)
    written = 0
    print(f"下载 {target.name}（{size} 字节）…")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temp.open("wb") as out:
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
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def _extract(archive: Path, dest_name: str) -> None:
    """解包到 voice 目录下的 dest_name/（压缩包内一层目录剥掉）。"""
    root = tts.voice_root()
    with tarfile.open(archive, "r:bz2") as tar:
        members = tar.getmembers()
        inner_root = members[0].name.split("/")[0]
        for member in members:
            if not member.isfile():
                continue
            relative = Path(member.name).relative_to(inner_root)
            if len(relative.parts) == 0:
                continue
            target = root / dest_name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            target.write_bytes(source.read())
    print(f"已解包到 {dest_name}/")


def _verify_voice_dir() -> set[str]:
    """逐文件校验登记表；返回仍未就绪的文件名集合。"""
    pending: set[str] = set()
    for spec in tts.VOICES.values():
        for name, size, sha256 in spec.files:
            if name in pending:
                continue
            target = tts.voice_root() / name
            if not _matches(target, size, sha256):
                pending.add(name)
    return pending


def main() -> int:
    root = tts.voice_root()
    root.mkdir(parents=True, exist_ok=True)
    pending = _verify_voice_dir()
    if not pending:
        print(f"语音模型已就绪（{len(tts.VOICES)} 个音色）。")
        return 0

    for name, url, size, dest in ARCHIVES:
        if not any(item.startswith(f"{dest}/") for item in pending):
            print(f"跳过 {name}：登记的文件已就绪")
            continue
        archive = root / f"{name}.tar.bz2"
        _download(url, archive, size)
        _extract(archive, dest)
        archive.unlink(missing_ok=True)

    still = _verify_voice_dir()
    for item in sorted(still):
        print(f"校验未过：{item}")
    if still:
        return 1
    print(f"语音模型已就绪（{len(tts.VOICES)} 个音色）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
