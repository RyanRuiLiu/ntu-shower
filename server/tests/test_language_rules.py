"""措辞规则的执行机制（`AGENTS.md` 的「通用约束」，AGENTS.md）。

## 为什么措辞要写成测试

"兜底 / 降级 / 兼容垫层 / 回退"这几个词描述的不是文风，而是**一种设计**：
假定主方案不完整，补一个次等的。本项目的取向相反——每条通路各自完整、
按场景选（见 `docs/CAPABILITIES.md`）。用词会固化思维：注释里写着"兜底方案"
的代码，往往真的只做到次等水准。

写在 ADR 里不够——ADR 没人复查。这里把它变成门禁。

## 豁免

定义这条规则的文档必须能说出它禁止的词，否则规则无法被表述。
豁免位置登记在 [EXEMPT]，**新增须写明理由**。
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: 扫描范围：源码与文档。构建产物、依赖、缓存不在其中。
SCAN_SUFFIXES = {".py", ".kt", ".md", ".js", ".css", ".html", ".kts"}
SKIP_DIRS = {"build", ".gradle", "node_modules", ".venv", "__pycache__", ".git", ".mypy_cache"}

#: 被禁止的措辞。中文词与英文对应词一并覆盖——换个说法绕过没有意义。
#
#: 分三类，共同点是**它们都先把主方案说成不完整的**：
#:   - "兜底/降级/兼容垫层/回退"：假定主方案不够，再补一个次等的；
#:   - "推测"：假定自己会给不出结论，于是先把话说过；
#:   - "保障/防御/容错/健壮"：假定自己会出错，于是加一层并非必需的东西。
#: 本项目的取向是每条通路各自完整、按场景选，所以这三类描述在这里都不成立。
BANNED = (
    "兜底",
    "降级",
    "兼容垫层",
    "回退",
    "fallback",
    "graceful degradation",
    "推测",
    "保障",
    "防御",
    "容错",
    "健壮",
)

#: 允许出现的地方，每条都要有理由。
EXEMPT = {
    # 约束入口：它必须能说出禁止的是什么
    "AGENTS.md": "列明禁令本身",
    # 声明符合规则：这里出现禁词是在说"本模块没有这样东西"
    "android/app/src/main/java/com/ryanliu/ntushower/core/dns/DnsResolver.kt": "声明无缓存、无次等取值",
    "server/app/modules/sessions/auto_settle.py": "说明宁可报错也不用静默取值",
    "docs/ARCHITECTURE.md": "禁令写在架构总览里",
    # 本测试自身
    "server/tests/test_language_rules.py": "本文件",
}


def _scanned_files() -> list[Path]:
    return [
        p
        for p in REPO.rglob("*")
        if p.is_file() and p.suffix in SCAN_SUFFIXES and not any(part in SKIP_DIRS for part in p.parts)
    ]


def _relative(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def test_no_banned_wording_outside_exempt_files() -> None:
    """禁词只允许出现在豁免文件里。"""
    offenders: list[str] = []

    for path in _scanned_files():
        rel = _relative(path)
        if rel in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for word in BANNED:
                if word in line or word.lower() in line.lower():
                    offenders.append(f"{rel}:{lineno}  {word}  ← {line.strip()[:70]}")

    assert not offenders, "出现被禁止的措辞（见 AGENTS.md）：\n" + "\n".join(offenders)


def test_exempt_files_still_exist() -> None:
    """豁免清单里的文件必须还在——重命名后豁免会静默失效。"""
    missing = [rel for rel in EXEMPT if not (REPO / rel).is_file()]
    assert not missing, f"豁免清单指向了不存在的文件：{missing}"


def test_exempt_entries_carry_a_reason() -> None:
    """每条豁免都要写明理由；否则它只是一个"这里不管"的列表。"""
    for rel, reason in EXEMPT.items():
        assert reason.strip(), f"{rel} 的豁免缺少理由"


def test_banned_list_covers_the_named_offenders() -> None:
    """守住清单本身：被点名的措辞必须都在里面。

    守的是"有人把清单改短"——那是最容易让规则静默失效的一种改法。
    """
    named = ("兜底", "降级", "兼容垫层", "回退", "推测", "保障", "防御", "容错", "健壮")
    for word in named:
        assert word in BANNED, f"清单漏了被点名的措辞：{word}"


@pytest.mark.parametrize("word", BANNED)
def test_banned_words_are_not_empty_or_trivial(word: str) -> None:
    """守住清单里每一项的形态：空串或单字符会让上面的扫描永远命中。"""
    assert len(word) >= 2
    assert not re.fullmatch(r"\s*", word)
