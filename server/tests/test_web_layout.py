"""网页端布局的硬约束：窄屏规则只有一处，且必须最后加载。

## 为什么要有这个文件

窄屏样式曾经散在五个文件里，断点值各不相同（1200／1100／1100／1024／640）。
没有任何机制能发现它们已经不一致：CSS 不报错，它只是安静地按后加载的那条生效，
表现出来是"某一档宽度下某块布局不对"，而那时已经没人记得有几处写过它。

这里守三件事：`@media` 只在 `style/narrow.css` 里、断点只有一个值、`index.html`
把它排在所有样式表之后（排不到最后就覆盖不了上面的规则）。

守的是**规则本身**，不是某个视口下的渲染结果：像素级的布局要人看，机器守不住，
机器能守住的是"规则不许再散开"。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STYLE_DIR = REPO / "server" / "web" / "style"
NARROW = STYLE_DIR / "narrow.css"
REDUCED_MOTION = STYLE_DIR / "reduced-motion.css"
INDEX = REPO / "server" / "web" / "index.html"

#: 断点的写法。只认这一种形状：`@media (width <= 1024px)`。换成 `max-width` 或区间写法
#: 要同步改这里——认不出来的形状等于没被这条测试看见。
BREAKPOINT = re.compile(r"@media\s*\(\s*width\s*<=\s*(\d+)px\s*\)")

#: 「顶栏之下的剩余高度」只许由 flex 分配（见 base.css）。写成视口高度减顶栏高度的话，
#: 顶栏在窄屏换成两行时每个减数都得跟着改，漏掉一处的表现是内容被裁或面板被顶开，
#: 而不是报错——这条测试就是为了让它别以那种形式回来。
VIEWPORT_SUBTRACTION = re.compile(r"calc\(\s*100(?:d)?vh\s*-\s*var\(--bar-height\)")


def _css_files() -> list[Path]:
    return sorted(STYLE_DIR.glob("*.css"))


def test_narrow_style_exists() -> None:
    assert NARROW.is_file(), f"窄屏样式文件不见了：{NARROW}"


def test_media_queries_live_in_one_file() -> None:
    """别处的 `@media` 一律报出来：那正是这条规矩要挡的事。"""
    offenders: list[str] = []
    for path in _css_files():
        if path in (NARROW, REDUCED_MOTION):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "@media" in line:
                offenders.append(f"{path.name}:{lineno}  {line.strip()[:70]}")
    assert not offenders, "窄屏规则只允许写在 style/narrow.css：\n" + "\n".join(offenders)


def test_narrow_carries_exactly_one_breakpoint() -> None:
    values = {int(value) for value in BREAKPOINT.findall(NARROW.read_text(encoding="utf-8"))}
    assert len(values) == 1, f"断点只允许一个值，实际有：{sorted(values)}"


def test_narrow_loads_after_every_other_stylesheet() -> None:
    sheets = re.findall(r'<link rel="stylesheet" href="style/([^"]+)"', INDEX.read_text(encoding="utf-8"))
    assert sheets, "index.html 里没有找到样式表引用"
    assert sheets[-1] == "narrow.css", f"窄屏样式要排在最后（它负责改写上面的规则），实际顺序：{sheets}"


def test_view_height_is_not_computed_from_the_bar_height() -> None:
    offenders: list[str] = []
    for path in _css_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if VIEWPORT_SUBTRACTION.search(line):
                offenders.append(f"{path.name}:{lineno}  {line.strip()[:70]}")
    assert not offenders, "「顶栏之下的剩余高度」交给 flex 分配，不要写视口高度减法：\n" + "\n".join(offenders)
