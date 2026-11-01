"""合成 APK 清单：按 aapt 真实块布局生成只含 AndroidManifest.xml 的 ZIP。"""

import io
import struct
import zipfile


def build_apk(version: str, version_code: int, *, utf8: bool = False) -> bytes:
    """合成一个只含 AndroidManifest.xml 的 ZIP，版本属性按 aapt 的真实块布局生成。

    布局逐段对齐真机产物：字符串池 → 资源映射表 → start-element。资源映射表按 aapt
    约定与字符串池**前缀**按下标对齐——属性名字符串必须排在池的最前面。真机 APK 太大
    不进仓库，发布校验与 AXML 解析器的测试都用它。
    """
    attr_names = ("versionCode", "versionName")
    strings = [*attr_names, "manifest", str(version)]

    pool_body = bytearray()
    offsets: list[int] = []
    for text in strings:
        offsets.append(len(pool_body))
        if utf8:
            raw = text.encode("utf-8")
            pool_body += bytes([len(raw), len(raw)]) + raw + b"\x00"
        else:
            pool_body += struct.pack("<H", len(text)) + text.encode("utf-16-le") + b"\x00\x00"
    flags = (1 << 8) if utf8 else 0
    pool = (
        struct.pack(
            "<HHIIIIII",
            0x0001,
            28,
            28 + 4 * len(strings) + len(pool_body),
            len(strings),
            0,
            flags,
            28 + 4 * len(strings),
            0,
        )
        + struct.pack(f"<{len(strings)}I", *offsets)
        + bytes(pool_body)
    )

    resmap = struct.pack("<HHI", 0x0180, 8, 8 + 4 * len(attr_names)) + struct.pack(
        f"<{len(attr_names)}I", 0x0101021B, 0x0101021C
    )

    def attr(name_index: int, data_type: int, data: int, raw: int) -> bytes:
        # TypedValue：size u16（=8）+ res0 u8（=0）+ dataType u8 + data u32，合成一个 u32 放前半
        return struct.pack("<IIII", 0xFFFFFFFF, name_index, raw, 8 | (data_type << 24)) + struct.pack("<I", data)

    attributes = attr(0, 0x10, version_code, 0xFFFFFFFF) + attr(
        1, 0x03, strings.index(str(version)), strings.index(str(version))
    )
    start_element = (
        struct.pack("<HHI", 0x0102, 16, 16 + 20 + len(attributes))
        + struct.pack("<II", 1, 0xFFFFFFFF)
        + struct.pack("<IIHHHHHH", 0xFFFFFFFF, strings.index("manifest"), 20, 20, 2, 0, 0, 0)
        + attributes
    )

    total = len(pool) + len(resmap) + len(start_element)
    xml = struct.pack("<HHI", 0x0003, 8, 8 + total) + pool + resmap + start_element

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", xml)
    return buffer.getvalue()
