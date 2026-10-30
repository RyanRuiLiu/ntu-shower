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

from app.core import terms

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


# ── 术语的唯一正名（表格与扫描器在 app/core/terms.py）────────────────────────
# 这里只守两件事：表格本身的形状，以及扫描器的判据真的生效。
# "全仓没有弃用词"那条等清理跑完再加——加早了必红，而"已知违规白名单"是垫层。


def test_canonical_and_deprecated_are_populated() -> None:
    assert terms.CANONICAL, "正名表空了"
    assert terms.DEPRECATED, "弃用词表空了"


def test_every_deprecated_word_has_a_usable_replacement() -> None:
    """弃用词必须给出改法：只说"别用"等于让人自己猜。"""
    for word, suggestion in terms.DEPRECATED.items():
        assert len(word) >= 2, f"{word} 太短，会命中无关文本"
        assert suggestion.strip(), f"{word} 没有给出改法"


def test_exempt_hits_carry_a_reason_and_a_word() -> None:
    """限定词豁免：键必须是 `相对路径:词`，理由必须写清"这里的合法义是什么"。"""
    for key, reason in terms.EXEMPT_HITS.items():
        path, _, word = key.rpartition(":")
        assert path, f"{key} 不是 `相对路径:词` 的形状"
        assert word, f"{key} 的冒号后面为空"
        assert word in terms.DEPRECATED, f"{key} 里的词不在弃用词表里"
        assert len(reason.strip()) >= 10, f"{key} 的理由太短，说不清为什么合法"


def test_exempt_paths_exist() -> None:
    """豁免指向不存在的文件时应当报错，否则重命名后豁免会静默失效。"""
    for rel in terms.EXEMPT_FILES:
        assert (terms.REPO / rel).is_file(), f"EXEMPT_FILES 指向了不存在的文件：{rel}"
    for key in terms.EXEMPT_HITS:
        rel, _, _word = key.rpartition(":")
        assert (terms.REPO / rel).is_file(), f"EXEMPT_HITS 指向了不存在的文件：{rel}"


def test_scan_reports_a_deprecated_word(tmp_path: Path) -> None:
    """扫描器要真的能抓到——用临时目录造一个样本，不动仓库。"""
    word = next(iter(terms.DEPRECATED))
    (tmp_path / "sample.py").write_text(f"# 这里写了{word}\n", encoding="utf-8")

    found = terms.scan(tmp_path)

    assert [v.word for v in found] == [word]
    assert found[0].line == 1
    assert word in terms.summary(found)


def test_scan_ignores_a_clean_tree(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("# 全是我们自己的正名\n", encoding="utf-8")
    assert terms.scan(tmp_path) == []


def test_scan_skips_files_outside_the_scan_suffixes(tmp_path: Path) -> None:
    """非源码后缀不在扫描范围：二进制与资源文件里出现这些词没有意义。"""
    word = next(iter(terms.DEPRECATED))
    (tmp_path / "asset.png").write_text(word, encoding="utf-8")
    assert terms.scan(tmp_path) == []


def test_scan_honours_the_file_exemption(tmp_path: Path) -> None:
    """登记豁免的文件整份跳过。"""
    word = next(iter(terms.DEPRECATED))
    rel = "exempt.py"
    (tmp_path / rel).write_text(word, encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setitem(terms.EXEMPT_FILES, rel, "样本文件")
        assert terms.scan(tmp_path) == []
    finally:
        monkeypatch.undo()


def test_scan_honours_the_position_exemption(tmp_path: Path) -> None:
    """限定词在登记的位置上不报，换一行就报——豁免是**按位置**的，不是按词。"""
    word = next(iter(terms.DEPRECATED))
    (tmp_path / "sample.py").write_text(f"{word}\n", encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setitem(terms.EXEMPT_HITS, f"sample.py:{word}", "这一处确实是另一个意思，理由够长")
        assert terms.scan(tmp_path) == []
    finally:
        monkeypatch.undo()
    assert len(terms.scan(tmp_path)) == 1


def test_scan_honours_the_word_pattern(tmp_path: Path) -> None:
    """登记过正则的词，只在**不是**那个合法搭配时才报。

    样本用程序拼出来，不在测试里写出那个词：写出来它自己就成了违规，于是只能给它开豁免，
    而豁免是给"同形不同义"留的，不是给"我没法在测试里说它"留的。
    """
    guarded = next(iter(terms.PATTERNS))
    legit = "下" + guarded + "之前就拒"
    wrong = "只在声明的范围内" + guarded
    (tmp_path / "sample.py").write_text(f"{legit}\n{wrong}\n", encoding="utf-8")

    found = terms.scan(tmp_path)

    assert [v.word for v in found] == [guarded]
    assert [v.line for v in found] == [2], f"合法搭配那一行不该报：{found}"


def test_main_exit_code_follows_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """命令行入口：干净返回 0，有违规返回 1——CI 靠它判。"""
    monkeypatch.setattr(terms, "scan", lambda **_kwargs: [])
    assert terms._main() == 0

    sample = terms.Violation("a.py", 1, next(iter(terms.DEPRECATED)), "改法", "原文")
    monkeypatch.setattr(terms, "scan", lambda **_kwargs: [sample])
    assert terms._main() == 1


def test_repo_has_no_deprecated_terms() -> None:
    """全仓没有弃用词。

    这条是这套东西的目的本身：同一个概念只许一个中文说法。它曾经是红的（257 处），
    清理跑完才加——**不配"已知违规白名单"**：白名单就是垫层，而垫层会让这条测试
    从"守住术语"退化成"守住白名单别变长"。
    """
    found = terms.scan()
    assert not found, terms.summary(found) + "\n" + "\n".join(str(v) for v in found[:20])
