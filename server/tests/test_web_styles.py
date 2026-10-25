"""网页样式统一门禁。

静态样式只能落在 `web/style/` 的共享 CSS 与令牌里；视图与核心脚本不得写内联样式。
运行时才能知道的几何值（节点缩进、落点、菜单位置、KPI 占比）只能写 CSS 自定义属性，
由样式表里的 `var(--*)` 消费——这样布局规则仍只有 CSS 一处。
"""

import re
from collections.abc import Iterator
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"

STYLE_ATTR = re.compile(r'style\s*=\s*"')
#: 直接写内联属性（.style.left = ...）；只允许 .style.setProperty。
STYLE_PROP = re.compile(r"\.style\.(?!setProperty\()")
#: setProperty 只允许写自定义属性（--*）。
SET_PROP = re.compile(r"\.style\.setProperty\(\s*[\"'](?!--)")
#: 颜色只能出现在 tokens.css；CSS 选择器里的 #id 不匹配 3–8 位十六进制。
COLOR = re.compile(r"(?<![\w-])#[0-9a-fA-F]{3,8}\b|rgba?\(")
BLANK_HEADER = re.compile(r"<th>\s*</th>")
BARE_ACTION_HEADER = re.compile(r"<th>操作</th>")
FONT_SIZE = re.compile(r"font-size:\s*(?![^\n]*var\(--fs-)")
RADIUS = re.compile(r"border-radius:\s*(?![^\n]*var\(--radius)")
TRANSITION = re.compile(r"transition:\s*(?![^\n]*var\(--transition)")


def _web_sources() -> Iterator[Path]:
    yield WEB / "index.html"
    yield from sorted(WEB.glob("*.js"))
    yield from sorted((WEB / "views").glob("*.js"))
    yield from sorted((WEB / "core").glob("*.js"))


def _offenders(pattern: re.Pattern[str], paths: Iterator[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{path.relative_to(WEB.parent)}:{number} {line.strip()}")
    return found


def test_views_have_no_inline_style_attributes() -> None:
    offenders = _offenders(STYLE_ATTR, _web_sources())
    assert not offenders, "视图里出现了内联 style：\n" + "\n".join(offenders)


def test_runtime_geometry_only_sets_custom_properties() -> None:
    offenders = _offenders(STYLE_PROP, _web_sources()) + _offenders(SET_PROP, _web_sources())
    assert not offenders, "内联样式属性只能通过 --* 自定义属性写入：\n" + "\n".join(offenders)


def _style_sources() -> Iterator[Path]:
    yield from sorted((WEB / "style").glob("*.css"))


def test_tables_have_labeled_action_headers() -> None:
    """表格的动作列也必须写表头，不留空 <th>，并用 col-actions 收窄列宽。"""
    sources = (WEB / "views" / path.name for path in (WEB / "views").glob("*.js"))
    offenders = _offenders(BLANK_HEADER, sources)
    offenders += _offenders(BARE_ACTION_HEADER, (WEB / "views" / path.name for path in (WEB / "views").glob("*.js")))
    assert not offenders, "表格存在无表头或未收窄的动作列：\n" + "\n".join(offenders)


def test_colors_live_only_in_tokens() -> None:
    tokens = WEB / "style" / "tokens.css"
    offenders = _offenders(COLOR, (path for path in _style_sources() if path != tokens))
    assert not offenders, "颜色值只能写在 tokens.css：\n" + "\n".join(offenders)


def test_typography_radius_and_transition_use_tokens() -> None:
    tokens = WEB / "style" / "tokens.css"
    sources = [path for path in _style_sources() if path != tokens]
    offenders = (
        _offenders(FONT_SIZE, iter(sources)) + _offenders(RADIUS, iter(sources)) + _offenders(TRANSITION, iter(sources))
    )
    assert not offenders, "字号 / 圆角 / 过渡只能引用 tokens：\n" + "\n".join(offenders)
