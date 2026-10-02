"""版本号一致性：服务端入口、pyproject、设备端构建脚本三处必须相同。

APK versionName 与服务端发布记录一致是 OTA 的强制要求；把约定变成可判红的测试，
避免"改一处忘另一处"。
"""

import re
from pathlib import Path

from app.core.version import APP_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]

_PYPROJECT = REPO_ROOT / "server" / "pyproject.toml"
_ANDROID_BUILD = REPO_ROOT / "android" / "app" / "build.gradle.kts"


def test_pyproject_version_matches_app_version() -> None:
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)

    assert match is not None, "pyproject.toml 缺少 [project] version"
    assert match.group(1) == APP_VERSION


def test_android_version_name_matches_app_version() -> None:
    text = _ANDROID_BUILD.read_text(encoding="utf-8")
    match = re.search(r'versionName\s*=\s*"([^"]+)"', text)

    assert match is not None, "build.gradle.kts 缺少 versionName"
    assert match.group(1) == APP_VERSION
