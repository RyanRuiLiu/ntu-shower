"""web 两套界面引用的 API 与服务端 openapi 的对账。

跨面钉子：前端引用了一个服务端不存在的路径时，运行时表现为 404（2026-11-22
生产实证：legacy 审计页把 installs 拼进 /audit/{kind} 得到 404，静态检查与
单面测试都不拦——只有把两面的取数 URL 与 openapi 逐条比对才当场红）。

提取方式：正则抓取两类源文件的 URL 模板（api.js 的 send(`...`) 与 queries.ts
的 get/post/... 与 devicePath 调用），模板变量归一为 {x}；openapi 路径按静态
段比对。匹配规则：URL 的静态段按序全部出现在某条 openapi 路径的静态段里即
视为存在；audit/{kind} 的动态段由词表钉子单独覆盖。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]

_TEMPLATE_VAR = re.compile(r"\$\{[^}]*\}")


def _openapi_paths() -> set[str]:
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mktemp(suffix='.db')}")
    os.environ.setdefault("SESSION_SECRET", "x" * 32)
    from app.main import create_app

    spec = create_app().openapi()
    return set(spec["paths"])


def _normalize(url: str) -> str:
    return _TEMPLATE_VAR.sub("{x}", url).split("?")[0]


def _legacy_urls() -> set[str]:
    text = (_SERVER_DIR / "web-legacy/core/api.js").read_text(encoding="utf-8")
    return {_normalize(m.group(1).replace("${API_V1}", "/api/v1")) for m in re.finditer(r"send\(`([^`]+)`", text)}


def _new_urls() -> set[str]:
    text = (_SERVER_DIR / "web/src/api/queries.ts").read_text(encoding="utf-8")
    urls: set[str] = set()
    # 逐行取含 API_V1 的模板段：query 参数里嵌着内层反引号模板（?device_id=...），
    # 整体正则会截断在第一个内层反引号——按行切反引号后取含 API_V1 的那一段最稳。
    for line in text.splitlines():
        if "${API_V1}" not in line:
            continue
        segment = next(part for part in line.split("`") if "${API_V1}" in part)
        url = _normalize(segment.replace("${API_V1}", "/api/v1")).strip()
        if url.endswith("{x}"):
            urls.add(url)
        else:
            urls.add(url.rsplit("/", 1)[0])
    # devicePath(id, suffix) 拼的是 /devices/{x}<suffix>：按第二参数逐调用归一
    for match in re.finditer(r'devicePath\(\s*\w+\s*,\s*[`"\']([^`"\']+)[`"\']\s*\)', text):
        urls.add(_normalize(f"/api/v1/devices/{{x}}{match.group(1)}"))
    # devicePath(id) 单参形态 = /devices/{x}
    urls.add("/api/v1/devices/{x}")
    return urls


def _exists(url: str, paths: set[str]) -> bool:
    """URL 的静态段按序全部出现在某条 openapi 路径的静态段里，且不超过其长度。"""
    head = [seg for seg in url.strip("/").split("{")[0].strip("/").split("/") if seg]
    if not head:
        return False
    for path in paths:
        api_static = [seg for seg in path.strip("/").split("/") if not (seg.startswith("{") and seg.endswith("}"))]
        if len(head) <= len(api_static) and all(seg in api_static for seg in head):
            return True
    return False


def test_legacy_api_urls_exist_in_openapi() -> None:
    paths = _openapi_paths()
    missing = sorted(url for url in _legacy_urls() if not _exists(url, paths))
    assert not missing, f"legacy 引用了 openapi 不存在的路径: {missing}"


def test_new_api_urls_exist_in_openapi() -> None:
    paths = _openapi_paths()
    missing = sorted(url for url in _new_urls() if not _exists(url, paths))
    assert not missing, f"新版引用了 openapi 不存在的路径: {missing}"


def test_legacy_audit_kinds_exist_in_openapi() -> None:
    """audit/{kind} 的动态段单列钉住：审计取数方式的 kind 词表必须逐个是真实路径。"""
    text = (_SERVER_DIR / "web-legacy/views/audit.js").read_text(encoding="utf-8")
    audit_block = text[text.index("const TABS = {") : text.index("── 状态与渲染")]
    kinds = set(re.findall(r"kind: '([a-z-]+)'", audit_block))
    paths = _openapi_paths()
    missing = sorted(kind for kind in kinds if f"/api/v1/audit/{kind}" not in paths)
    assert not missing, f"audit kind 词表引用了不存在的路径: {missing}"
    assert kinds, "对账钉子失效：audit.js 的 TABS 里没有 kind"
