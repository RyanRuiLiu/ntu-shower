"""浴室统计的类别登记表：有哪些榜、分布怎么分档、时段取哪一段。

## 为什么类别要登记在这里

页面上有七个榜、两个分布、一个时段。它们的**名字、顺序、口径注解**若写在客户端，
服务端改一次排序口径、加一档分桶，界面就静默说错话——而两端都不会报错。

`config/spec.py` 的 `GROUPS` 已经立了先例（"分组元数据只在这里一份，前端据此分卡片
渲染，不在各处硬编码键表"），这里照同一套路：**类别只在这里一份，由接口发给客户端**。

## 时段 11–24 时是展示选择，不是营业时间

热力图与逐时图只画有人来的那几小时。实测近 30 天 2661 场会话，本地时刻落在
11–24 时的有 2608 场（97.9%），高峰在 22 时；时段外的 2.1% 是清晨与午间的零星使用，
画进来会把横轴拉长、主要时段压扁。

**浴室 App 那天什么时候开机由设备侧决定**，不是我们的配置——`tools/find_out_of_hours.py`
里那组 10:55／23:58 是排查脚本的自用常量，不要把它当产品配置引用过来。真要改这个时段，
改下面两个常量即可，接口会把它带给客户端。

## 榜单名不用自造词

`terms.py` 定了"一个概念一个中文正名"。榜单名是展示文案，但它指的那件事**有正名**：
"平均单次"说的是淋浴时长，"最晚开始"说的是平均开始时刻。自造词（原型里的「悠速」）
读者猜不出它排的是什么，猜错了也不会报错——这正是 `terms.py` 要消灭的那种情况。
"""

from dataclasses import dataclass
from typing import Literal

#: 分桶时段：本地时刻的起点与列数。11 时到 23 时，24:00 收尾算在 23 时那一列。
HOUR_START = 11
HOUR_COUNT = 13

#: 每个榜展示前几名。**展示参数，不是业务规则**——它只影响列表长度，
#: 不影响任何一条统计口径。
LEADERBOARD_SIZE = 10

#: 热力色阶的分档界限——**相对于本设备自己最忙的那一格**，不是相对容量。
#:
#: 这是 Google Popular Times 的做法（"热门时段"把每个时段相对于该地点最忙的时段表示）：
#: **每台设备跟自己的峰值比**。所以换一台喷头数不同的设备（5／6／7／8）、或者一台用法
#: 完全不同的设备，都不用调这里的门槛。绝对占用率做不到——它的门槛要按设备重定，
#: 否则就是一台全红、另一台全绿。
#:
#: **最高档是"本设备最忙"不是"满"**：实测两段学期里峰值只占容量 53%–55%。图例说
#: "闲 → 忙"；判断"现在去有没有位置"要看点击说明里的绝对占用（"平均占用 3/7"）。
HEAT_LEVELS: tuple[float, ...] = (0.15, 0.35, 0.55, 0.75)


@dataclass(frozen=True)
class BoardOrder:
    """一个榜的一个排法方向。"""

    key: Literal["desc", "asc"]
    """`desc` 从多到少，`asc` 从少到多。"""

    label: str
    """这个方向在页面上叫什么（「洗得最快」比「升序」说得清）。"""


@dataclass(frozen=True)
class LeaderboardSpec:
    """一个排行榜。"""

    key: str
    """标识符。判据与接口字段用它，界面用 [title]。"""

    title: str
    """界面上那一栏的名字。**说这一栏是什么，不说它怎么排**——"最晚开始"那种写法把方向
    混进了名字，而方向是 `orders` 的事。"""

    orders: tuple[BoardOrder, ...]
    """能按哪些方向排，**第一个是默认**。

    **每个榜都给两个方向**：有最多就有最少，界面上的那个切换按钮位置才固定——只有部分
    榜能换向的话，按钮时有时无，读者每切一次榜都要重新找它。
    """

    note: str = ""
    """这一榜的口径注解，空串表示无需说明。

    **它必须与服务端真正的排序口径同源**：写在客户端就成了假说明，而假说明不会报错。
    """

    detail: str = ""
    """副列取 `LeaderboardRow` 的哪个字段，空串表示按场排的那一榜（副列是喷头号与日期）。

    **由服务端指定**，而不是客户端按 `key` 去判断：榜与列的对应是这一榜口径的一部分。
    写在客户端就成了第二份，服务端换一列而客户端不跟着改，页面上只是数字悄悄变了意思。
    """

    detail_label: str = ""
    """副列的前缀（如「平均占用」）。空串表示不加前缀，只把数写出来。"""

    kind: Literal["person", "single"] = "person"
    """按**人**排还是按**场**排。

    按场的那一榜，每一行是"某一场"，所以带得着喷头号与分配时刻；按人的行是"某个人"。
    两种行在契约里合成一种形状（用不到的字段填 None／空串），客户端一个循环就能渲染。
    """


@dataclass(frozen=True)
class HistogramBucket:
    """分布里的一档。"""

    label: str
    """如「5–10 分」。"""

    upper_s: int | None
    """这一档的上界（秒）；None 表示"其余全部"。

    边界是**数据**不是文案：分档改了，`label` 跟着重算，不靠人记得同步两处。
    """


@dataclass(frozen=True)
class HistogramSpec:
    """一个分布。"""

    key: str
    title: str
    buckets: tuple[HistogramBucket, ...]

    note: str = ""
    """副标上的一句话口径，空串表示这一块的副标由客户端另拼。

    **与榜的 `note` 同一个道理**：它说的是"这一块在数什么"。写在客户端就成了第二份
    文案，改一处另一处不跟着动，而两处都不会报错。
    """


#: 榜，顺序即界面上的页签顺序。
#:
#: **一列七个页签**，按人排的与按场排的混在一起。分成两个清单，客户端得自己拼这一列、
#: 还得逐榜判断行形状，而那个判断写错了不报错——只会让某一榜少显示几个字。
#:
#: **凡"只改方向就变成另一个榜"的，合成一个榜**：「最长一次／最短一次」是同一批单场
#: 时长的两个方向，「平均淋浴」的快与久也是。合成之后由 `orders` 描述方向，界面上出
#: 一个切换；分成两个页签，读者要自己发现"这两个榜其实是同一件事"。
#:
#: **除「会话数」外，每一榜的输入都是出过水的会话**（`service._flowed`）：没淋浴的那次
#: 不该算进时长、天数与平均开始时刻。两种会话数都留着，是因为它们答的是两个问题——
#: 「来得勤」与「洗得多」，而生产数据里这两者能差到 89 : 30。
#:
#: 「连续淋浴天数」与「开始淋浴时间」要多算一步（按本地日期去重、取平均开始时刻），
#: 口径写在各自的 `note` 里。
BOARDS: tuple[LeaderboardSpec, ...] = (
    LeaderboardSpec(
        "sessions",
        "会话数",
        note="窗口内分配到的会话数，含未出水的",
        # 副列给"整场占用多久"（分配 → 释放），与主列"来了几次"配成一对；
        # 而它**含未出水的会话**，与会话数榜的口径一致。
        detail="assigned_avg_s",
        detail_label="平均占用",
        orders=(BoardOrder("desc", "次数最多"), BoardOrder("asc", "次数最少")),
    ),
    LeaderboardSpec(
        "sessions_flowed",
        "淋浴次数",
        note="窗口内真正出过水的会话数",
        detail="flow_avg_s",
        detail_label="平均淋浴",
        orders=(BoardOrder("desc", "次数最多"), BoardOrder("asc", "次数最少")),
    ),
    LeaderboardSpec(
        "flow",
        "淋浴总时长",
        note="窗口内淋浴时长合计",
        detail="sessions_flowed",
        orders=(BoardOrder("desc", "时长最长"), BoardOrder("asc", "时长最短")),
    ),
    LeaderboardSpec(
        "avg",
        "平均淋浴时长",
        note="按每个人每次淋浴时长的均值排名",
        detail="sessions_flowed",
        orders=(BoardOrder("desc", "洗得最久"), BoardOrder("asc", "洗得最快")),
    ),
    LeaderboardSpec(
        "single",
        "单次淋浴时长",
        note="按单场淋浴时长排名",
        kind="single",
        orders=(BoardOrder("desc", "最长一次"), BoardOrder("asc", "最短一次")),
    ),
    LeaderboardSpec(
        "streak",
        "连续淋浴天数",
        note="连续每天至少有一次出水会话的天数",
        detail="sessions_flowed",
        orders=(BoardOrder("desc", "连续最多"), BoardOrder("asc", "连续最少")),
    ),
    LeaderboardSpec(
        "latest",
        "开始淋浴时间",
        note="出水会话的平均开始时刻",
        detail="sessions_flowed",
        orders=(BoardOrder("desc", "开始最晚"), BoardOrder("asc", "开始最早")),
    ),
)

#: 分布清单。两个都用 `usage_session`，一档的范围由相邻两档的 `upper_s` 夹出来。
HISTOGRAMS: tuple[HistogramSpec, ...] = (
    HistogramSpec(
        "flow",
        "淋浴时长分布",
        (
            HistogramBucket("0–3 分", 180),
            HistogramBucket("3–6 分", 360),
            HistogramBucket("6–10 分", 600),
            HistogramBucket("10–15 分", 900),
            HistogramBucket("15 分以上", None),
        ),
    ),
    HistogramSpec(
        "pauses",
        "关阀次数分布",
        (
            HistogramBucket("1 次", 1),
            HistogramBucket("2 次", 2),
            HistogramBucket("3 次", 3),
            HistogramBucket("4 次以上", None),
        ),
        note="窗口内每一场关了几次阀",
    ),
)

BOARDS_BY_KEY: dict[str, LeaderboardSpec] = {item.key: item for item in BOARDS}
HISTOGRAMS_BY_KEY: dict[str, HistogramSpec] = {item.key: item for item in HISTOGRAMS}
