"""在两种安排下，为同一份日记构造合法的替代行程。

合法性的硬约束只取可公开核对的运行规则：

- ``options[安排][事项].window``：受理时间窗；
- ``options[安排][事项].duration``：办理时长；
- ``options[安排][事项].delegate``：该窗口是否接受委托；
- ``options[安排].joint``：两事项能否同办；
- ``transport[片区]``：每户可用往返车次（发车—到达）。

``frame.available``（各角色可到场时段）作为**敏感性约束**单独开关：
严格模式下它会否掉一部分实际日记，说明资料之间存在冲突，不能据此
把人悄悄删掉；默认比较只用上面五条运行规则。

几条明确口径：

1. 委托失效（原来可代办、新窗口不接受委托）时，事项改由当事人到场；
   陪同者随之重新排入，而不是给原行程加一个常数。
2. 办不完就是办不完：未完成事项从该户负担里剔除，但该户仍保留在样本中，
   并计入未完成权重。
3. 同一角色不能同时出现在两趟重叠的行程里。
4. 本模块不使用任何“在家等待”的常数；未出行角色的负担为 0，
   出行角色的负担就是其在外跨度。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .data import COMPANION, ELDER, PROXY, Samples

Availability = dict[str, tuple[tuple[int, int], ...]]


@dataclass(frozen=True)
class Job:
    activity: str
    handler: str
    duration: int
    window: tuple[int, int]


@dataclass(frozen=True)
class Trip:
    activities: tuple[str, ...]
    travelers: tuple[str, ...]
    depart: int
    arrive: int
    return_depart: int
    return_arrive: int
    starts: dict[str, tuple[int, int]]

    @property
    def span(self) -> int:
        return self.return_arrive - self.depart

    def role_minutes(self) -> dict[str, int]:
        minutes = {ELDER: 0, PROXY: 0, COMPANION: 0}
        for person in self.travelers:
            minutes[person] += self.span
        return minutes


@dataclass(frozen=True)
class Itinerary:
    arrangement: str
    grouped: str
    trips: tuple[Trip, ...]
    roles: dict[str, int]
    completed: tuple[str, ...]
    unfinished: tuple[str, ...]
    delegated_but_barred: tuple[str, ...]
    handlers: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.roles.values())


def _covers(intervals, start: int, end: int) -> bool:
    return any(lo <= start and end <= hi for lo, hi in intervals)


def _earliest_start(intervals, earliest: int, duration: int, limit: int) -> int | None:
    candidates = []
    for lo, hi in intervals:
        begin = max(earliest, lo)
        if begin + duration <= min(hi, limit):
            candidates.append(begin)
    return min(candidates) if candidates else None


def _schedule(
    jobs: list[Job],
    arrive: int,
    return_depart: int,
    availability: dict[str, Availability] | None,
) -> dict[str, tuple[int, int]] | None:
    """同一角色串行、不同角色并行；每项完整落在时间窗与返程发车之前。"""
    by_handler: dict[str, list[Job]] = {}
    for job in jobs:
        by_handler.setdefault(job.handler, []).append(job)
    handlers = list(by_handler)
    best: tuple[int, dict[str, tuple[int, int]]] | None = None
    for combo in itertools.product(*[list(itertools.permutations(by_handler[h])) for h in handlers]):
        starts: dict[str, tuple[int, int]] = {}
        ok = True
        for handler, order in zip(handlers, combo):
            cursor = arrive
            for job in order:
                limit = min(job.window[1], return_depart)
                earliest = max(cursor, job.window[0])
                if availability is None:
                    begin = earliest if earliest + job.duration <= limit else None
                else:
                    begin = _earliest_start(
                        availability[handler], earliest, job.duration, limit
                    )
                if begin is None:
                    ok = False
                    break
                starts[job.activity] = (begin, begin + job.duration)
                cursor = begin + job.duration
            if not ok:
                break
        if not ok:
            continue
        end = max(e for _, e in starts.values())
        if best is None or end < best[0]:
            best = (end, starts)
    return None if best is None else best[1]


def _trip_candidates(
    samples: Samples,
    household: str,
    jobs: list[Job],
    availability: dict[str, Availability] | None,
) -> list[Trip]:
    frame = samples.frame[household]
    handlers = {job.handler for job in jobs}
    travelers = set(handlers)
    if ELDER in handlers and frame.requires_accompaniment:
        travelers.add(COMPANION)
    trips: list[Trip] = []
    for depart, arrive in samples.transport[frame.stratum]["outbound"]:
        for return_depart, return_arrive in samples.transport[frame.stratum]["return"]:
            if return_depart < arrive:
                continue
            starts = _schedule(jobs, arrive, return_depart, availability)
            if starts is None:
                continue
            if ELDER in handlers and frame.requires_accompaniment and availability is not None:
                if not all(
                    _covers(availability[COMPANION], *starts[job.activity])
                    for job in jobs
                    if job.handler == ELDER
                ):
                    continue
            trips.append(
                Trip(
                    activities=tuple(job.activity for job in jobs),
                    travelers=tuple(sorted(travelers)),
                    depart=depart,
                    arrive=arrive,
                    return_depart=return_depart,
                    return_arrive=return_arrive,
                    starts=starts,
                )
            )
    return trips


def _handler_choices(samples: Samples, household: str, arrangement: str, activity: str):
    """返回 (可选承办角色, 是否“委托被新窗口取消”)。"""
    delegated = activity in samples.diary[household].delegated
    allowed = samples.options[arrangement][activity]["delegate"]
    if delegated and not allowed:
        return [ELDER], True
    if delegated:
        return [PROXY], False
    return [ELDER], False


def _assemble(
    samples,
    household,
    arrangement,
    pairs,
    joint_required: bool,
    availability: dict[str, Availability] | None,
) -> Itinerary | None:
    """枚举“分两趟”和“同办”两种结构，再枚举车次组合；按总分钟取最优。"""
    options = samples.options[arrangement]
    jobs = [
        Job(activity, handler, options[activity]["duration"], tuple(options[activity]["window"]))
        for activity, handler in pairs
    ]
    completed = tuple(activity for activity, _ in pairs)
    groupings = []
    if not joint_required or not options["joint"]:
        groupings.append([[job] for job in jobs])
    if options["joint"]:
        groupings.append([jobs])
    best: tuple[tuple, Itinerary] | None = None
    for groups in groupings:
        per_group = [
            _trip_candidates(samples, household, group, availability) for group in groups
        ]
        if any(not candidates for candidates in per_group):
            continue
        for combination in itertools.product(*per_group):
            trips = list(combination)
            clash = False
            for i in range(len(trips)):
                for j in range(i + 1, len(trips)):
                    if set(trips[i].travelers) & set(trips[j].travelers):
                        if not (
                            trips[i].return_arrive <= trips[j].depart
                            or trips[j].return_arrive <= trips[i].depart
                        ):
                            clash = True
            if clash:
                continue
            roles = {ELDER: 0, PROXY: 0, COMPANION: 0}
            for trip in trips:
                for person, minutes in trip.role_minutes().items():
                    roles[person] += minutes
            itinerary = Itinerary(
                arrangement=arrangement,
                grouped="joint" if len(groups) == 1 and len(groups[0]) > 1 else "separate",
                trips=tuple(trips),
                roles=roles,
                completed=completed,
                unfinished=(),
                delegated_but_barred=(),
                handlers={activity: handler for activity, handler in pairs},
            )
            key = (itinerary.total, len(trips), roles[ELDER])
            if best is None or key < best[0]:
                best = (key, itinerary)
    return None if best is None else best[1]


def build_itinerary(
    samples: Samples,
    household: str,
    arrangement: str,
    joint_required: bool = False,
    availability: dict[str, Availability] | None = None,
) -> Itinerary:
    """为某户在某安排下排合法行程。办不完的事项进入 ``unfinished``，不删除该户。"""
    activities = list(samples.diary[household].activities)
    choices = []
    barred: set[str] = set()
    for activity in activities:
        options, invalidated = _handler_choices(samples, household, arrangement, activity)
        choices.append(options)
        if invalidated:
            barred.add(activity)

    for handler_set in itertools.product(*choices):
        itinerary = _assemble(
            samples,
            household,
            arrangement,
            list(zip(activities, handler_set)),
            joint_required,
            availability,
        )
        if itinerary is not None:
            return Itinerary(
                **{**itinerary.__dict__, "delegated_but_barred": tuple(sorted(barred))}
            )

    for size in range(len(activities) - 1, 0, -1):
        candidates: list[Itinerary] = []
        for subset in itertools.combinations(activities, size):
            sub = list(subset)
            sub_choices = [options for activity, options in zip(activities, choices) if activity in sub]
            for handler_set in itertools.product(*sub_choices):
                itinerary = _assemble(
                    samples,
                    household,
                    arrangement,
                    list(zip(sub, handler_set)),
                    joint_required,
                    availability,
                )
                if itinerary is not None:
                    candidates.append(
                        Itinerary(
                            **{
                                **itinerary.__dict__,
                                "unfinished": tuple(a for a in activities if a not in sub),
                                "delegated_but_barred": tuple(sorted(barred)),
                            }
                        )
                    )
        if candidates:
            return min(candidates, key=lambda i: (i.total, len(i.trips)))
    return Itinerary(
        arrangement=arrangement,
        grouped="none",
        trips=(),
        roles={ELDER: 0, PROXY: 0, COMPANION: 0},
        completed=(),
        unfinished=tuple(activities),
        delegated_but_barred=tuple(sorted(barred)),
    )
