"""用生产库的真实帧重放一遍派生，检查算法在真实数据上的行为。

只读 `door_snapshot`，不写任何东西回生产库；派生结果留在内存里做统计。

## 补上库里没存的两样东西

生产的 `door_snapshot` 只有设备轴原文与 `captured_at`，**没有"设备当时的钟读数"
与"设备当时锚定的服务端时刻"**——也就是没有历史 `skew`。缺了它，`assigned_at`
会把设备轴当成服务端轴，与 `prev_at` 跨轴相比，自洽检验会大面积误判。

这两样可以从帧本身估出来：帧里最新写下的设备钟读数与 `captured_at` 是**同一刻**
（那一次写库正是这一帧的成因），两者相减即那一刻的偏差。逐帧估计就等于模拟了
"帧带设备钟读数"这条设计——也正是将来设备端要补的那个字段。

运行：python replay.py [db路径]（缺省指向生产库）
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model import Deriver, Frame, Row, parse, parse_server, row_from_payload  # noqa: E402

DB = sys.argv[1] if len(sys.argv) > 1 else "/opt/ntu-shower/server/data/ntu-shower.db"
MAX_PRINT = 12


def frame_skew(frame: Frame, prev_rows: dict[int, Row] | None) -> float | None:
    """从一帧估计设备钟偏差 = 服务端时刻 − 设备时刻。

    只取"相对上一帧真正刚写下的"时间读数：浴室 App 每次写库都给某个字段落当前
    墙上钟，而这一帧正是那次写库触发的，二者差值即那一刻的偏差。帧里没变过的
    读数是陈年的（可能是几小时前写的），用它们估计会把"读数年龄"当成偏差——
    释放帧把所有字段清空后尤其如此。释放帧没有留下任何新鲜读数，返回 None，
    让偏差保持原值。
    """
    if prev_rows is None:
        # 第一帧是 startup 全貌，所有读数都是历史遗留，没有"刚写下"的时刻
        return None
    deltas = []
    for r in frame.rows:
        prev = prev_rows.get(r.door_no)
        if prev is None:
            continue
        for text, old in (
            (r.open_time, prev.open_time),
            (r.in_time, prev.in_time),
            (r.close_time, prev.close_time),
        ):
            if not text or text == old:
                continue
            moment = parse(text)
            if moment is not None:
                deltas.append(frame.captured_at - moment)
    return min(deltas) if deltas else None


class ReplayDeriver(Deriver):
    """重放专用派生器：逐帧刷新 skew 估计，模拟生产环境里缺失的校时信息。"""

    def __init__(self) -> None:
        self._skew = 0.0
        self._estimate = 0.0
        self._prev_rows: dict[int, Row] | None = None
        self.skew_history: list[tuple[float, float]] = []
        super().__init__(known_skew=self._skew_at)

    def _skew_at(self, _t: float) -> float:
        return self._skew

    def feed(self, frame: Frame) -> None:
        estimated = frame_skew(frame, self._prev_rows)
        self._prev_rows = {r.door_no: r.copy() for r in frame.rows}
        if estimated is not None:
            self._estimate = estimated
            # 与生产同一套：变化超过阈值就直接采信，否则平滑
            if abs(estimated - self._skew) > 60:
                self._skew = estimated
            else:
                self._skew = self._skew + 0.3 * (estimated - self._skew)
            self.skew_history.append((frame.captured_at, self._skew))
        super().feed(frame)


def main() -> int:
    conn = sqlite3.connect(DB)
    # 按 captured_at 排，与生产工具 `tools/rebuild_derived.py` 的口径一致：真实库里
    # import 帧是后来才写进来的、描述的却是 100 天前，按 id 排会把历史整个堆到实时帧
    # 后面。本批数据只有 import 帧且按序导入，两种排法结果相同——但口径要对齐。
    #
    # `captured_at` 可能为空（设备还没锚上），那时退回 `received_at`，与派生时的取法一致。
    rows = conn.execute(
        "SELECT snapshot_id, source, captured_at, received_at, doors FROM door_snapshot"
        " ORDER BY COALESCE(captured_at, received_at), id"
    ).fetchall()

    deriver = ReplayDeriver()
    frames = 0
    skipped = 0
    for snapshot_id, source, captured_at, received_at, doors_json in rows:
        at = parse_server(captured_at) or parse_server(received_at)
        if at is None:
            skipped += 1
            continue
        try:
            doors = json.loads(doors_json)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(doors, list):
            skipped += 1
            continue
        parsed = [r for o in doors if isinstance(o, dict) and (r := row_from_payload(o)) is not None]
        deriver.feed(Frame(captured_at=at, rows=parsed, source=source, snapshot_id=snapshot_id))
        frames += 1

    # 收尾：数据到此为止仍在出水的段，标成未关闭——与场景驱动保持同一口径。
    # 少了这一步，重放会悄悄漏掉数据末尾那一批在途段。
    for door_no, seg in list(deriver.open_segment.items()):
        session = deriver.open_session.get(door_no)
        if session is not None:
            seg.source = "unclosed"
            session.segments.append(seg)
        deriver.open_segment.pop(door_no)

    sessions = deriver.sessions
    segs = [s for sess in sessions for s in sess.segments]

    print("=" * 74)
    print("真实数据重放")
    print("=" * 74)
    print(f"帧：读入 {frames}，跳过 {skipped}")
    print(f"状态迁移：{len(deriver.event_log)}")
    print(f"会话：{len(sessions)}    段：{len(segs)}")
    print()
    print("段来源      :", dict(Counter(s.source for s in segs)))
    print("会话完整性  :", dict(Counter(s.segments_complete for s in sessions)))
    print("观测连续性  :", dict(Counter(s.observed_span for s in sessions)))
    print("起点来源    :", dict(Counter(s.assigned_source for s in sessions)))

    flows = [s.flow_s for s in segs]
    if flows:
        flows_sorted = sorted(flows)
        mid = flows_sorted[len(flows_sorted) // 2]
        print(
            f"段时长      : 最小 {min(flows)}，中位 {mid}，最大 {max(flows)}，"
            f"平均 {sum(flows) / len(flows):.1f}，总 {sum(flows)} 秒"
        )

    print()
    print("--- 估计出的设备钟偏差（库里没存，从帧反推）---")
    history = deriver.skew_history
    if history:
        values = [v for _, v in history]
        print(f"  取样 {len(values)} 次，最小 {min(values):.0f}，最大 {max(values):.0f} 秒")
        jumps = [
            (t, v)
            for (t, v), (_, pv) in zip(history[1:], history[:-1])
            if abs(v - pv) > 60
        ]
        print(f"  跳变（相邻差 > 60 秒）：{len(jumps)} 次")
        for t, v in jumps[:MAX_PRINT]:
            print(f"    帧时刻 {t:.0f} 后偏差约为 {v:.0f} 秒")
        if len(jumps) > MAX_PRINT:
            print(f"    …… 另有 {len(jumps) - MAX_PRINT} 次")

    print()
    print("--- 异常检查 ---")
    problems: list[str] = []
    for sess in sessions:
        for i, s in enumerate(sess.segments, 1):
            if s.flow_s < 0:
                problems.append(f"负时长 门{sess.door_no} 段{i} {s.flow_s}")
            if s.flow_s > 1300:
                problems.append(f"超上限 门{sess.door_no} 段{i} {s.flow_s} 秒")
            if s.closed_at and s.opened_at and s.closed_at < s.opened_at:
                problems.append(f"段倒挂 门{sess.door_no} 段{i}")
        if sess.ended_at and sess.assigned_at and sess.ended_at < sess.assigned_at:
            problems.append(f"会话倒挂 门{sess.door_no}")
        if sess.segments and sess.assigned_at:
            first = sess.segments[0]
            if first.opened_at and first.opened_at < sess.assigned_at:
                problems.append(f"开阀早于起点 门{sess.door_no}")

    if problems:
        for p in problems[:MAX_PRINT]:
            print("  ✗", p)
        if len(problems) > MAX_PRINT:
            print(f"  …… 另有 {len(problems) - MAX_PRINT} 处")
    else:
        print("  无")
    print()
    print(f"异常合计：{len(problems)}")

    print()
    print("--- 段来源与时长交叉（看 observed 是否真的偏大）---")
    for kind in ("measured", "observed"):
        subset = [s.flow_s for s in segs if s.source == kind]
        if subset:
            print(
                f"  {kind:9s} {len(subset):5d} 段  "
                f"中位 {sorted(subset)[len(subset) // 2]:4d}  "
                f"最大 {max(subset):5d}  "
                f"合计 {sum(subset):7d} 秒"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
