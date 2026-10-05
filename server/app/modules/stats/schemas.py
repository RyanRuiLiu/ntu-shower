"""浴室统计领域的 API 契约。

## 中文不在这里

这份契约里不出现任何中文句子或词——类别的名字与口径注解在 `spec.py` 的登记表里，
由接口下发。写在这里的每一个中文都是第二份，改一处必漏一处。

## 数值给原始的，格式化在客户端

榜的 `value`、分布的 `count`、喷头热度的 `sessions` 都是原始数值，不带单位、不拼接。
原因是同一个量在不同位置写法不同（`flow` 榜要 `7h05m`、单次榜要 `8m24s`），
而**格式规则**由客户端的一个函数统一负责，比让服务端拼十种字符串更好维护。

例外是 `LabeledOption`：标识符到中文名的映射必须由服务端给，客户端不许自带一份。
"""

from typing_extensions import TypedDict

from app.modules.doors.schemas import DoorState


class LabeledOption(TypedDict):
    """「标识符 → 界面文字」的条目。

    状态取值、统计类别、榜单都走它。**判据永远用 `key`，界面永远用 `label`**，
    两者不许互相替代——拿 `label` 当判据会在改文案时静默失效。
    """

    key: str
    label: str


class StatsTotals(TypedDict):
    """窗口内的汇总。**只留页面上真正会读的那些**。

    人次、平均淋浴时长、平均等待各有一处更合适的落点：「各楼层」那张表（含本设备那一行）。
    此前这里还各存一份给顶部那句总览用，而那句与那张表是同一批数——留着就是同一概念的
    第二份来源，改一处另一处不跟着动，页面上只会显示出两个值。
    """

    flow_median_s: int
    """淋浴时长的中位（秒）。中位比均值抗离群：一场两小时的会话会把均值拉飞。

    样本是**出过水的**那些会话：没淋浴的那次算进来会把它拉向 0，而页面上看不出是哪些
    拉低的。
    """


class TodaySummary(TypedDict):
    """今日，**截至现在**的口径。

    进行中的会话不计入 `served` 与 `flow_avg_s`——它们的淋浴时长还没走完，算进去
    只会把平均值往下拽。所以这一块的数字随时在变，界面上要写明"截至 HH:MM"。
    """

    as_of: str
    """截止时刻（RFC 3339）。它是这个包里所有"今日"数字的基准。"""

    served: int
    """已洗人次：今日**出过水且已结束**的会话数。"""

    bathing: int
    """此刻处于淋浴中的喷头数。它与 `served` 是两回事，不要相加。"""

    flow_avg_s: int
    """已洗会话的淋浴时长均值（秒）。"""


class HistogramRow(TypedDict):
    """分布里的一档。`label` 来自 `spec.HISTOGRAMS` 的分档表。"""

    label: str
    count: int


class HistogramView(TypedDict):
    """一个分布（淋浴时长、关阀次数）。"""

    key: str
    title: str
    note: str
    """副标上的一句话口径。空串表示这一块的副标由客户端另拼——「淋浴时长分布」的副标
    要带窗口内的中位，那个数在 `totals` 里，比一句静态注解更该出现在那里。"""
    rows: list[HistogramRow]


class DoorUsageRow(TypedDict):
    """一个喷头在窗口内的使用情况。**只统计出过水的会话**。"""

    door_no: int
    sessions_flowed: int
    """使用次数。分配了却没出水的那次不算用过。"""
    flow_avg_s: int
    """这个喷头上出过水的会话的淋浴时长均值（秒）。"""


class LeaderboardRow(TypedDict):
    """榜上的一行。**排序由服务端做**，`rank` 就是最终名次。

    **两种榜的行是同一个形状**：按人排的行是"某个人"，按场排的行是"某一场"——后者
    才填 `door_no` 与 `at`。合成一种形状是为了让客户端用一个循环渲染全部七榜；
    分成两种形状，客户端就得逐榜判断该用哪半字段，而那个判断写错了不报错。
    """

    rank: int
    cert_no: str
    value: int
    """这一榜排序依据的值。含义由所属榜的 `key` 决定：

    `sessions`／`sessions_flowed` 会话数、`flow` 淋浴总秒数、`avg` 平均淋浴秒数、
    `single` 那一场的秒数、`streak` 连续天数、`latest` 平均开始时刻（当地 0 点起的分钟数）。
    """

    door_no: int | None
    """喷头号。**只有按场排的那一榜有**，按人排的为 None。"""

    at: str
    """那一场的分配时刻（RFC 3339 UTC）。**只有按场排的那一榜有**，按人排的为空串。

    客户端按自己的时区显示——服务端不给"9 月 28 日"这种字。
    """

    sessions: int | None
    """这个人的**会话数**（含没出水的）。它答的是"占了喷头几次"。按场排的那一榜为 None。

    它与 `sessions_flowed` 要一起看：差得远说明"来得多、洗得少"，而只看其中一个数
    分不出这两种人——生产数据里最极端的一个是 89 场会话、只有 30 次出水。
    """

    sessions_flowed: int | None
    """这个人的**出水会话数**。它答的是"洗了几次"。按场排的那一榜为 None。"""

    flow_avg_s: int | None
    """这个人的平均淋浴时长（秒）。按场排的那一榜为 None。

    单看总时长分不出"次数多"还是"每次久"，这个数补上另一半。
    """

    assigned_avg_s: int | None
    """这个人的**分配时长**均值（秒）——从分配到释放，含没出水的那几次。

    与会话数同口径：`会话数` 那一榜的副列要它，"来了几次"与"每次占多久"是一对。
    按场排的那一榜为 None。
    """


class LeaderboardView(TypedDict):
    """一个榜：类别元数据 + 各个方向已排好的行。"""

    key: str
    title: str
    note: str

    detail: str
    """副列取 `LeaderboardRow` 的哪个字段，空串表示按场排的那一榜（副列是喷头号与日期）。

    **由服务端指定**，而不是客户端按 `key` 去判断：榜与列的对应是这一榜口径的一部分。
    写在客户端就成了第二份，服务端换一列而客户端不跟着改，页面上只是数字悄悄变了意思。
    """

    detail_label: str
    """副列的前缀（如「平均占用」）。空串表示不加前缀，只把数写出来。"""

    order: str
    """默认方向，`orders` 的第一项。"""

    orders: list[LabeledOption]
    """这一榜能按哪些方向排，以及每个方向在页面上叫什么。

    **每个榜都是两项**：有最多就有最少。界面上的切换按钮位置因此固定，读者不用每次
    切榜都重新找它。
    """

    rows: dict[str, list[LeaderboardRow]]
    """方向 → 已排好的行。**两个方向都算好一起给**：切换时不发请求，「切榜不重拉」
    这条才继续成立。"""


class CompareRow(TypedDict):
    """楼层对比里的一台设备。字段与主视图的汇总同源，便于横向比。"""

    device_id: str
    name: str
    door_count: int
    sessions_flowed: int
    """出过水的会话数。楼层对比表里的"人次"用它。"""
    flow_avg_s: int
    """出过水的会话的淋浴时长均值（秒）。"""
    wait_avg_s: int
    """**平均等待**（秒）：从"最后一个空位被占"到"第一个空位空出来"的那些时段的均值。

    它量的是**挤不进去**——喷头全满的那一段，想来的人只能等。取值是占用口径（占着喷头
    就算，与出没出水无关）。**不是"分配后多久开阀"**：那段时间里喷头已经归使用者了，
    没有任何人在等它。算法见 `service._full_wait_seconds`。
    """

    peak_start: int
    """高峰时段的起止小时（本地时）。它由热力矩阵推出，所以与页面上那张图同源。"""

    peak_end: int


class MineView(TypedDict):
    """个人统计。**它跨设备**——同一批人可能在不同楼层洗，所以不与主视图同端点。"""

    cert_no: str
    sessions_flowed: int
    """出过水的会话数。**只开会话没出水的不算**——那是测试行为，不是淋浴。"""
    flow_total_s: int
    flow_avg_s: int
    flow_longest_s: int
    door_no: int | None
    """最常用的喷头号；窗口内没有会话时为 None。"""

    hour_mode: int | None
    """最常开始的小时（本地时，0–23）；窗口内没有会话时为 None。"""

    faster_than_pct: int
    """比这个百分比的人洗得快。窗口内样本太少时给 0。"""

    last_at: str
    """最近一场的分配时刻（RFC 3339 UTC）；没有则为空串。"""

    last_flow_s: int


class ReplayDoor(TypedDict):
    """回放序列里的一次状态变化。"""

    door_no: int
    state: DoorState
    """变到的那个状态。展示名走 `StatsView.states`，客户端不自带中文。"""

    at: str
    """变成这个状态的时刻（RFC 3339 UTC）。"""

    cert_no: str
    bath_time_s: int
    """那一刻设备累计的出水秒数。客户端用它算"已洗多久"。"""


class ReplayDoorLast(TypedDict):
    """一个喷头在**这一天开始之前**的最后一次关阀。"""

    door_no: int
    closed_at: str
    """关阀时刻（RFC 3339 UTC）。"""

    cert_no: str
    """那一次是谁。空串表示那一段没有学号。"""


class ReplayView(TypedDict):
    """`GET /stats/devices/{device_id}/replay` 的读模型：**一整天**的状态变化序列。

    一次拉一天（实测 162 条、10.5 KB），客户端按滑块位置重放，拖动时不再发请求。
    按"给定时刻查该时刻的现态"的写法要扫到那一刻为止的全部历史事件（实测 36–55 ms），
    而滑块每移动一格就触发一次——那样每次拖动都会卡。换成一天一次之后，
    客户端重放一次约 0.009 ms。
    """

    device_id: str
    date: str
    """本地日期 `YYYY-MM-DD`。"""

    tz_offset_minutes: int
    hour_start: int
    hour_count: int
    events: list[ReplayDoor]
    """该日的状态变化，按时刻升序。客户端重放时取每个门最后出现的那个状态。"""

    last_closed: list[ReplayDoorLast]
    """每个喷头在**这一天开始之前**的最后一次关阀。

    空闲（或已选中）的芯片要显示"上次是谁、几点关的水"，而那一天的事件序列里没有它
    ——那一次发生在更早的日子。客户端拿它作底，再用当天的事件往上盖。
    """


class StatsView(TypedDict):
    """`GET /api/v1/stats/devices/{device_id}` 的读模型：一台设备一次装载。

    一次给全。切设备与切统计窗口要重拉，其余（七个榜的页签与方向、分布、回放）
    都在客户端本地切，不发请求。
    """

    device_id: str
    name: str
    door_count: int

    since: str
    """窗口起点（RFC 3339 UTC）。"""

    until: str

    tz_offset_minutes: int
    """本次分桶用的时区偏移：**本地时间 − UTC** 的分钟数，UTC+8 即 480。

    方向与 `extend_hours_zone_offset_minutes` 一致。注意 JavaScript 的
    `Date.getTimezoneOffset()` 返回的是**反的**（UTC+8 时给 −480），传之前要取负。
    """

    hour_start: int
    hour_count: int
    """分桶时段的起点与长度（本地时）。**服务端给**，客户端不写死 11——
    营业时间一改，写死的那份就成了错的，而且它不会报错。"""

    states: list[LabeledOption]
    """状态取值的展示名，与回放里的 `state` 配套。"""

    totals: StatsTotals
    today: TodaySummary

    heat: list[list[float]]
    """`[星期][小时]` 的平均占用喷头数，行为 `hour_count` 列。

    星期 0 是周一，与 Python 的 `date.weekday()` 一致（不是 JavaScript 的
    `getDay()`——那个 0 是周日，客户端按它取值会整体错位一天）。

    **它是时间平均**：一格里"平均有几个喷头被占着"。所以 `7.0` 要出现，需要那一小时
    里 7 个喷头几乎全程被占——实测近 90 天最高的一格是 1.29，而**瞬时**满员确实出现过
    （累计 2.5 小时）。两者答的是不同的问题：这个答"通常挤不挤"，状态页答"现在有没有位置"。
    """

    heat_levels: list[list[int]]
    """`heat` 的色阶（0 起，最高档是本设备最忙的时段），与它同形。

    **分档相对于本图自己的峰值，不是相对容量**——这是 Google Popular Times 的做法
    （"热门时段"把每个时段相对于该地点最忙的时段表示）：每台设备跟自己比，所以喷头数
    不同（5／6／7／8）、用法不同的设备都不用调门槛。绝对占用率做不到这一点。

    什么算"忙"由服务端定（`spec.HEAT_LEVELS`），客户端只按它铺颜色——一处说了算。

    **最高档是"最忙"不是"满"**：实测两段学期里峰值只占容量 53%–55%。判断"现在去有没有
    位置"要看 `heat` 的绝对值（点击说明里显示的就是它）。
    """

    heat_title: str
    """热力图区块的标题。**描述文案由服务端下发**——改字不该要求客户端发版。"""

    heat_note: str
    """热力图副标，含窗口天数，客户端原样显示。与 `histograms` 的 `note` 同一道理：
    它说的是这一块在数什么，写在客户端就成了第二份文案。"""

    heat_legend: list[LabeledOption]
    """六个色阶的名字，`key` 是色阶值（"0"–"5"），顺序即色阶升序。

    图例文案在这里定稿——颜色承诺什么（"最忙"而非"满"）是口径的一部分。
    """

    heat_texts: list[list[str]]
    """每个格子的说明文字（点击展示），与 `heat` 同形。

    时刻、绝对占用、占容量的比例全部由服务端成文——一格怎么念是这一格的口径，
    留在客户端就成了第二份，改一处另一处不跟着动，而两处都不会报错。
    """

    heat_now_text: str
    """说明行的初始内容，整句由服务端成文。

    请求时刻落在服务时段内时是"现在"那一格的说明（带 `spec.HEAT_NOW_PREFIX`），
    之外是整句引导（`spec.HEAT_HINT`）。它是拉取那一刻的读数，与响应里每个
    数字同寿——刷新即重取。客户端原样显示，不为热力图的任何一句话持有底稿。
    """

    copy: dict[str, str]
    """统计页其余区块的描述文案，整表下发（登记表见 `spec.STATS_COPY`）。

    `{days}` 已由服务端填好；其余占位符（`{as_of}` `{t}` `{pct}`……）是运行时值，
    由客户端用 shower-utils 格式化后填入。客户端不持有这些句子的底稿——改字只改
    `spec` 一处，不必发版。
    """

    histograms: list[HistogramView]
    doors: list[DoorUsageRow]

    leaderboards: list[LeaderboardView]
    """全部榜，顺序即界面上的页签顺序。按人排与按场排混在这一列里，行形状相同。"""

    compare: list[CompareRow]
    """各设备的轻量汇总，供楼层对比。它比主视图轻得多（每台设备几个数字），
    所以不做成单独端点——分开拉反而多一次往返。"""
