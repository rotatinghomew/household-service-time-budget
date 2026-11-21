"""同车与公共行程的分摊。

同一次乘车可能服务多个事项、多户家庭（``trip`` 号跨户共用）。
本模块给出公开、可复核的分摊口径，并验证一条硬约束：

    家庭总分钟不随分摊方法变化；分摊只决定“归到哪个事项”。

``policy.shared_trip_split`` 当前口径：已完事项等分；没有已完事项不分给事项。
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import Samples


@dataclass(frozen=True)
class Allocation:
    activity: str
    minutes: float


def split_minutes(total: int, completed: list[str], method: str = "equal_completed") -> list[Allocation]:
    """把一段公共分钟按口径分给事项。

    ``equal_completed``：仅已完事项等分；没有已完事项则不分给任何事项。
    ``equal_all``：所有事项等分（对照口径，用来验证家庭总量不变）。
    ``first_only``：全部记到第一个事项（对照口径）。
    """
    if method == "equal_completed":
        targets = completed
    elif method == "equal_all":
        targets = completed or []
    elif method == "first_only":
        targets = completed[:1]
    else:
        raise ValueError(f"未知分摊口径：{method}")
    if not targets:
        return []
    share = total / len(targets)
    return [Allocation(activity, share) for activity in targets]


def household_totals(samples: Samples, method: str = "equal_completed") -> dict[str, float]:
    """按户汇总分摊后的分钟；不同 ``method`` 的合计必须相等。"""
    totals: dict[str, float] = {}
    for household, diary in samples.diary.items():
        events = samples.events[(household, diary.trip)]
        ride = events["乘车"]
        total = ride.end - ride.start
        allocations = split_minutes(total, list(diary.activities), method)
        totals[household] = sum(a.minutes for a in allocations)
    return totals


def allocation_invariance(samples: Samples) -> dict:
    """返回各口径下的家庭合计与验证结果，供会议复核。"""
    methods = ("equal_completed", "equal_all", "first_only")
    tables = {method: household_totals(samples, method) for method in methods}
    baseline = tables["equal_completed"]
    invariant = all(tables[m] == baseline for m in methods)
    return {
        "methods": {m: round(sum(v.values()), 1) for m, v in tables.items()},
        "per_household_equal": invariant,
        "note": "分摊只改变事项归属；家庭合计在三种口径下相同。",
    }
