"""日历时序与整次行程视图。

跨过统计日的行程不能只按“分钟数”看，必须在日历上说得通：

- 出发时刻按当地日历解释；``>=1440`` 的分钟表示次日；
- 返程结束为空时，给出返程用时的上下界，并说明整次行程最早/最晚何时结束；
- 区间没有前后锚点（例如只有返程开始、没有出发）时，不把区间当零，
  而是标为“不可定界”，列入需访问/需补充记录的家庭。

本模块只做描述与一致性检查，不产生业务比较结论。
"""

from __future__ import annotations

from dataclasses import dataclass

from .burden import Interval, clock
from .data import Samples


@dataclass(frozen=True)
class TripTimeline:
    household: str
    stratum: str
    day: str
    trip: str
    depart: str
    arrive: str
    service: tuple[str, str]
    wait: tuple[str, str]
    care: tuple[str, str]
    return_depart: str
    return_arrive: str | None
    ends_next_day: bool
    bounded: bool
    note: str


def trip_timeline(samples: Samples, household: str) -> TripTimeline:
    diary = samples.diary[household]
    frame = samples.frame[household]
    events = samples.events[(household, diary.trip)]
    ride = events["乘车"]
    wait = events["等候"]
    care = events["照看"]
    service = events["办件"]
    back = events["返程"]
    low, high = samples.policy["missing_minutes_bounds"]

    if back.end is None:
        return_arrive = None
        ends_next_day = back.start >= 1440 or back.start + high >= 1440
        note = (
            f"返程开始有记（{clock(back.start)}），结束未记；"
            f"返程用时按 {low}–{high} 分钟定界，整次行程最早 {clock(back.start + low)}、"
            f"最晚 {clock(back.start + high)} 结束。"
        )
    else:
        return_arrive = clock(back.end)
        ends_next_day = back.end >= 1440
        note = "返程有完整起止。"

    bounded = back.end is not None or ride.start < back.start
    if not bounded:
        note += " 区间缺乏前后锚点，不能当作零，需补记。"

    return TripTimeline(
        household=household,
        stratum=frame.stratum,
        day=diary.day,
        trip=diary.trip,
        depart=clock(ride.start),
        arrive=clock(ride.end),
        service=(clock(service.start), clock(service.end)),
        wait=(clock(wait.start), clock(wait.end)),
        care=(clock(care.start), clock(care.end)),
        return_depart=clock(back.start),
        return_arrive=return_arrive,
        ends_next_day=ends_next_day,
        bounded=bounded,
        note=note,
    )


def timeline_table(samples: Samples) -> list[dict]:
    rows = [trip_timeline(samples, household) for household in samples.diary]
    return [
        {
            "家庭": row.household,
            "片区": row.stratum,
            "日期": row.day,
            "行程": row.trip,
            "出发": row.depart,
            "到达": row.arrive,
            "办件": f"{row.service[0]}–{row.service[1]}",
            "当事人等候": f"{row.wait[0]}–{row.wait[1]}",
            "同行照看": f"{row.care[0]}–{row.care[1]}",
            "返程发车": row.return_depart,
            "返程到达": row.return_arrive or "未记",
            "跨日": "是" if row.ends_next_day else "",
            "可定界": "是" if row.bounded else "否",
            "说明": row.note,
        }
        for row in rows
    ]


def integrity_findings(samples: Samples) -> list[dict]:
    """可核对的时序问题；空列表表示没有发现。"""
    findings: list[dict] = []
    for household, diary in samples.diary.items():
        events = samples.events[(household, diary.trip)]
        ride = events["乘车"]
        back = events["返程"]
        wait = events["等候"]
        care = events["照看"]
        service = events["办件"]
        if ride.end < ride.start:
            findings.append({"家庭": household, "问题": "乘车结束早于开始"})
        if back.start < service.end:
            findings.append({"家庭": household, "问题": "返程发车早于办件结束"})
        if back.end is None:
            findings.append(
                {
                    "家庭": household,
                    "问题": f"返程结束缺失；按 {samples.policy['missing_minutes_bounds']} 定界，不作零",
                    "严重度": "记录缺口",
                }
            )
        if wait.end < wait.start or care.end < care.start:
            findings.append({"家庭": household, "问题": "照看或等候区间倒置"})
        if service.start < ride.end:
            findings.append({"家庭": household, "问题": "办件在到达之前开始"})
        if ride.start >= 1440:
            findings.append(
                {
                    "家庭": household,
                    "问题": f"出发时刻 {clock(ride.start)} 已跨统计日",
                    "严重度": "跨日",
                }
            )
    return findings
