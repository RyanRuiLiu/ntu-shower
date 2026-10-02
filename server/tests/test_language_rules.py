"""措辞规则的执行机制（AGENTS.md 的禁令）。

"兜底 / 降级 / 兼容垫层 / 回退"等词描述的不是文风，而是一种设计：假定主方案不完整，
补一个次等的。本项目的取向是每条通路各自完整、按场景选，用词会固化思维。写在文档
里不够，这里把它变成门禁。

## 豁免

定义这条规则的文档必须能说出它禁止的词，否则规则无法被表述。豁免位置登记在
EXEMPT，新增须写明理由。
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: 扫描范围：源码与文档。构建产物、依赖、缓存不在其中。
SCAN_SUFFIXES = {".py", ".kt", ".md", ".js", ".css", ".html", ".kts"}
SKIP_DIRS = {"build", ".gradle", "node_modules", ".venv", "__pycache__", ".git", ".mypy_cache"}

#: 被禁止的措辞，中文词与英文对应词一并覆盖。
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
    "AGENTS.md": "列明禁令本身",
    "android/app/src/main/java/com/ryanliu/ntushower/core/dns/DnsResolver.kt": "声明无缓存、无次等取值",
    "docs/ARCHITECTURE.md": "禁令写在架构总览里",
    "server/tests/test_language_rules.py": "本文件",
}


def _scanned_files() -> list[Path]:
    return [
        path
        for path in REPO.rglob("*")
        if path.is_file() and path.suffix in SCAN_SUFFIXES and not any(part in SKIP_DIRS for part in path.parts)
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
                    offenders.append(f"{rel}:{lineno}  {word}  <- {line.strip()[:70]}")

    assert not offenders, "出现被禁止的措辞（见 AGENTS.md）：\n" + "\n".join(offenders)


def test_exempt_files_still_exist() -> None:
    """豁免清单里的文件必须还在：重命名后豁免会静默失效。"""
    missing = [rel for rel in EXEMPT if not (REPO / rel).is_file()]
    assert not missing, f"豁免清单指向了不存在的文件：{missing}"


def test_exempt_entries_carry_a_reason() -> None:
    for rel, reason in EXEMPT.items():
        assert reason.strip(), f"{rel} 的豁免缺少理由"


def test_banned_list_covers_the_named_offenders() -> None:
    """守住清单本身：被点名的措辞必须都在里面。"""
    named = ("兜底", "降级", "兼容垫层", "回退", "推测", "保障", "防御", "容错", "健壮")
    for word in named:
        assert word in BANNED, f"清单漏了被点名的措辞：{word}"


@pytest.mark.parametrize("word", BANNED)
def test_banned_words_are_not_empty_or_trivial(word: str) -> None:
    """空串或单字符会让扫描永远命中。"""
    assert len(word) >= 2
    assert not re.fullmatch(r"\s*", word)
