"""为测试文件补充类型注解（一次性工具，用毕即删）。

按参数名映射类型；用负向断言排除函数名（def 之后）与属性/已注解写法。
import 语句统一在替换后由 ruff 排序。
"""

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent / "tests"

# 参数名 → (类型, 需要的 import)
PARAM_TYPES: dict[str, tuple[str, str | None]] = {
    "monkeypatch": ("MonkeyPatch", "from pytest import MonkeyPatch"),
    "fresh_db": ("str", None),
    "tmp_path": ("Path", "from pathlib import Path"),
    "repo": ("ModuleType", "from types import ModuleType"),
    "store": ("ModuleType", "from types import ModuleType"),
    "shadow_db": ("ModuleType", "from types import ModuleType"),
}

# _recv_until 的首参是 TestClient 的 WS 会话
RECV_UNTIL = re.compile(r"def _recv_until\(ws([,)])")
WS_IMPORT = "from starlette.testclient import WebSocketTestSession"


def ensure_import(text: str, statement: str) -> str:
    """确保 import 存在（按最末符号名判重），插入到最后一个 import 之后。"""
    symbol = statement.split()[-1].split(".")[-1]
    if re.search(rf"\b{symbol}\b", text.split("def ")[0]):
        return text
    lines = text.split("\n")
    last_import = 0
    for index, line in enumerate(lines):
        if re.match(r"^(import |from )", line):
            last_import = index
    lines.insert(last_import + 1, statement)
    return "\n".join(lines)


def patch(path: Path) -> list[str]:
    text = original = path.read_text(encoding="utf-8")
    needed: set[str] = set()

    if RECV_UNTIL.search(text):
        text = RECV_UNTIL.sub(r"def _recv_until(ws: WebSocketTestSession\1", text)
        if text != original:
            needed.add(WS_IMPORT)

    for name, (type_name, statement) in PARAM_TYPES.items():
        pattern = re.compile(rf"(?<!def )\b{name}\b(?!\s*[:.])")
        if not pattern.search(text):
            continue
        text = pattern.sub(f"{name}: {type_name}", text)
        if statement and re.search(rf"\b{name}\b: {type_name}", text):
            needed.add(statement)

    for statement in sorted(needed):
        text = ensure_import(text, statement)

    if text != original:
        path.write_text(text, encoding="utf-8")
    return sorted(needed)


changed = []
for file_path in sorted(TESTS.rglob("*.py")):
    imports = patch(file_path)
    if imports:
        changed.append((file_path.name, imports))

for name, imports in changed:
    print(f"{name}: +{', '.join(imports)}")
print(f"\n改动文件数: {len(changed)}")
