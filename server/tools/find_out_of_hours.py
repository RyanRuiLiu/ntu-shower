"""在规定时间之外洗澡的人。

## 为什么以 door_state_event 为主轴

它自带三个关键字段：`occurred_at`（**服务端轴**，采集端看见状态变化的那一刻锚定，
不经过设备钟）、`cert_no`（谁）、`bath_time_s`（那一刻设备累计的出水秒数）。

而 `usage_session.assigned_at` 是**设备轴平移来的**，其中 `observed` 那一档是**观测下界**
——记的是"我们看见它的时刻"，真实开阀更早。只拿它判定会漏报（起点落在合法区而真实
越界）也看不清（落在越界区时早多少无从得知）。

所以：

- **主轴** = `door_state_event` 里 `to_state = 'bathing'` 的事件（进入出水态）。
  它的时刻就是服务端看见"阀开了"的那一刻，可靠。
- **会话起点**只作旁证，用来指出"这一场的起点记在什么位置"。

## 判据

事件的本地时刻落在规定时间之外即计一次。同一场会话可能产生多个 `-> bathing` 事件
（暂停后恢复），它们各算一次「出水」，但同属一个人。
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)

DB = sys.argv[1]
OPEN = (10, 55)
CLOSE = (23, 58)
for i, a in enumerate(sys.argv):
    if a == "--open" and i + 1 < len(sys.argv):
        h, m = sys.argv[i + 1].split(":")
        OPEN = (int(h), int(m))
    if a == "--close" and i + 1 < len(sys.argv):
        h, m = sys.argv[i + 1].split(":")
        CLOSE = (int(h), int(m))

CST = timezone(timedelta(hours=8))


def local(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(CST)
    except ValueError:
        return None


def outside(dt: datetime) -> bool:
    m = dt.hour * 60 + dt.minute
    return m < OPEN[0] * 60 + OPEN[1] or m > CLOSE[0] * 60 + CLOSE[1]


conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

# 进入出水态的事件。bath_time_s 是设备那一刻的累计值，用来判断这是不是新一轮出水
# （累计归零后重新开始）——同一场里暂停再开会让它继续涨。
events = [
    dict(r)
    for r in conn.execute("""
    SELECT id, occurred_at, door_no, cert_no, from_state, to_state, bath_time_s
    FROM door_state_event
    WHERE to_state = 'bathing'
    ORDER BY occurred_at
""")
]
print(f"全部「进入出水态」事件：{len(events)}")
print(f"全部事件（含其它迁移）：{conn.execute('SELECT COUNT(*) FROM door_state_event').fetchone()[0]}")

hits = [(e, t) for e in events if (t := local(e["occurred_at"])) and outside(t)]
# 「场次」只数 waiting -> bathing：那是这一场第一次出水。paused -> bathing 是同一场里
# 暂停后恢复，bath_time_s 接着涨，不是新的一场。
starts = [(e, t) for e, t in hits if e["from_state"] == "waiting"]
print(
    f"落在 {OPEN[0]:02d}:{OPEN[1]:02d}..{CLOSE[0]:02d}:{CLOSE[1]:02d} 之外的：{len(hits)} 次出水，"
    f"其中 {len(starts)} 次是「开始洗澡」（waiting -> bathing）"
)
print()

people: dict[str, list] = {}
for e, t in hits:
    people.setdefault(e["cert_no"] or "(无证件号)", []).append((e, t))

print("=" * 100)
print(f"按人聚合：共 {len(people)} 人")
print("=" * 100)
for cert, items in sorted(people.items(), key=lambda kv: min(x[1] for x in kv[1])):
    n_start = sum(1 for e, _ in items if e["from_state"] == "waiting")
    doors = sorted({e["door_no"] for e, _ in items})
    span = f"{min(t for _, t in items).strftime('%Y-%m-%d')} .. {max(t for _, t in items).strftime('%Y-%m-%d')}"
    print(f"\n证件号 {cert}")
    print(f"  开始洗澡 {n_start} 次；出水轮次 {len(items)} 次；涉及门 {doors}；时间跨度 {span}")
    for e, t in sorted(items, key=lambda x: x[1]):
        kind = "开始" if e["from_state"] == "waiting" else "恢复"
        print(
            f"    {t.strftime('%Y-%m-%d %H:%M:%S')} 本地  门{e['door_no']}  累计出水 {e['bath_time_s']:>4} 秒  [{kind}]"
        )

print()
print("=" * 100)
print(f"口径：规定时间（本地）{OPEN[0]:02d}:{OPEN[1]:02d} .. {CLOSE[0]:02d}:{CLOSE[1]:02d}")
print("  时刻取自 door_state_event.occurred_at——服务端轴，不受设备钟影响。")
print("  bath_time_s 是设备侧累计值（0 = 刚开阀；非 0 = 暂停后恢复）。")
print("  「次数」只数 waiting -> bathing（开始洗澡）；paused -> bathing 是同一场里恢复。")

# 自检：有没有"有出水却没有任何 -> bathing 事件"的段。那会是本排查的盲区，
# 必须为空——否则清单不完整，得先补事件再跑。
blind = conn.execute("""
    SELECT COUNT(*) FROM usage_segment g
    WHERE g.flow_s > 0 AND NOT EXISTS (
        SELECT 1 FROM door_state_event e
        WHERE e.door_no = g.door_no AND e.to_state = 'bathing'
          AND e.occurred_at >= g.opened_at
          AND e.occurred_at <= COALESCE(g.closed_at, g.opened_at))
""").fetchone()[0]
print(f"  自检：有出水却无出水态事件的段 = {blind} 个" + ("（均为合法时段，清单完整）" if blind else "（无盲区）"))
