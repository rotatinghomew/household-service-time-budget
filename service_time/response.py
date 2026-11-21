"""非应答、晚回执与假设台账。

抽样框（``frame``）记录未答复家庭；日记（``diary``）只覆盖答复户。
家庭权重用于总体估计，不能再乘给“实际每人”当作独立样本。

访问回执补齐部分非应答后，会议需要能查两件事：
- 现行建议哪些仍依赖无法确认的分摊或响应假设（``recommendation_dependencies``）；
- 前一次建议为什么不同（``revision_explanation``）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import Samples


@dataclass(frozen=True)
class Coverage:
    frame_households: int
    frame_weight: int
    diary_households: int
    diary_weight: float
    nonresponse: tuple[str, ...]
    nonresponse_weight: float
    late_receipts: tuple[str, ...]
    late_receipt_weight: float

    @property
    def response_rate(self) -> float:
        return self.diary_households / self.frame_households if self.frame_households else 0.0

    @property
    def weighted_response_rate(self) -> float:
        return self.diary_weight / self.frame_weight if self.frame_weight else 0.0


def coverage(samples: Samples, received_by: str | None = None) -> Coverage:
    frame = samples.frame
    diary = samples.diary
    if received_by is None:
        respondents = set(diary)
    else:
        respondents = {hh for hh, row in diary.items() if row.received <= received_by}
    nonresponse = tuple(sorted(set(frame) - respondents))
    late = tuple(sorted(hh for hh in respondents if not frame[hh].answered_by_cutoff))
    return Coverage(
        frame_households=len(frame),
        frame_weight=sum(hh.weight for hh in frame.values()),
        diary_households=len(respondents),
        diary_weight=float(sum(frame[hh].weight for hh in respondents)),
        nonresponse=nonresponse,
        nonresponse_weight=float(sum(frame[hh].weight for hh in nonresponse)),
        late_receipts=late,
        late_receipt_weight=float(sum(frame[hh].weight for hh in late)),
    )


def nonresponse_profile(samples: Samples) -> dict:
    frame = samples.frame
    nonresponse = sorted(set(frame) - set(samples.diary))
    by_stratum: dict[str, dict[str, float]] = {}
    for stratum in sorted({hh.stratum for hh in frame.values()}):
        all_rows = [frame[hh] for hh in frame if frame[hh].stratum == stratum]
        missing = [frame[hh] for hh in nonresponse if frame[hh].stratum == stratum]
        by_stratum[stratum] = {
            "frame": len(all_rows),
            "nonresponse": len(missing),
            "frame_weight": sum(h.weight for h in all_rows),
            "nonresponse_weight": sum(h.weight for h in missing),
        }
    protected_missing = [hh for hh in nonresponse if frame[hh].protected]
    returns = [
        (hh, samples.diary[hh].received)
        for hh in sorted(samples.diary)
        if samples.diary[hh].received > samples.policy["initial_cutoff"]
    ]
    return {
        "nonresponse": nonresponse,
        "by_stratum": by_stratum,
        "protected_nonresponse": protected_missing,
        "protected_nonresponse_weight": sum(frame[hh].weight for hh in protected_missing),
        "late_receipts": returns,
    }


# 每条假设：是否影响“总体/角色/未完成”的结论，以及会议可怎么核对。
ASSUMPTIONS = [
    {
        "假设": "未答复家庭在两种安排下的负担与答复户同分布",
        "资料是否可确认": "否",
        "影响": "总体均值与保护组均值的外推；这些户已单列为需访问",
        "是否影响推荐": "是（仅影响外推，不影响已答复户内的比较）",
    },
    {
        "假设": "同车行程的乘车分钟按已完事项等分（分摊口径）",
        "资料是否可确认": "口径由 policy 给出；具体分摊不可核对",
        "影响": "事项层面的分钟归属",
        "是否影响推荐": "否（家庭合计在三种口径下相同，见分摊不变性）",
    },
    {
        "假设": "返程结束缺失区间的用时落在政策给定界内",
        "资料是否可确认": "界由 policy 给出；具体值不可确认",
        "影响": "代办人与家庭分钟的上界",
        "是否影响推荐": "是（区间结论，不能给点估计）",
    },
    {
        "假设": "可到场时段只约束在点时段，不约束整趟外出",
        "资料是否可确认": "与实际事件大面积冲突，无法确认",
        "影响": "主口径的可行性；严格口径会多出未完成家庭",
        "是否影响推荐": "是（换口径会改变未完成量与均值）",
    },
    {
        "假设": "同一代办人的受托资格可在不同事项之间复用",
        "资料是否可确认": "否，资料只给每事项是否允许委托",
        "影响": "代办人趟次与等待",
        "是否影响推荐": "是（若不可复用，代办人负担上升）",
    },
    {
        "假设": "集中办证件窗口不接受委托后，原代办人不再代办",
        "资料是否可确认": "是，由 options.delegate 给出",
        "影响": "当事人与陪同者按新行程重排",
        "是否影响推荐": "该假设本身可确认，不属“不可确认”项",
    },
]


def assumption_ledger() -> list[dict]:
    return [dict(row) for row in ASSUMPTIONS]


def recommendation_dependencies(
    comparison,
    strict_comparison,
    counterfactual_comparison,
) -> list[dict]:
    """现行建议的哪些部分依赖无法确认的假设。"""
    base = comparison.cells
    strict = strict_comparison.cells
    counter = counterfactual_comparison.cells
    return [
        {
            "结论要素": "总体均值下降/不下降",
            "依赖假设": "可到场时段口径",
            "为何依赖": (
                f"主口径下两安排合计为 {base['分开办'].total:.1f} / {base['集中办'].total:.1f}；"
                f"严格口径下为 {strict['分开办'].total:.1f} / {strict['集中办'].total:.1f}"
            ),
        },
        {
            "结论要素": "保护组未完成量是否越线",
            "依赖假设": "可到场时段口径 + 未答复户同分布",
            "为何依赖": (
                f"主口径保护组未完成权重 {base['分开办'].unfinished_weight:.0f} → "
                f"{base['集中办'].unfinished_weight:.0f}；"
                f"严格口径 {strict['分开办'].unfinished_weight:.0f} → {strict['集中办'].unfinished_weight:.0f}"
            ),
        },
        {
            "结论要素": "代办人负担下降是否真实",
            "依赖假设": "集中办委托资格",
            "为何依赖": (
                f"按现行 options，集中办有 {base['集中办'].delegated_but_barred_weight:.0f} "
                f"加权事项失去代办资格；若保留代办资格，合计为 "
                f"{counter['分开办'].total:.1f} / {counter['集中办'].total:.1f}"
            ),
        },
        {
            "结论要素": "返程缺失区间的代办人分钟",
            "依赖假设": "缺失区间定界",
            "为何依赖": "缺失返程的户按上下界定界；点估计不可得",
        },
        {
            "结论要素": "事项层面分摊",
            "依赖假设": "同车分摊",
            "为何依赖": "分摊只改变归属；家庭合计不随口径变化，故不影响推荐",
        },
    ]


def revision_explanation(previous: dict, current: dict) -> list[dict]:
    """对比前一次与当前建议，逐条说明为什么不同。"""
    explanation = []
    for arrangement in ("分开办", "集中办"):
        prev = previous["cells"][arrangement]
        curr = current["cells"][arrangement]
        if prev["total"] != curr["total"]:
            explanation.append(
                {
                    "安排": arrangement,
                    "变化": f"合计 {prev['total']:.1f} → {curr['total']:.1f}",
                    "原因": "补齐晚回执/修正缺失区间后，代办人与家庭成员分钟重新汇总",
                }
            )
    if previous["verdict"] != current["verdict"]:
        explanation.append(
            {
                "安排": "总体",
                "变化": f"结论 {previous['verdict']} → {current['verdict']}",
                "原因": "保护组未完成量或总体的变化方向改变",
            }
        )
    return explanation
