"""两种安排的比较：总体、角色、受保护组与未完成量。

比较口径固定为“每户每个事项都由可核对的运行规则排成合法行程”：

- 主口径不用 ``frame.available`` 作为硬约束，因为实际日记与该时段本身冲突
  （见 ``calendar.integrity_findings``）；把它作为敏感性单独报告；
- 出行只使用 ``transport`` 给出的往返车次；
- 同一角色的两趟行程不得重叠；
- 委托资格按 ``options[安排][事项].delegate``，委托失效时改由当事人到场，
  陪同者重新排入，不给原行程加常数。

所有汇总都用家庭扩权数 ``frame.weight``；权重只用于总体估计，
不再乘给实际每人当独立样本。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from .burden import weighted_quantile
from .data import COMPANION, ELDER, PROXY, Samples
from .itinerary import Itinerary, build_itinerary

ARRANGEMENTS = ("分开办", "集中办")
ROLE_ORDER = (ELDER, PROXY, COMPANION)


@dataclass(frozen=True)
class Cell:
    arrangement: str
    households: int
    weight: float
    roles: dict[str, float]
    total: float
    unfinished_households: int
    unfinished_weight: float
    unfinished_activities_weight: float
    delegated_but_barred_weight: float
    completed_activities_weight: float


@dataclass(frozen=True)
class Comparison:
    cells: dict[str, Cell]
    per_household: dict[str, dict[str, Itinerary]]
    groups: dict[str, dict[str, Cell]]
    protected_unfinished: dict[str, float]
    availability_conflicts: dict[str, list[str]]
    matched: dict[str, Cell]
    matched_households: tuple[str, ...]
    unfinished_by_arrangement: dict[str, tuple[str, ...]]


def _cell(samples: Samples, households: list[str], itineraries: dict[str, Itinerary], arrangement: str) -> Cell:
    weight = sum(samples.frame[hh].weight for hh in households)
    role_totals = {role: 0.0 for role in ROLE_ORDER}
    for hh in households:
        for role, minutes in itineraries[hh].roles.items():
            role_totals[role] += minutes * samples.frame[hh].weight
    unfinished = [hh for hh in households if itineraries[hh].unfinished]
    return Cell(
        arrangement=arrangement,
        households=len(households),
        weight=float(weight),
        roles={role: round(value / weight, 1) for role, value in role_totals.items()},
        total=round(sum(role_totals.values()) / weight, 1),
        unfinished_households=len(unfinished),
        unfinished_weight=float(sum(samples.frame[hh].weight for hh in unfinished)),
        unfinished_activities_weight=float(
            sum(len(itineraries[hh].unfinished) * samples.frame[hh].weight for hh in households)
        ),
        delegated_but_barred_weight=float(
            sum(len(itineraries[hh].delegated_but_barred) * samples.frame[hh].weight for hh in households)
        ),
        completed_activities_weight=float(
            sum(len(itineraries[hh].completed) * samples.frame[hh].weight for hh in households)
        ),
    )


def compare(
    samples: Samples,
    joint_required: bool = False,
    availability_mode: str = "off",
    delegate_override: dict | None = None,
    weekend_frees_protected: bool = False,
) -> Comparison:
    """``availability_mode``：``off``（主口径）、``strict``（把可到场时段当硬约束）。

    ``delegate_override``：只用于反事实，例如“若集中办窗口仍接受原委托”。
    ``weekend_frees_protected``：仅与 ``strict`` 搭配使用；把受保护家庭的代办人
    时段并成一段，模拟“周末窗口让固定工作时段家庭也能来”。这是情景，不是事实。
    """
    model = samples
    if delegate_override is not None:
        options = copy.deepcopy(samples.options)
        for arrangement, acts in delegate_override.items():
            for activity, allowed in acts.items():
                options[arrangement][activity]["delegate"] = allowed
        model = Samples(
            frame=samples.frame,
            diary=samples.diary,
            events=samples.events,
            options=options,
            transport=samples.transport,
            policy=samples.policy,
        )

    itineraries = {}
    conflicts: dict[str, list[str]] = {arrangement: [] for arrangement in ARRANGEMENTS}
    for arrangement in ARRANGEMENTS:
        itineraries[arrangement] = {}
        for hh in model.diary:
            availability = None
            if availability_mode == "strict":
                availability = dict(model.frame[hh].available)
                if weekend_frees_protected and model.frame[hh].protected:
                    windows = availability[PROXY]
                    availability[PROXY] = ((min(a for a, _ in windows), max(b for _, b in windows)),)
            itinerary = build_itinerary(
                model,
                hh,
                arrangement,
                joint_required=joint_required,
                availability=availability,
            )
            itineraries[arrangement][hh] = itinerary
            if availability_mode == "strict" and itinerary.unfinished:
                conflicts[arrangement].append(hh)

    households = list(model.diary)
    cells = {
        arrangement: _cell(model, households, itineraries[arrangement], arrangement)
        for arrangement in ARRANGEMENTS
    }

    group_keys = {
        "受保护": [hh for hh in households if model.frame[hh].protected],
        "需陪同": [hh for hh in households if model.frame[hh].requires_accompaniment],
        "固定工作时段(保护)": [
            hh
            for hh in households
            if model.frame[hh].protected and len(model.frame[hh].available[PROXY]) > 1
        ],
    }
    groups = {
        name: {
            arrangement: _cell(model, subset, itineraries[arrangement], arrangement)
            for arrangement in ARRANGEMENTS
        }
        for name, subset in group_keys.items()
    }
    protected_unfinished = {
        arrangement: float(
            sum(
                model.frame[hh].weight
                for hh in group_keys["受保护"]
                if itineraries[arrangement][hh].unfinished
            )
        )
        for arrangement in ARRANGEMENTS
    }
    # 两种安排都完整办完的家庭：用来消除“未完成导致分钟被剔除”的选择偏差。
    matched_households = tuple(
        hh
        for hh in households
        if not itineraries["分开办"][hh].unfinished
        and not itineraries["集中办"][hh].unfinished
    )
    matched = {
        arrangement: _cell(model, list(matched_households), itineraries[arrangement], arrangement)
        for arrangement in ARRANGEMENTS
    }
    unfinished_by_arrangement = {
        arrangement: tuple(hh for hh in households if itineraries[arrangement][hh].unfinished)
        for arrangement in ARRANGEMENTS
    }
    return Comparison(
        cells=cells,
        per_household=itineraries,
        groups=groups,
        protected_unfinished=protected_unfinished,
        availability_conflicts=conflicts,
        matched=matched,
        matched_households=matched_households,
        unfinished_by_arrangement=unfinished_by_arrangement,
    )


def recommendation(comparison: Comparison, policy: dict) -> dict:
    """按事先约定判定：总体改善但保护组未完成量越线则不能推荐。"""
    limit = policy["protected_max_increase_unfinished"]
    increase = (
        comparison.protected_unfinished["集中办"] - comparison.protected_unfinished["分开办"]
    )
    overall = comparison.cells["集中办"].total - comparison.cells["分开办"].total
    matched_delta = comparison.matched["集中办"].total - comparison.matched["分开办"].total
    unfinished_increase = (
        comparison.cells["集中办"].unfinished_weight
        - comparison.cells["分开办"].unfinished_weight
    )
    if increase > limit:
        verdict = "不推荐"
        reason = f"保护组未完成量增加 {increase:g}，超过上限 {limit:g}"
    elif unfinished_increase > 0:
        verdict = "不推荐"
        reason = "集中办未完成家庭权重增加"
    elif overall < 0:
        verdict = "可推荐"
        reason = "总体分钟下降且保护组未完成量未越线"
    else:
        verdict = "不优于现行"
        reason = "总体分钟未下降"
    return {
        "verdict": verdict,
        "reason": reason,
        "overall_delta": round(overall, 1),
        "matched_delta": round(matched_delta, 1),
        "matched_households": len(comparison.matched_households),
        "matched_cells": {
            arrangement: {"total": cell.total, "roles": cell.roles}
            for arrangement, cell in comparison.matched.items()
        },
        "protected_unfinished_delta": increase,
        "limit": limit,
        "role_delta": {
            role: round(
                comparison.cells["集中办"].roles[role] - comparison.cells["分开办"].roles[role], 1
            )
            for role in ROLE_ORDER
        },
        "cells": {
            arrangement: {
                "total": cell.total,
                "roles": cell.roles,
                "unfinished_households": cell.unfinished_households,
                "unfinished_weight": cell.unfinished_weight,
                "unfinished_activities_weight": cell.unfinished_activities_weight,
                "completed_activities_weight": cell.completed_activities_weight,
                "delegated_but_barred_weight": cell.delegated_but_barred_weight,
            }
            for arrangement, cell in comparison.cells.items()
        },
        "groups": {
            name: {
                arrangement: {
                    "total": cell.total,
                    "roles": cell.roles,
                    "unfinished_weight": cell.unfinished_weight,
                }
                for arrangement, cell in cells.items()
            }
            for name, cells in comparison.groups.items()
        },
        "protected_unfinished": comparison.protected_unfinished,
    }


def role_quantile_table(samples: Samples, comparison: Comparison) -> dict:
    """把两套安排的每户角色分钟按权重取分位点，说明均值之外的分布。"""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for arrangement in ARRANGEMENTS:
        rows = comparison.per_household[arrangement]
        values = {role: [] for role in (*ROLE_ORDER, "家庭")}
        weights = []
        for hh, itinerary in rows.items():
            weights.append(samples.frame[hh].weight)
            for role in ROLE_ORDER:
                values[role].append(itinerary.roles[role])
            values["家庭"].append(itinerary.total)
        out[arrangement] = {
            role: {
                "p50": weighted_quantile(v, weights, 0.50),
                "p90": weighted_quantile(v, weights, 0.90),
                "max": max(v),
            }
            for role, v in values.items()
        }
    return out


def household_burden_table(comparison: Comparison) -> list[dict]:
    """逐户列出两安排下的角色分钟与未完成事项，便于会议追到具体家庭。"""
    rows = []
    for hh in comparison.per_household["分开办"]:
        separate = comparison.per_household["分开办"][hh]
        central = comparison.per_household["集中办"][hh]
        rows.append(
            {
                "household": hh,
                "separate": {
                    "roles": dict(separate.roles),
                    "total": separate.total,
                    "unfinished": list(separate.unfinished),
                    "barred": list(separate.delegated_but_barred),
                },
                "central": {
                    "roles": dict(central.roles),
                    "total": central.total,
                    "unfinished": list(central.unfinished),
                    "barred": list(central.delegated_but_barred),
                },
                "delta": central.total - separate.total,
                "delta_elder": central.roles[ELDER] - separate.roles[ELDER],
                "delta_proxy": central.roles[PROXY] - separate.roles[PROXY],
                "delta_companion": central.roles[COMPANION] - separate.roles[COMPANION],
            }
        )
    return rows


def households_needing_visit(samples: Samples) -> list[dict]:
    """证据不足、无法判断是否越线的家庭类型；不并入均值。"""
    frame = samples.frame
    diary = samples.diary
    out = []
    for hh in sorted(set(frame) - set(diary)):
        row = frame[hh]
        out.append(
            {
                "household": hh,
                "stratum": row.stratum,
                "weight": row.weight,
                "protected": row.protected,
                "requires_accompaniment": row.requires_accompaniment,
                "reason": "未答复：两种安排的负担与未完成量都无法判断",
            }
        )
    for hh in sorted(diary):
        if not frame[hh].answered_by_cutoff:
            out.append(
                {
                    "household": hh,
                    "stratum": frame[hh].stratum,
                    "weight": frame[hh].weight,
                    "protected": frame[hh].protected,
                    "requires_accompaniment": frame[hh].requires_accompaniment,
                    "reason": "初次截止后补回：现有结论依赖晚回执，需核对补记是否完整",
                }
            )
    return out
