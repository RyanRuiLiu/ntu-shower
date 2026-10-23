"""APK 二进制 manifest 解析：真实块布局、两种字符串池与各类残缺输入。

解析器没有官方参照实现可依赖，测试用合成 APK（`helpers.build_apk`，块布局逐段
对齐 aapt 产物）加残缺输入双向夹住；真实 APK 的解析结果已与 `aapt dump badging`
对照验证过一次（2026-10-24，0.4.39/83 与 0.4.40/84 各一）。
"""

import io
import struct
import zipfile

import pytest
from tests.helpers import build_apk

from app.modules.ota import apk_manifest


def _zip_with(manifest: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", manifest)
    return buffer.getvalue()


def test_extracts_versions_from_utf16_pool() -> None:
    assert apk_manifest.extract_versions(build_apk("0.4.41", 85)) == apk_manifest.ApkVersions(85, "0.4.41")


def test_extracts_versions_from_utf8_pool() -> None:
    assert apk_manifest.extract_versions(build_apk("1.2.3", 7, utf8=True)) == apk_manifest.ApkVersions(7, "1.2.3")


def test_rejects_bytes_that_are_not_a_zip() -> None:
    with pytest.raises(apk_manifest.ApkManifestError):
        apk_manifest.extract_versions(b"garbage")


def test_rejects_zip_without_manifest() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("other.txt", b"x")
    with pytest.raises(apk_manifest.ApkManifestError):
        apk_manifest.extract_versions(buffer.getvalue())


def test_rejects_manifest_that_is_not_axml() -> None:
    with pytest.raises(apk_manifest.ApkManifestError):
        apk_manifest.extract_versions(_zip_with(b"not axml at all"))


def test_rejects_truncated_axml() -> None:
    xml = build_apk("0.4.41", 85)
    wrapper_size = struct.unpack_from("<I", xml, 4)[0]
    with pytest.raises(apk_manifest.ApkManifestError):
        apk_manifest.extract_versions(_zip_with(xml[: wrapper_size - 1]))


def test_rejects_axml_without_start_element() -> None:
    xml = build_apk("0.4.41", 85)
    # 找到 start-element 块并整个删掉：只剩字符串池与资源映射表
    offset = 8
    while offset + 8 <= len(xml):
        chunk_type, _hs, chunk_size = struct.unpack_from("<HHI", xml, offset)
        if chunk_type == apk_manifest.RES_XML_START_ELEMENT_TYPE:
            xml = xml[:offset] + xml[offset + chunk_size :]
            break
        offset += chunk_size
    rebuilt = struct.pack("<HHI", 0x0003, 8, len(xml) - 8) + xml[8:]
    with pytest.raises(apk_manifest.ApkManifestError):
        apk_manifest.extract_versions(_zip_with(rebuilt))
