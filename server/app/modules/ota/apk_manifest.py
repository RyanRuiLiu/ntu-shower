"""从 APK 的二进制 AndroidManifest.xml 提取 versionCode / versionName。

发布链路的最后一道闸门：发布时声明的版本必须与 APK 内容声明一致。不一致的发布
会让设备端 `pm install` 返回 Success 而版本纹丝不动，manifest 永远"有新版"，
设备陷入无限重装循环（2026-10-24 全线实测：登记 0.4.40 的发布，字节里声明的是
0.4.39/83，六台设备全部空转）。

二进制 AXML 没有官方 Python 解析器，也不为这一道闸门引入第三方依赖——这里只解析
需要的最小子集：字符串池、资源映射表与首个 start-element（即 manifest 根元素）
的属性表。块布局按 AOSP `ResourceTypes.h`；属性名经资源映射表对回
`android:versionCode`(0x0101021b) / `android:versionName`(0x0101021c)。
"""

import io
import struct
import zipfile
from dataclasses import dataclass
from typing import NoReturn

RES_XML_TYPE = 0x0003
RES_STRING_POOL_TYPE = 0x0001
RES_XML_RESOURCE_MAP_TYPE = 0x0180
RES_XML_START_ELEMENT_TYPE = 0x0102

UTF8_FLAG = 1 << 8

ATTR_VERSION_CODE = 0x0101021B
ATTR_VERSION_NAME = 0x0101021C

TYPE_INT = 0x10
TYPE_STRING = 0x03


class ApkManifestError(ValueError):
    """不是可解析的 AXML，或 manifest 根元素上没有版本属性。"""


@dataclass(frozen=True)
class ApkVersions:
    version_code: int
    version_name: str


def _fail(reason: str) -> NoReturn:
    raise ApkManifestError(reason)


def extract_versions(apk: bytes) -> ApkVersions:
    """解析 APK 字节里的 manifest 版本；任何一步不成立都抛 [ApkManifestError]。"""
    try:
        with zipfile.ZipFile(io.BytesIO(apk)) as archive:
            xml = archive.read("AndroidManifest.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        _fail(f"读不到 AndroidManifest.xml：{exc}")
    return _extract_from_xml(xml)


def _extract_from_xml(xml: bytes) -> ApkVersions:
    if len(xml) < 8:
        _fail("AndroidManifest.xml 过短")
    xml_type, _header_size, xml_size = struct.unpack_from("<HHI", xml, 0)
    if xml_type != RES_XML_TYPE or xml_size > len(xml):
        _fail("AndroidManifest.xml 不是有效的二进制 AXML")
    xml = xml[:xml_size]

    strings: list[str] = []
    resource_ids: list[int] = []
    first_start_element: bytes | None = None

    offset = 8
    while offset + 8 <= len(xml):
        chunk_type, chunk_header_size, chunk_size = struct.unpack_from("<HHI", xml, offset)
        if chunk_size < 8 or offset + chunk_size > len(xml):
            _fail("AXML 块长度越界")
        body = xml[offset : offset + chunk_size]
        if chunk_type == RES_STRING_POOL_TYPE:
            strings = _parse_string_pool(body)
        elif chunk_type == RES_XML_RESOURCE_MAP_TYPE:
            count = (chunk_size - chunk_header_size) // 4
            resource_ids = list(struct.unpack_from(f"<{count}I", body, chunk_header_size))
        elif chunk_type == RES_XML_START_ELEMENT_TYPE and first_start_element is None:
            first_start_element = body
        offset += chunk_size

    if first_start_element is None:
        _fail("AXML 里没有元素")
    return _versions_from_start_element(first_start_element, strings, resource_ids)


def _parse_string_pool(chunk: bytes) -> list[str]:
    string_count = struct.unpack_from("<I", chunk, 8)[0]
    flags = struct.unpack_from("<I", chunk, 16)[0]
    strings_start = struct.unpack_from("<I", chunk, 20)[0]
    offsets = struct.unpack_from(f"<{string_count}I", chunk, 28)
    utf8 = bool(flags & UTF8_FLAG)
    out: list[str] = []
    for offset in offsets:
        start = strings_start + offset
        out.append(_decode_string(chunk, start, utf8))
    return out


def _decode_string(chunk: bytes, start: int, utf8: bool) -> str:
    if utf8:
        char_len, pos = _decode_length8(chunk, start)
        byte_len, pos = _decode_length8(chunk, pos)
        return chunk[pos : pos + byte_len].decode("utf-8", "replace")
    char_len, pos = _decode_length16(chunk, start)
    return chunk[pos : pos + char_len * 2].decode("utf-16-le", "replace")


def _decode_length8(data: bytes, pos: int) -> tuple[int, int]:
    first = data[pos]
    if first & 0x80:
        return ((first & 0x7F) << 8) | data[pos + 1], pos + 2
    return first, pos + 1


def _decode_length16(data: bytes, pos: int) -> tuple[int, int]:
    first = struct.unpack_from("<H", data, pos)[0]
    if first & 0x8000:
        second = struct.unpack_from("<H", data, pos + 2)[0]
        return ((first & 0x7FFF) << 16) | second, pos + 4
    return first, pos + 2


def _versions_from_start_element(chunk: bytes, strings: list[str], resource_ids: list[int]) -> ApkVersions:
    chunk_header_size = struct.unpack_from("<H", chunk, 2)[0]
    # attrExt 从 headerSize 处开始：ns/name 各 4 字节，attrStart/attrSize/attrCount
    # 是其后的三个 u16（相对 attrExt 起点的偏移与个数）
    attribute_start = struct.unpack_from("<H", chunk, 24)[0]
    attribute_size = struct.unpack_from("<H", chunk, 26)[0]
    attribute_count = struct.unpack_from("<H", chunk, 28)[0]
    if attribute_size < 20:
        _fail("AXML 属性结构尺寸异常")
    code: int | None = None
    name: str | None = None
    for i in range(attribute_count):
        base = chunk_header_size + attribute_start + i * attribute_size
        name_index = struct.unpack_from("<I", chunk, base + 4)[0]
        raw_value = struct.unpack_from("<I", chunk, base + 8)[0]
        data_type = chunk[base + 15]
        data = struct.unpack_from("<I", chunk, base + 16)[0]
        if name_index >= len(resource_ids):
            continue
        resource_id = resource_ids[name_index]
        if resource_id == ATTR_VERSION_CODE and data_type == TYPE_INT:
            code = data
        elif resource_id == ATTR_VERSION_NAME:
            value_index = raw_value if data_type == TYPE_STRING and raw_value < len(strings) else data
            if value_index < len(strings):
                name = strings[value_index]
    if code is None or name is None:
        _fail("manifest 根元素上没有 versionCode/versionName")
    return ApkVersions(code, name)
