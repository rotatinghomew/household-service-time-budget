"""读取并校验匿名合成资料。

资料分为三层：
- ``frame``：抽样框，含未答复家庭、家庭扩权数、受保护标记、可到场时段；
- ``diary``：按家庭抽取的出行日记，记录事项、是否办完、委托了哪些事项；
- ``events``：日记内每次行程的分角色事件（乘车、照看、等候、办件、返程）。

空值表示没有观测，不是数值零。跨日分钟数超过 1439。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROLES = ("长辈", "代办人", "同行者")
ELDER, PROXY, COMPANION = ROLES


@dataclass(frozen=True)
class Household:
    id: str
    stratum: str
    weight: int
    answered_by_cutoff: bool
    protected: bool
    persons: tuple[str, ...]
    available: dict[str, tuple[tuple[int, int], ...]]
    requires_accompaniment: bool


@dataclass(frozen=True)
class Diary:
    household: str
    trip: str
    day: str
    received: str
    activities: tuple[str, ...]
    complete: bool
    delegated: frozenset[str]


@dataclass(frozen=True)
class Event:
    kind: str
    person: str
    start: int
    end: int | None
    activity: str | None


@dataclass(frozen=True)
class Samples:
    frame: dict[str, Household]
    diary: dict[str, Diary]
    events: dict[tuple[str, str], dict[str, Event]]
    options: dict
    transport: dict
    policy: dict

    @property
    def respondents(self) -> set[str]:
        return set(self.diary)


def _household(row: dict) -> Household:
    available = {
        person: tuple((int(a), int(b)) for a, b in windows)
        for person, windows in row["available"].items()
    }
    return Household(
        id=row["id"],
        stratum=row["stratum"],
        weight=int(row["weight"]),
        answered_by_cutoff=bool(row["answer"]),
        protected=bool(row["protected"]),
        persons=tuple(row["persons"]),
        available=available,
        requires_accompaniment=bool(row["requires_accompaniment"]),
    )


def _diary(row: dict) -> Diary:
    return Diary(
        household=row["household"],
        trip=row["trip"],
        day=row["day"],
        received=row["received"],
        activities=tuple(row["activities"]),
        complete=bool(row["complete"]),
        delegated=frozenset(row["delegated"]),
    )


def _events(rows: Iterable[dict]) -> dict[tuple[str, str], dict[str, Event]]:
    trips: dict[tuple[str, str], dict[str, Event]] = {}
    for row in rows:
        event = Event(
            kind=row["kind"],
            person=row["person"],
            start=int(row["start"]),
            end=None if row["end"] is None else int(row["end"]),
            activity=row["activity"],
        )
        trips.setdefault((row["household"], row["trip"]), {})[event.kind] = event
    return trips


def load_samples(folder: Path | None = None) -> Samples:
    folder = folder or Path(__file__).resolve().parent.parent / "samples"
    raw = {
        name: json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("frame", "diary", "events", "options", "policy", "transport")
    }
    return Samples(
        frame={row["id"]: _household(row) for row in raw["frame"]},
        diary={row["household"]: _diary(row) for row in raw["diary"]},
        events=_events(raw["events"]),
        options=raw["options"],
        transport=raw["transport"],
        policy=raw["policy"],
    )


def validate(samples: Samples) -> list[str]:
    """返回资料自洽性问题的说明；空列表表示通过。"""
    problems: list[str] = []
    frame = samples.frame
    for hh, diary in samples.diary.items():
        if hh not in frame:
            problems.append(f"日记 {hh} 不在抽样框")
            continue
        household = frame[hh]
        trip_events = samples.events.get((hh, diary.trip))
        if trip_events is None:
            problems.append(f"日记 {hh}/{diary.trip} 没有事件")
            continue
        for event in trip_events.values():
            if event.person not in household.persons:
                problems.append(f"{hh}/{diary.trip} 的角色 {event.person} 不在家庭名单")
        for kind in ("乘车", "等候", "办件", "返程"):
            if kind not in trip_events:
                problems.append(f"{hh}/{diary.trip} 缺少 {kind} 事件")
        if trip_events.get("办件") and trip_events["办件"].activity not in diary.activities:
            problems.append(f"{hh}/{diary.trip} 的办件事项不在日记事项中")
        if not set(diary.delegated) <= set(diary.activities):
            problems.append(f"{hh}/{diary.trip} 的委托事项超出日记事项")
    for hh in frame:
        household = frame[hh]
        if set(household.available) != set(household.persons):
            problems.append(f"{hh} 的可到场时段与角色名单不一致")
        if household.stratum not in samples.transport:
            problems.append(f"{hh} 的片区 {household.stratum} 没有车次")
    for arrangement, acts in samples.options.items():
        for activity, opt in acts.items():
            if activity == "joint":
                continue
            window = opt["window"]
            if window[0] >= window[1]:
                problems.append(f"{arrangement}/{activity} 时间窗为空")
    cutoff = samples.policy["initial_cutoff"]
    if not any(d.received > cutoff for d in samples.diary.values()):
        problems.append("没有晚于初次截止日的回执，无法核对补齐过程")
    return problems
