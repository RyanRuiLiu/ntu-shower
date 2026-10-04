"""名单的两种表示之间的转换：设备上的文件原文 ↔ 行。

**行尾只在这里拼**，而且照设备上那份原件的形状来：CRLF，最后一行也带。不改成 LF 是
因为浴室 App 怎么读它我们没看过源码，而"按原样"不需要看过它。
"""

#: 一行一个学号，行尾一律 CRLF 且最后一行也带——原件是 188 字节 17 行、17 个 CR 配
#: 17 个 LF，逐字节数过。
LINE_ENDING = "\r\n"


def render(students: list[str]) -> str:
    """名单 → 文件原文。"""
    return "".join(f"{student}{LINE_ENDING}" for student in students)


def parse(content: str) -> list[str]:
    """文件原文 → 名单。

    空行丢掉：原件末尾本来就有行尾，按行拆必得一个空项；文件里真有别的空行时它也不是
    学号，留着只会在页面上显示成一个空行、再被提交回来时被拒。
    """
    return [line for line in content.splitlines() if line.strip()]
