"""文档体系检查：登记、标题、链接、编号、措辞、代码围栏与表格。

规则来源：STANDARDS.md §9.8 与 §8。证据记录保留原始章节编号，不受手动编号检查约束；
记录类文档可以写项目版本号。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.version import APP_VERSION

REPO = Path(__file__).resolve().parents[2]


def _registered() -> tuple[str, ...]:
    text = (REPO / "STANDARDS.md").read_text(encoding="utf-8")
    start = text.index("### 9.2 文档登记表")
    end = text.index("### 9.3", start)
    paths: list[str] = []
    for line in text[start:end].splitlines():
        match = re.match(r"\|\s*`([^`]+)`", line)
        if not match:
            continue
        path = match.group(1)
        if path.endswith(".md") and "*" not in path:
            paths.append(path)
    return tuple(dict.fromkeys(paths))


REGISTERED = _registered()

SKIP_DIRS = {".git", ".venv", "build", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle"}
RECORD_DOCS = {"CHANGELOG.md", "docs/DECISIONS.md"}
EVIDENCE_PREFIX = "docs/evidence/"

#: 文档中禁止的模糊、时间漂移与空泛措辞。代码注释不受本检查约束（见 STANDARDS.md §7.10）。
STYLE_WORDS = ("我们", "可能", "目前", "以后", "将来", "通常", "一般", "建议", "尽量")
NUMBERED_HEADING = re.compile(r"^#{2,4}\s+(?:\d+(?:\.\d+)*\.?|[一二三四五六七八九十]+、)\s")
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _relative(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _docs() -> list[Path]:
    return sorted(
        path
        for path in REPO.rglob("*.md")
        if path.is_file() and not any(part in SKIP_DIRS for part in path.relative_to(REPO).parts)
    )


def _registered_docs() -> list[Path]:
    return [REPO / rel for rel in REGISTERED]


def _is_evidence(rel: str) -> bool:
    return rel.startswith(EVIDENCE_PREFIX)


def _without_fences(path: Path) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            result.append((lineno, line))
    return result


def test_every_markdown_is_registered_or_evidence() -> None:
    allowed = set(REGISTERED)
    offenders = [rel for rel in map(_relative, _docs()) if rel not in allowed and not _is_evidence(rel)]
    assert not offenders, f"未登记的 Markdown 文件：{offenders}"


def test_every_registered_document_exists() -> None:
    missing = [rel for rel in REGISTERED if not (REPO / rel).is_file()]
    assert not missing, f"登记表中的文件不存在：{missing}"


@pytest.mark.parametrize("path", _docs(), ids=_relative)
def test_document_has_one_h1(path: Path) -> None:
    h1 = [text for _lineno, text in _without_fences(path) if re.match(r"^#\s+\S", text)]
    assert len(h1) == 1, f"{_relative(path)} 应只有一个一级标题，实际 {len(h1)} 个"


@pytest.mark.parametrize("path", _docs(), ids=_relative)
def test_living_document_has_no_manual_heading_numbers(path: Path) -> None:
    rel = _relative(path)
    if rel in {"STANDARDS.md", "docs/DECISIONS.md"} or _is_evidence(rel):
        return
    offenders = [(lineno, text) for lineno, text in _without_fences(path) if NUMBERED_HEADING.match(text)]
    assert not offenders, f"{rel} 使用了手工章节编号：{offenders}"


@pytest.mark.parametrize("path", _docs(), ids=_relative)
def test_relative_links_resolve(path: Path) -> None:
    missing: list[str] = []
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        target = target.split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if not (path.parent / target).resolve().exists():
            missing.append(target)
    assert not missing, f"{_relative(path)} 存在失效链接：{missing}"


@pytest.mark.parametrize("path", _docs(), ids=_relative)
def test_living_document_has_no_vague_wording(path: Path) -> None:
    rel = _relative(path)
    if _is_evidence(rel):
        return
    text = path.read_text(encoding="utf-8")
    offenders = [word for word in STYLE_WORDS if word in text]
    assert not offenders, f"{rel} 出现模糊或时间漂移措辞：{offenders}"


@pytest.mark.parametrize("path", _docs(), ids=_relative)
def test_living_document_does_not_hardcode_project_version(path: Path) -> None:
    rel = _relative(path)
    if rel in RECORD_DOCS or _is_evidence(rel):
        return
    assert APP_VERSION not in path.read_text(encoding="utf-8"), f"{rel} 写死了项目版本号 {APP_VERSION}"


@pytest.mark.parametrize("path", _docs(), ids=_relative)
def test_code_fences_are_balanced_and_tagged(path: Path) -> None:
    in_fence = False
    offenders: list[int] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip().startswith("```"):
            continue
        if not in_fence:
            if line.strip() == "```":
                offenders.append(lineno)
            in_fence = True
        else:
            in_fence = False
    assert not in_fence, f"{_relative(path)} 的代码围栏未闭合"
    assert not offenders, f"{_relative(path)} 的代码围栏缺少语言标记：{offenders}"


TEXT_SUFFIXES = {".py", ".kt", ".js", ".md", ".yml", ".yaml", ".toml", ".sh", ".ps1", ".kts", ".json"}


def _text_files() -> list[Path]:
    return [
        path
        for path in REPO.rglob("*")
        if path.is_file()
        and path.suffix in TEXT_SUFFIXES
        and not any(part in SKIP_DIRS for part in path.relative_to(REPO).parts)
        and _relative(path) != "server/tests/test_docs.py"
    ]


def test_every_document_has_an_inbound_reference() -> None:
    texts = {path: path.read_text(encoding="utf-8", errors="ignore") for path in _text_files()}
    missing: list[str] = []
    for rel in REGISTERED:
        if rel == "README.md":
            continue
        if not any(rel in text for path, text in texts.items() if _relative(path) != rel):
            missing.append(rel)
    for path in _docs():
        rel = _relative(path)
        if not _is_evidence(rel):
            continue
        if not any(rel in text for other, text in texts.items() if _relative(other) != rel):
            missing.append(rel)
    assert not missing, f"没有入链的文档：{missing}"


@pytest.mark.parametrize("path", _docs(), ids=_relative)
def test_tables_have_consistent_columns(path: Path) -> None:
    offenders: list[tuple[int, list[int]]] = []
    block: list[str] = []
    start = 0
    for lineno, line in _without_fences(path):
        if line.strip().startswith("|"):
            if not block:
                start = lineno
            block.append(line)
            continue
        if block:
            counts = [len(re.findall(r"(?<!\\)\|", item)) for item in block]
            if len(set(counts)) != 1:
                offenders.append((start, counts))
            block = []
    if block:
        counts = [len(re.findall(r"(?<!\\)\|", item)) for item in block]
        if len(set(counts)) != 1:
            offenders.append((start, counts))
    assert not offenders, f"{_relative(path)} 的表格列数不一致：{offenders}"
