"""应用版本单一来源。

三处版本号必须一致（OTA 强制要求，见 AGENTS.md）：
- 本文件 APP_VERSION
- server/pyproject.toml 的 [project] version
- android/app/build.gradle.kts 的 versionName

一致性由 tests/test_version_consistency.py 强制校验——是执行机制，不是人工约定。
"""

APP_VERSION = "0.4.27"
