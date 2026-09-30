"""未使用符号审计（一次性工具，用毕即删）。

对 app/ 下每个模块级公开符号（函数/类/常量），统计它在整个项目
（app/ + tests/）里被引用的次数，列出引用数为 0 的符号。
Ast 级统计而非文本匹配，避免同名误判。
"""

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
TESTS = ROOT / "tests"


def definitions(path: Path) -> list[tuple[str, int, str]]:
    """模块级公开定义：(名字, 行号, 种类)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, int, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node.lineno, "def"))
        elif isinstance(node, ast.ClassDef):
            out.append((node.name, node.lineno, "class"))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    out.append((target.id, node.lineno, "const"))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper():
                out.append((node.target.id, node.lineno, "const"))
    return out


def references(path: Path) -> Counter[str]:
    """文件内所有 Name / Attribute 形式的引用计数。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    counter: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            counter[node.id] += 1
        elif isinstance(node, ast.Attribute):
            counter[node.attr] += 1
    return counter


sources = sorted([*APP.rglob("*.py"), *TESTS.rglob("*.py")])
total: Counter[str] = Counter()
for path in sources:
    total.update(references(path))

rows: list[tuple[int, str, int, str, str]] = []
for path in sorted(APP.rglob("*.py")):
    if path.name == "__init__.py":
        continue
    for name, lineno, kind in definitions(path):
        if name.startswith("_"):
            continue
        hits = total[name] - (1 if kind == "const" else 0)
        rows.append((hits, path.relative_to(ROOT).as_posix(), lineno, name, kind))

rows.sort()
print(f"{'refs':>4}  {'kind':<5}  location")
for hits, loc, lineno, name, kind in rows:
    if hits <= 0:
        print(f"{hits:>4}  {kind:<5}  {loc}:{lineno}  {name}")

print()
print(f"公开符号总数: {len(rows)}")
print(f"零引用（从未被使用）: {sum(1 for r in rows if r[0] <= 0)}")
print(f"仅 1 处引用: {sum(1 for r in rows if r[0] == 1)}")
