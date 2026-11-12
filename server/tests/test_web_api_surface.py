"""web API 面的适应度函数：core/api.js 的成员与调用方一一对应。

eslint 的 no-undef 只抓裸标识符，抓不到 `api.<成员>` 这种属性访问——拼错的成员名
（`api.deviseRuntime`）和零调用的死成员（包装了没人用的端点）都静默通过，直到运行时
或永远。本测试把两个方向都钉死：

- api.js 定义的每个成员，在 web 源码里至少有一个调用点（死成员=漂移）；
- web 源码里每个 `api.<x>` 引用，都能在 api.js 里找到定义（拼错=运行时才炸）。

会话包装器（login / logout / refreshSession / sessionActive）同理：它们是 hasSession
的唯一所有者，若视图绕开它们直接调 api 端点，登录态就会静默失灵——2026-11-10 的
submitLogin 事故正是这个类别（api.login 不写登录态）。每个包装器都必须有调用方。
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"

#: 会话包装器：hasSession 的唯一所有者，必须在 core/api.js 之外被调用。
SESSION_WRAPPERS = ("login", "logout", "refreshSession", "sessionActive")


def _js_sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in sorted(WEB.rglob("*.js"))
        if "node_modules" not in path.parts and path.name != "contract.js"
    }


def _api_members(source: str) -> list[str]:
    """core/api.js 的 `export const api = { … }` 里的成员名。"""
    match = re.search(r"export const api = \{(.*?)\n\}", source, re.S)
    assert match, "core/api.js 的 api 对象没找到——文件结构变了，先改这里"
    return re.findall(r"^ {2}([a-zA-Z_]+):", match.group(1), re.M)


def _callers_of(name: str, sources: dict[Path, str]) -> list[Path]:
    """web 源码里 `api.<name>` 的调用点（含 api 换行后的跨行链）；api.js 自身不算。"""
    pattern = re.compile(rf"api\s*\.\s*{re.escape(name)}(?![\w.])")
    return [path for path, text in sources.items() if path.name != "api.js" and pattern.search(text)]


def test_every_api_member_has_a_caller() -> None:
    sources = _js_sources()
    members = _api_members(sources[WEB / "core" / "api.js"])
    dead = [name for name in members if not _callers_of(name, sources)]
    assert not dead, f"api.js 的这些成员没有任何调用点（死成员=漂移）：{dead}"


def test_every_api_reference_is_defined() -> None:
    sources = _js_sources()
    members = set(_api_members(sources[WEB / "core" / "api.js"]))
    # `api.<x>` 引用（含跨行链）；排除 import 路径里的 `api.js`——成员名后跟引号的是路径
    referenced: dict[str, Path] = {}
    for path, text in sources.items():
        if path.name == "api.js":
            continue
        # lookbehind 排除路径形（`core/api.js`）：`api` 紧跟在 `/` 后面的是 import 路径
        for match in re.finditer(r"(?<![\w/'\"`])api\s*\.\s*([a-zA-Z_]+)(?![\w.])", text):
            referenced.setdefault(match.group(1), path)
    unknown = {name: str(path) for name, path in referenced.items() if name not in members}
    assert not unknown, f"web 里引用了 api.js 未定义的成员（运行时才会炸）：{unknown}"


def test_session_wrappers_are_the_only_auth_channel() -> None:
    sources = _js_sources()
    api_source = sources[WEB / "core" / "api.js"]
    for wrapper in SESSION_WRAPPERS:
        # 包装器自身在 api.js 里定义；调用方必须在其余文件里
        callers = [
            path for path, text in sources.items() if path.name != "api.js" and re.search(rf"\b{wrapper}\(", text)
        ]
        assert callers, f"会话包装器 {wrapper} 没有调用方——登录态会静默失灵（2026-11-10 submitLogin 事故类别）"
        # 端点不得绕开包装器被直接调：/auth/login 与 /auth/logout 只许出现在 api.js 与包装器实现里
        for path, text in sources.items():
            if path.name == "api.js" or path == WEB / "app.js":
                continue
            assert "/api/v1/auth/" not in text, f"{path.name} 直接引用了 auth 端点——会话必须走 {wrapper} 包装器"
    assert "/api/v1/auth/" in api_source, "api.js 必须承载 auth 端点定义"
