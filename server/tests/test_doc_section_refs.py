"""代码与文档里的「某文档某节」引用必须指向真实存在的节。

引用是导航，不是内容——它失效时没有任何后果可见：读的人翻过去找不到那一节，多半
以为是自己看错了。改节名、加序号、拆章节都会让引用静默失效，所以由这个测试守着。

**比对时忽略序号**：`RUNBOOK` 的节叫「5. 故障处置」，引用写成「故障处置」或
「5. 故障处置」都指同一处，两种写法都接受——否则改一次编号就要改一圈引用。
"""

import re
from pathlib import Path

DOCS = {
    "README": "README.md",
    "AGENTS": "AGENTS.md",
    "ARCHITECTURE": "docs/ARCHITECTURE.md",
    "DECISIONS": "docs/DECISIONS.md",
    "RUNBOOK": "docs/RUNBOOK.md",
}

#: 引用形如 `docs/RUNBOOK.md「5. 故障处置」` 或 `DECISIONS 的「数据分层：五张表」`。
_REFERENCE = re.compile(
    r"(?:(?:docs/)?(ARCHITECTURE|DECISIONS|RUNBOOK|AGENTS|README)\.md|(ARCHITECTURE|DECISIONS|RUNBOOK|AGENTS))"
    r"[^\S\n]*[「『]([^」』]+)[」』]"
)

#: 扫描范围：仓库里除了这些，其余文本文件都可能是引用者。
_SCANNED_SUFFIXES = (".py", ".js", ".kt", ".kts", ".sh", ".ps1", ".json", ".yml", ".md")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _titles(path: Path) -> list[str]:
    return [match.group(1).strip() for match in re.finditer(r"(?m)^#{2,4}\s+(.+)$", path.read_text(encoding="utf-8"))]


def _strip_ordinal(title: str) -> str:
    """去掉「1. 」与「ADR-0001: 」这类前缀，让引用与标题可按正文比对。"""
    title = re.sub(r"^ADR-\d{4}:\s*", "", title)
    return re.sub(r"^\d+[.、]\s*", "", title).strip()


def test_document_section_references_resolve() -> None:
    """每一处「文档 + 节名」的引用，都能在目标文档里找到对应的节。"""
    root = _repo_root()
    titles = {key: [_strip_ordinal(t) for t in _titles(root / rel)] for key, rel in DOCS.items()}

    broken: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
            continue
        if any(part in {".git", ".venv", "node_modules", "build", "__pycache__"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _REFERENCE.finditer(text):
            key = match.group(1) or match.group(2)
            section = match.group(3)
            candidates = titles.get(key)
            if candidates is None:
                broken.append(f"{path.relative_to(root)}: 引用了未知文档 {key}")
                continue
            wanted = _strip_ordinal(section)
            if not any(t == wanted or t.startswith(wanted) for t in candidates):
                broken.append(f"{path.relative_to(root)}: {key}.md 里没有「{section}」这一节")

    assert not broken, "章节引用失效：\n" + "\n".join(broken)
