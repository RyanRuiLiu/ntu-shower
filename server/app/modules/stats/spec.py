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

#: 在多个句子里复用的词。**词在这里只有一份**：改措辞不会漏掉任何一处。
_NEAR_DAYS = "近 {days} 天"
_AVG_SHOWER = "平均淋浴"
_LONGEST = "最长一次"
_COUNT_MORE = "次数最多"
_COUNT_LESS = "次数最少"

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
#: **界限取 (0.15, 0.40, 0.65, 0.85) 的原因**：读者把颜色当绝对读——红色就是"挤"。
#: 忙时段的格子普遍落在峰值的 0.4–0.8，若 0.35 就给黄、0.55 给橙、0.75 就红，大半个图
#: 常年暖色，而峰值本身可能只占容量一两成，红色名不副实（"大多数格子总是红的黄的，
#: 看不懂什么意思"）。上调之后绿与浅色覆盖日常，暖色只随"接近本室最忙"递进；绝对
#: 占用始终在逐格说明里（`_heat_texts`），"现在去有没有位置"以它为准。
#:
#: **最高档是"本设备最忙"不是"满"**：实测两段学期里峰值只占容量 53%–55%。图例
#: （`HEAT_LEGEND`）说的是"最忙"；判断"现在去有没有位置"看逐格说明里的绝对占用。
HEAT_LEVELS: tuple[float, ...] = (0.15, 0.40, 0.65, 0.85)

#: 热力图区块的标题。**描述文案由服务端下发**——改字不该要求客户端发版。
HEAT_TITLE = "各时段占用"

#: 热力图副标。窗口天数由 service 按 `days` 填（`str.format`），措辞改在这里一处。
HEAT_NOTE = f"{_NEAR_DAYS} · 颜色深浅看该时段相对本设备最忙时刻的繁忙程度"

#: 六个色阶的名字，下标即色阶值（0–5）。图例两端显示首尾两档，整句说明在逐格文字里。
#:
#: **最高档叫「最忙」不叫「满」**：色阶相对本设备峰值（见 `HEAT_LEVELS`），红色承诺
#: 的是"这是本室最忙的时段"，不是"没有空位"——图例若写"满"，读者拿它对容量，
#: 而它答不了那个问题。
HEAT_LEGEND: tuple[str, ...] = ("没人", "空", "较空", "一般", "较忙", "最忙")

#: 星期几的展示名，下标与 Python `date.weekday()` 一致（0 是周一）。
#: 逐格说明要写"周二 21 时"，星期名是文案的一部分，归这里一份。
WEEKDAY_LABELS: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

#: 说明行的初始内容：落在服务时段内时，"现在"那一格说明的前缀。
HEAT_NOW_PREFIX = "现在 · "

#: 说明行在服务时段之外时的整句引导——那一刻没有"现在"那一格可指。
HEAT_HINT = "点格子看各时段详情，颜色越红越挤"

#: 统计页其余区块的描述文案登记表，接口以 `copy` 一个字典整表下发。
#:
#: **为什么收在服务端**：客户端发一次版成本高，而这些句子是口径的表述（"进行中
#: 不计入时长""同星期平均"）——写在客户端就成了第二份，改一处另一处不跟着动，
#: 而两处都不会报错。先例是 `config/spec.py` 的 `GROUPS`（分组元数据只在这里一份）。
#:
#: **占位符的分工**：`{days}` 由 service 在下发前填（窗口天数是请求参数）；其余
#: （`{as_of}` `{t}` `{pct}` `{doors}` `{states}` `{text}` `{door}` `{hour}`）是运行时
#: 值，客户端用 `shower-utils` 的格式化函数填入——**词归服务端，数归客户端**，
#: 数值格式化全仓只有 shower-utils 一处。

STATS_COPY: dict[str, str] = {
    "empty_devices": "暂无已登记的设备",
    "empty_window": "这个窗口内没有会话记录",
    "today_title": "今日",
    "today_note": "截至 {as_of} · 进行中不计入时长",
    "served_label": "已洗人次",
    "bathing_label": "淋浴中",
    "avg_label": _AVG_SHOWER,
    "replay_title": "时间点回放",
    "replay_sub": "按日期查看某一时刻各喷头的状态",
    "replay_summary": "{doors} 个喷头：{states}",
    "replay_detail_bathing": "已洗 {t}",
    "replay_detail_paused": "已停 {t}",
    "replay_detail_waiting": "已等 {t}",
    "replay_detail_idle": "上次 {text}",
    "doors_title": "喷头使用次数",
    "doors_note": f"{_NEAR_DAYS} · 次数与平均淋浴时长",
    "compare_title": "各楼层",
    "compare_note": _NEAR_DAYS,
    "mine_title": f"我的{_NEAR_DAYS}",
    "mine_last": "最近一次 {when} · 洗了 {t}",
    "mine_fav": "常用喷头 {door} · 常在 {hour} 点前后",
    "mine_faster_fast": "你比 {pct}% 的人洗得快",
    "mine_faster_slow": "你比 {pct}% 的人洗得久",
    "lb_title": "排行榜",
    "lb_sub": _NEAR_DAYS,
    "lb_foot": "学号为浴室系统账号，在榜单与回放中公开显示",
    "cmp_sessions": "人次",
    "cmp_flow": _AVG_SHOWER,
    "cmp_peak": "高峰",
    "cmp_wait": "平均等待",
    "mine_count": "次数",
    "mine_total": "总时长",
    "mine_avg": _AVG_SHOWER,
    "mine_longest": _LONGEST,
    "mine_scope": "个人统计跨全部设备；上面各块都按当前这一台设备算",
    "mine_need_credential": "先在设置页配置浴室账号密码，这里才会显示你的统计",
    "foot_title": "统计口径",
    "foot_1": "只统计真正出过水的会话：开了喷头却没出水的，不计入次数、时长与榜单",
    "foot_2": "淋浴时长只算出水的时间，中间关阀的不计入",
    "foot_3": "占用与高峰按「喷头被占着」算，与出没出水无关",
    "foot_4": "平均等待是满员时段的平均：喷头全被占着，从占满到有一个空出来，这样一段算一次",
    "foot_5": "今日是「截至现在」，还在淋浴的那一场不计入人次与平均",
}


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
        orders=(BoardOrder("desc", _COUNT_MORE), BoardOrder("asc", _COUNT_LESS)),
    ),
    LeaderboardSpec(
        "sessions_flowed",
        "淋浴次数",
        note="窗口内真正出过水的会话数",
        detail="flow_avg_s",
        detail_label=_AVG_SHOWER,
        orders=(BoardOrder("desc", _COUNT_MORE), BoardOrder("asc", _COUNT_LESS)),
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
        orders=(BoardOrder("desc", _LONGEST), BoardOrder("asc", "最短一次")),
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
