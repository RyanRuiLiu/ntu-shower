"""术语的唯一出处：正名表、弃用词表、全仓扫描器。

## 为什么要有这个文件

同一个概念在仓库里被叫成好几样，读注释的人就得先在脑子里做一次映射，而映射做错了
**不会报错**——只会让他把两件事当成一件。2026-10-04 定死：一个概念一个中文正名。

规矩两条：

1. **一个概念一个中文正名**；注释、文档、界面文案、提交信息一律用它。
2. **标识符不另起中文别名**。列名、动作名、状态取值本身就是标识符，说话时直接写
   标识符。跨表同名的列必须**带表名说**：`usage_session.assigned_source`（起点来源）
   与 `usage_segment.source`（段长来源）是两个不同的量。

## 为什么弃用词要写死在代码里

弃用词不是"不雅"，是**有歧义**。「在洗」既能指"占用"又能指"正在出水"，而这两件事在
数据里是两个量（`usage_session` 与 `usage_segment`）。留着它，读者只能靠上下文猜。
文档里写一句"请勿使用"挡不住任何人，这里把它变成门禁。

## 两类弃用词，处置不同

- **硬禁**：在本项目里没有合法义的词（`刷卡`／`缺口`／`采集端` …）。出现即违规。
- **限定**：有合法义的词，只在某个意义上违规（`终端` 指 shell 合法、指那台机器不合法；
  `代理` 指 nginx 合法、指我们的程序不合法；`快照` 指数据库快照合法、指帧不合法；
  `登录` 引浴室 App 界面原文合法、我们自己的描述不合法）。这类按**位置**登记豁免，
  每条都要写理由——理由本身是给下一个人看的判断依据。

## 用法

    cd server && python -m app.core.terms          # 打印全部违规，有则退出码 1

`tests/test_language_rules.py` 调用 [scan] 把它变成门禁。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

#: 扫描范围：源码、契约、文档、部署脚本。构建产物、依赖、缓存不在其中。
SCAN_SUFFIXES = frozenset({".py", ".kt", ".js", ".md", ".css", ".html", ".kts", ".json", ".sh", ".ps1", ".toml"})
SKIP_DIRS = frozenset(
    {"build", ".gradle", "node_modules", ".venv", "__pycache__", ".git", ".mypy_cache", ".pytest_cache"}
)

#: 正名表：概念 → 唯一中文正名。**这份是给人看的判据，不是机器可读的映射**，
#: 机器只用下面的 DEPRECATED。标识符一并列出，免得有人再给标识符起中文别名。
CANONICAL: dict[str, str] = {
    # ── 实体 ──
    "那台机器": "设备",
    "机器上我们装的程序": "NTU Shower",
    "浴室那套 App": "浴室 App",
    "出水的那个口": "喷头",
    "一次占用": "会话（`usage_session`）",
    "一次连续出水": "段（`usage_segment`）",
    "占用的人": "学号（`cert_no`）",
    # ── 时刻（全在服务端轴上）──
    "喷头被分配给这个人的时刻": "分配时刻（`assigned_at`）",
    "拿不到设备原文时退回的观测值": "首次观测时刻（`assigned_source = observed`）",
    "这一段开始出水": "开阀时刻（`opened_at`）",
    "这一段停止出水": "关阀时刻（`closed_at`）",
    "这一次占用结束": "释放时刻（`ended_at`）",
    "最后一次确认还占着": "最后确认时刻（`last_seen_at`）",
    # ── 时长 ──
    "出水累计的时长": "淋浴时长（设备累计 `bath_time_s`／段之和 `flow_s`，两种来源）",
    "整场占用": "分配时长（`释放时刻 − 分配时刻`）",
    "入门后到首次开阀": "等待时长",
    "我们没看见的那段时间": "盲区（`observed_span = 'gap'`，长度＝`释放时刻 − 最后确认时刻`）",
    # ── 可信度四维（不是一回事）──
    "起点怎么来的": "起点来源（`assigned_source`）",
    "我们看没看见": "观测连续性（`observed_span`）",
    "段长证据强度": "段长来源（`source`）",
    "段记全没有": "段完整性（`segments_complete`）",
    # ── 喷头状态取值（与小程序的展示文案一致，不许另起）──
    "`free`": "空闲",
    "`selected`": "已选中",
    "`waiting`": "等待开阀",
    "`bathing`": "淋浴中",
    "`paused`": "已暂停",
    "`repair`": "维修中",
    "`disabled`": "已禁用",
    "占用态集合": "占用（＝等待开阀／淋浴中／已暂停）",
    "可分配态集合": "可分配（＝空闲／已选中）",
    # ── 三个终结动作（因果，不是同义）──
    "浴室 App 把喷头那一行清零、会话结束": "释放",
    "走支付／挂账流程": "结算（`session.settle`）",
    "停水这个物理动作": "关阀",
    # ── 服务器侧按配置自动跑的那几个 ──
    "服务器后台按配置自动跑的功能": "后台任务（预约续期／会话续期／自动结算／延长服务时间，"
    "配置在配置页的「服务端业务」页签）",
    # ── 其余 ──
    "一次上报": "帧（`door_snapshot`）",
    "我们把命令发给设备": "下发",
    "设备把收到的命令跑起来": "执行（与「下发」相对：下发在我们这一侧，执行在设备那一侧）",
    "当下的钟差": "偏差（`clock_skew_s`）",
    "钟自然走偏的速率": "漂移",
}

#: 硬禁词：本项目里没有合法义，出现即违规。值是应当改成的说法。
DEPRECATED: dict[str, str] = {
    "会话维护": "后台任务（那三个功能在服务器上按配置自动跑，不叫维护）",
    "刷卡": "分配（这台设备无卡，`card_no` 实测恒为 0）",
    "进门": "分配",
    "入门": "分配（指时刻时写「分配时刻」，指喷头时写「喷头」）",
    "已入门": "等待开阀",
    "缺口": "盲区",
    "断层": "盲区",
    "断链": "盲区（指一段时间时）／断连（指连接时）",
    "钟差": "偏差",
    "采集端": "NTU Shower（程序）／设备（那台机器）",
    "场次": "会话",
    "阀门": "喷头（「阀门」是浴室 App 内部的水控件）",
    "开水": "开阀",
    "在洗": "占用（状态）／淋浴中（那一段）",
    "发命令": "下发",
    "出水时长": "淋浴时长",
    "占用时长": "分配时长",
    "使用中": "淋浴中",
    "快照": "帧（指一次上报时）／备份（指数据库或配置的存档时，见 RUNBOOK）",
    "终端": "设备（指那台机器时）；指 shell 时保留「终端」",
    "代理": "NTU Shower（指我们的程序时）；指 nginx 时保留「代理」",
    "登录": "分配（指浴室 App 提交凭证那一刻）／身份（指我们的 X-Identity）",
}

#: 少数词有合法搭配，用正则把**合法的那种**排除在外。默认按字面匹配。
#: 这一条的存在本身就是判据：只有当"这个词在某个搭配里是对的"时才登记，
#: 而不是为了让扫描器闭嘴。
PATTERNS: dict[str, str] = {
    # 「下发命令」里的「发命令」是对的——`下发` 才是正名，不能把它一起打掉
    "发命令": r"(?<!下)发命令",
    # 「判断链路」里的「断链」是「判/断链/路」被切出来的，与"断开"无关
    "断链": r"(?<!判)断链",
}

#: 硬禁词出现即算违规的例外，按文件登记，理由必填。
#: 判据只有一条：**这个位置是在引用外部原文**（浴室 App 的界面文字、旧系统的历史描述），
#: 而不是在描述我们自己的东西。
EXEMPT_FILES: dict[str, str] = {
    "server/app/core/terms.py": "本文件：弃用词表本身就是数据",
}

#: 限定词在某个文件里合法，按 `相对路径:词` 登记，理由必填。**没登记的一律算违规。**
#: 能换个更准的词就不登记（例如数据库快照改叫「备份」）——豁免是给真正同形不同义的情形留的，
#: 登记一条就等于承认"这个词在这里指的确实是另一个东西"。
EXEMPT_HITS: dict[str, str] = {
    "server/web/views/devices.js:终端": "指操作者本机的 shell（「在另一终端中执行」「终端里敲命令」），不是那台机器",
    "server/app/core/audit.py:代理": "指 nginx 反向代理（真实地址在 X-Real-IP 里），与我们的程序无关",
    "server/app/gateway/routes.py:代理": "同上：nginx 反向代理",
    "docs/REFERENCE.md:代理": "同上：nginx 反向代理（来源 IP 一节），与我们的程序无关",
    "android/app/src/test/java/com/ryanliu/ntushower/features/automation/BathAppPageTest.kt:登录": (
        "引自浴室 App 的界面原文「请先登录」，是测试夹具，不是我们的说法"
    ),
}


@dataclass(frozen=True)
class Violation:
    """一处违规。`suggestion` 是应改成的说法，直接给改的人看。"""

    path: str
    line: int
    word: str
    suggestion: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  「{self.word}」→ {self.suggestion}\n    {self.text}"


def scanned_files(root: Path = REPO) -> list[Path]:
    """扫描范围内的全部文件，按路径排序（输出稳定，便于逐批核对）。"""
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in SCAN_SUFFIXES and not any(part in SKIP_DIRS for part in path.parts)
    )


def relative(path: Path, root: Path = REPO) -> str:
    return path.relative_to(root).as_posix()


def _pattern(word: str) -> re.Pattern[str]:
    """这个词怎么匹配：默认按字面，[PATTERNS] 里登记过就用它给的正则。"""
    return re.compile(PATTERNS.get(word, re.escape(word)))


def scan(root: Path = REPO) -> list[Violation]:
    """全仓扫描，返回全部违规。纯函数，同样的仓库给同样的结果。"""
    found: list[Violation] = []
    patterns = {word: _pattern(word) for word in DEPRECATED}
    for path in scanned_files(root):
        rel = relative(path, root)
        if rel in EXEMPT_FILES:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            for word, suggestion in DEPRECATED.items():
                if not patterns[word].search(line):
                    continue
                if f"{rel}:{word}" in EXEMPT_HITS:
                    continue
                found.append(Violation(rel, lineno, word, suggestion, line.strip()[:90]))
    return found


def summary(found: list[Violation]) -> str:
    """按词汇总，用于汇报进度与验收。"""
    by_word: dict[str, int] = {}
    for v in found:
        by_word[v.word] = by_word.get(v.word, 0) + 1
    lines = [f"违规 {len(found)} 处，涉及 {len({v.path for v in found})} 个文件", ""]
    for word, count in sorted(by_word.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {word:<8} {count:4} 处   → {DEPRECATED[word]}")
    return "\n".join(lines)


def _main() -> int:
    found = scan()
    if not found:
        print("术语检查通过：全仓无弃用词。")
        return 0
    print(summary(found))
    print()
    for v in sorted(found, key=lambda v: (v.path, v.line)):
        print(v)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
