"""资料能证明什么、不能证明什么。

这一层不产生负担数字，只回答会议最容易被问倒的问题：
同一份日记里，哪些事项真有起止、哪些只有“列在事项清单里”；
可到场时段与实际事件是否兼容；``complete`` 到底指什么。
"""

from __future__ import annotations

from collections import Counter

from .data import COMPANION, ELDER, PROXY, Samples


def activity_event_coverage(samples: Samples) -> dict:
    """日记列了哪些事项，事件日志里又真有哪些事项的办件起止。"""
    requested = Counter()
    observed = Counter()
    for household, diary in samples.diary.items():
        for activity in diary.activities:
            requested[activity] += 1
        for event in samples.events[(household, diary.trip)].values():
            if event.kind == "办件" and event.activity:
                observed[event.activity] += 1
    return {
        "requested": dict(requested),
        "observed_service_events": dict(observed),
        "missing_service_events": {
            activity: requested[activity] - observed.get(activity, 0)
            for activity in requested
            if requested[activity] > observed.get(activity, 0)
        },
        "note": (
            "事件日志只记录了办件起止的事项才可用于核对办理时长；"
            "只出现在事项清单、没有办件事件的事项，其时长只能取 options.duration。"
        ),
    }


def complete_semantics(samples: Samples) -> dict:
    """``complete`` 与“返程结束是否缺失”的交叉核对。"""
    complete = [hh for hh, row in samples.diary.items() if row.complete]
    incomplete = [hh for hh, row in samples.diary.items() if not row.complete]
    missing_return = [
        hh
        for hh, row in samples.diary.items()
        if samples.events[(hh, row.trip)]["返程"].end is None
    ]
    cross = {
        (row.complete, samples.events[(hh, row.trip)]["返程"].end is None)
        for hh, row in samples.diary.items()
    }
    return {
        "complete": len(complete),
        "incomplete": len(incomplete),
        "incomplete_households": sorted(incomplete),
        "missing_return_households": sorted(missing_return),
        "complete_equals_missing_return": set(missing_return) == set(incomplete),
        "note": (
            "``complete`` 与‘返程结束是否缺失’完全重合时，它更像‘这趟是否记完整’"
            "的记录标记，不能直接当作‘事项是否办成’。未完成事项须由安排资料重排后判定。"
            if set(missing_return) == set(incomplete)
            else "``complete`` 与缺失返程不一致，需逐户核对。"
        ),
    }


def availability_conflicts(samples: Samples) -> dict:
    """实际事件是否落在本人声明的可到场时段内。

    冲突不代表日记作假；更可能是“可到场时段”只约束在办事点的区间，
    不约束整趟外出。这里把冲突分类，供敏感性分析引用。
    """
    def covers(person, household, start, end):
        return any(
            lo <= start and end <= hi
            for lo, hi in samples.frame[household].available[person]
        )

    proxy_service = []
    elder_wait = []
    companion_care = []
    venue = []
    for household, diary in samples.diary.items():
        events = samples.events[(household, diary.trip)]
        service = events["办件"]
        wait = events["等候"]
        care = events["照看"]
        ride = events["乘车"]
        back = events["返程"]
        if not covers(PROXY, household, service.start, service.end):
            proxy_service.append(household)
        if not covers(ELDER, household, wait.start, wait.end):
            elder_wait.append(household)
        if not covers(COMPANION, household, care.start, care.end):
            companion_care.append(household)
        end = back.end if back.end is not None else back.start
        for person in (ELDER, PROXY, COMPANION):
            if not covers(person, household, ride.end, end):
                venue.append(household)
                break
    return {
        "proxy_service_outside_availability": sorted(proxy_service),
        "elder_wait_outside_availability": sorted(elder_wait),
        "companion_care_outside_availability": sorted(companion_care),
        "venue_interval_not_covered": sorted(venue),
        "note": (
            "可到场时段与实际事件大面积不兼容（当事人等候、陪同照看尤甚），"
            "说明该字段更可能是‘可预约/可受理的窗口’或调查口径与事件口径不同，"
            "而不是‘人只能在此时段离家’。把它当硬约束会否掉大量真实行程，"
            "因此主口径不采用，只在敏感性里报告，并列入需访问核对的家庭类型。"
        ),
    }


def shared_trip_facts(samples: Samples) -> dict:
    """同车（同一行程号跨户共用）与多事项共乘的事实清单。"""
    by_trip: dict[str, list[str]] = {}
    for household, diary in samples.diary.items():
        by_trip.setdefault(diary.trip, []).append(household)
    shared = {trip: sorted(hhs) for trip, hhs in by_trip.items() if len(hhs) > 1}
    return {
        "trips": len(by_trip),
        "shared_trips": len(shared),
        "shared_examples": dict(sorted(shared.items())[:5]),
        "note": "同一行程号可服务多户；每户单独记账，公共乘车分钟按政策口径分摊，家庭合计不随之变化。",
    }
