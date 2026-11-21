"""角色时间账：把一次外出拆成当事人、代办人、被迫陪同者。

日记的原始记录口径本来就不统一：有的家庭报当事人的等候（``等候``），
有的报代办人的往返（``乘车``＋``办件``＋``返程``）。所以这里刻意分开两件事：

``engaged``（观测口径，仅对日记）
    每个人**自己被记到**的那一段：当事人＝在点等候，代办人＝乘车＋办件＋返程，
    陪同者＝并行照看。这些段长度不同、起止不同，不能直接相加当成“家庭总负担”，
    更不能用它来判断老人“少走一趟”是否真的省了时间。

``elapsed``（外出跨度）
    从出发上车到返程到达的整段，说明这次外出占用了日历上的哪一段。

对两种**替代行程**的角色负担，本模块不算：谁出行由模型明确决定，
出行角色的负担就是其外出跨度，见 ``itinerary.Itinerary.roles``。观测与模型
因此不混用口径。

缺失区间（返程结束为空）用 ``policy.missing_minutes_bounds`` 给出上下界，
绝不当作零。跨日事件保留绝对分钟，另由 ``clock`` 解释成日历时刻。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable

from .data import COMPANION, ELDER, PROXY, Samples


@dataclass(frozen=True)
class Interval:
    lo: int
    hi: int

    @property
    def minutes(self) -> int:
        return self.hi - self.lo

    def __add__(self, other: "Interval") -> "Interval":
        return Interval(self.lo + other.lo, self.hi + other.hi)


ZERO = Interval(0, 0)


@dataclass(frozen=True)
class ObservedLedger:
    engaged: dict[str, Interval]
    elapsed: Interval
    elapsed_clock: Interval
    attended: frozenset[str]

    @property
    def recorded_total(self) -> Interval:
        """日记里三人各自被记到的时间之和；这是对账量，不是家庭总负担。"""
        total = ZERO
        for value in self.engaged.values():
            total = total + value
        return total


def crosses_statistical_day(events: dict) -> bool:
    return any(
        (event.start >= 1440) or (event.end is not None and event.end >= 1440)
        for event in events.values()
    )


def clock(minute: int) -> str:
    """把当地分钟转成“次日 hh:mm”这样的日历标签。"""
    day, minute = divmod(minute, 1440)
    return f"{'次日 ' if day else ''}{minute // 60:02d}:{minute % 60:02d}"


def _back(back, missing: tuple[int, int]) -> Interval:
    """返程用时的区间；结束缺失时用政策界，绝不当零。"""
    if back.end is None:
        return Interval(missing[0], missing[1])
    duration = back.end - back.start
    return Interval(duration, duration)


def observed_ledger(samples: Samples, household: str) -> ObservedLedger:
    diary = samples.diary[household]
    events = samples.events[(household, diary.trip)]
    missing = tuple(samples.policy["missing_minutes_bounds"])
    ride = events["乘车"]
    wait = events["等候"]
    care = events["照看"]
    service = events["办件"]
    back = events["返程"]

    ride_interval = Interval(ride.start, ride.end)
    back_interval = _back(back, missing)
    service_interval = Interval(service.start, service.end)
    proxy_minutes = Interval(
        ride_interval.minutes + service_interval.minutes + back_interval.lo,
        ride_interval.minutes + service_interval.minutes + back_interval.hi,
    )
    elapsed_minutes = Interval(
        (back.start + back_interval.hi if back.end is None else back.end) - ride.start,
        (back.start + back_interval.hi if back.end is None else back.end) - ride.start,
    )
    elapsed_clock = Interval(
        ride.start,
        back.start + back_interval.hi if back.end is None else back.end,
    )

    engaged = {
        ELDER: Interval(wait.end - wait.start, wait.end - wait.start),
        PROXY: proxy_minutes,
        COMPANION: Interval(care.end - care.start, care.end - care.start),
    }
    attended = {PROXY}
    for role, window in ((ELDER, (wait.start, wait.end)), (COMPANION, (care.start, care.end))):
        if window[0] < elapsed_clock.hi and window[1] > elapsed_clock.lo:
            attended.add(role)
    return ObservedLedger(
        engaged=engaged,
        elapsed=elapsed_minutes,
        elapsed_clock=elapsed_clock,
        attended=frozenset(attended),
    )


def weighted_mean(values: Iterable[Interval], weights: Iterable[int], bound: str) -> float:
    pairs = list(zip(values, weights))
    if not pairs:
        raise ValueError("没有可汇总的记录")
    return sum(getattr(value, bound) * weight for value, weight in pairs) / sum(
        weight for _, weight in pairs
    )


def weighted_quantile(values: list[int], weights: list[int], q: float) -> float:
    ordered = sorted(zip(values, weights))
    total = sum(weights)
    if total <= 0:
        raise ValueError("权重之和必须为正")
    target = q * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return float(value)
    return float(ordered[-1][0])


@dataclass(frozen=True)
class HouseholdRecord:
    household: str
    stratum: str
    weight: int
    protected: bool
    requires_accompaniment: bool
    complete: bool
    delegated: tuple[str, ...]
    ledger: ObservedLedger
    cross_day: bool
    received: str
    answered_by_cutoff: bool


def observed_records(samples: Samples) -> dict[str, HouseholdRecord]:
    records = {}
    for household, diary in samples.diary.items():
        frame = samples.frame[household]
        events = samples.events[(household, diary.trip)]
        records[household] = HouseholdRecord(
            household=household,
            stratum=frame.stratum,
            weight=frame.weight,
            protected=frame.protected,
            requires_accompaniment=frame.requires_accompaniment,
            complete=diary.complete,
            delegated=tuple(sorted(diary.delegated)),
            ledger=observed_ledger(samples, household),
            cross_day=crosses_statistical_day(events),
            received=diary.received,
            answered_by_cutoff=frame.answered_by_cutoff,
        )
    return records


def observed_summary(samples: Samples, received_by: str | None = None) -> dict:
    records = [
        record
        for record in observed_records(samples).values()
        if received_by is None or record.received <= received_by
    ]
    weights = [record.weight for record in records]
    frame = samples.frame

    def summarise(extract) -> dict[str, float]:
        intervals = [extract(record) for record in records]
        return {
            "lo": round(weighted_mean(intervals, weights, "lo"), 1),
            "hi": round(weighted_mean(intervals, weights, "hi"), 1),
            "p50_lo": statistics.median(i.lo for i in intervals),
            "p50_hi": statistics.median(i.hi for i in intervals),
        }

    engaged = {
        role: summarise(lambda r, role=role: r.ledger.engaged[role])
        for role in (ELDER, PROXY, COMPANION)
    }
    engaged["三人记录之和"] = summarise(lambda r: r.ledger.recorded_total)
    elapsed = summarise(lambda r: r.ledger.elapsed)

    # 答复状态：抽样框里的未答复家庭单列，不并入均值。
    all_households = list(frame.values())
    answered = [hh for hh in frame if hh in samples.diary]
    late = [hh for hh in answered if not frame[hh].answered_by_cutoff]
    return {
        "frame_households": len(all_households),
        "frame_weight": sum(hh.weight for hh in all_households),
        "diary_households": len(answered),
        "diary_weight": sum(frame[hh].weight for hh in answered),
        "nonresponse_households": sorted(set(frame) - set(answered)),
        "nonresponse_weight": sum(frame[hh].weight for hh in frame if hh not in samples.diary),
        "nonresponse_by_stratum": {
            stratum: sum(
                1 for hh in frame if hh not in samples.diary and frame[hh].stratum == stratum
            )
            for stratum in sorted({hh.stratum for hh in frame.values()})
        },
        "protected_nonresponse": sorted(
            hh for hh in frame if hh not in samples.diary and frame[hh].protected
        ),
        "late_receipts": sorted(late),
        "late_receipts_weight": sum(frame[hh].weight for hh in late),
        "households": len(records),
        "weight_total": sum(weights),
        "engaged": engaged,
        "elapsed": elapsed,
        "cross_day": sorted(r.household for r in records if r.cross_day),
        "incomplete": sorted(r.household for r in records if not r.complete),
        "missing_return_end": sorted(
            r.household
            for r in records
            if samples.events[(r.household, samples.diary[r.household].trip)]["返程"].end is None
        ),
        "departed_after_22": sorted(
            r.household
            for r in records
            if samples.events[(r.household, samples.diary[r.household].trip)]["乘车"].start >= 1320
        ),
    }


def observed_role_quantiles(samples: Samples, role: str, bound: str = "lo") -> dict[str, dict[str, float]]:
    """按片区给出日记记录段的加权分位点，展示角色负担分布而不只看均值。"""
    records = list(observed_records(samples).values())
    out: dict[str, dict[str, float]] = {}
    for stratum in sorted({record.stratum for record in records}):
        subset = [record for record in records if record.stratum == stratum]
        values = [getattr(record.ledger.engaged[role], bound) for record in subset]
        weights = [record.weight for record in subset]
        out[stratum] = {
            "p25": weighted_quantile(values, weights, 0.25),
            "p50": weighted_quantile(values, weights, 0.50),
            "p75": weighted_quantile(values, weights, 0.75),
            "p90": weighted_quantile(values, weights, 0.90),
        }
    return out
