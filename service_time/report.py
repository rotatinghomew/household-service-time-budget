"""生成可复核的会议材料（Markdown + JSON）。

用法：
    python3 -m service_time.report                      # 打印 Markdown
    python3 -m service_time.report --md out.md --json out.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .allocation import allocation_invariance
from .burden import COMPANION, ELDER, PROXY, observed_role_quantiles, observed_summary
from .calendar import integrity_findings, timeline_table
from .compare import (
    ARRANGEMENTS,
    ROLE_ORDER,
    compare,
    household_burden_table,
    households_needing_visit,
    recommendation,
    role_quantile_table,
)
from .data import load_samples, validate
from .evidence import (
    activity_event_coverage,
    availability_conflicts,
    complete_semantics,
    shared_trip_facts,
)
from .response import (
    assumption_ledger,
    coverage,
    nonresponse_profile,
    recommendation_dependencies,
)


def _fmt(value: float) -> str:
    text = f"{value:,.1f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def build_payload(samples) -> dict:
    base = compare(samples)
    strict = compare(samples, availability_mode="strict")
    strict_weekend = compare(
        samples, availability_mode="strict", weekend_frees_protected=True
    )
    counterfactual = compare(samples, delegate_override={"集中办": {"证件": True}})
    early = coverage(samples, received_by=samples.policy["initial_cutoff"])
    full = coverage(samples)
    base_rec = recommendation(base, samples.policy)
    strict_rec = recommendation(strict, samples.policy)
    strict_weekend_rec = recommendation(strict_weekend, samples.policy)
    counterfactual_rec = recommendation(counterfactual, samples.policy)
    return {
        "integrity": {
            "structure_problems": validate(samples),
            "findings": integrity_findings(samples),
        },
        "evidence": {
            "activity_coverage": activity_event_coverage(samples),
            "complete_semantics": complete_semantics(samples),
            "availability_conflicts": availability_conflicts(samples),
            "shared_trips": shared_trip_facts(samples),
        },
        "observed": observed_summary(samples),
        "observed_quantiles": {
            role: observed_role_quantiles(samples, role) for role in ROLE_ORDER
        },
        "timeline": timeline_table(samples),
        "allocation": allocation_invariance(samples),
        "coverage": {
            "initial_cutoff": samples.policy["initial_cutoff"],
            "followup_cutoff": samples.policy["followup_cutoff"],
            "by_initial_cutoff": {
                "households": early.diary_households,
                "weight": early.diary_weight,
                "response_rate": round(early.response_rate, 3),
                "weighted_response_rate": round(early.weighted_response_rate, 3),
            },
            "by_followup": {
                "households": full.diary_households,
                "weight": full.diary_weight,
                "response_rate": round(full.response_rate, 3),
                "weighted_response_rate": round(full.weighted_response_rate, 3),
            },
            "profile": nonresponse_profile(samples),
        },
        "comparison": {
            "base": base_rec,
            "strict_availability_sensitivity": strict_rec,
            "strict_weekend_sensitivity": strict_weekend_rec,
            "counterfactual_central_keeps_delegation": counterfactual_rec,
            "quantiles": role_quantile_table(samples, base),
            "households": household_burden_table(base),
            "needs_visit": households_needing_visit(samples),
            "availability_strict_conflicts": strict.availability_conflicts,
        },
        "dependencies": recommendation_dependencies(base, strict, counterfactual),
        "assumptions": assumption_ledger(),
        "revision": {
            "previous_cutoff": samples.policy["initial_cutoff"],
            "note": (
                "前一次（仅初次截止回执）与当前（随访补齐后）的差异，"
                "由 coverage 一节与逐户记录重算；若前一次结论不同，"
                "应说明是哪些晚回执或缺失区间被补齐。"
            ),
        },
    }


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def render_markdown(payload: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("# 周末集中办理：角色时间账与两种安排的合法性与负担")
    add("")
    add("> 本文件由 `python3 -m service_time.report` 从 `samples/` 的匿名合成资料生成。")
    add("> 时间单位为分钟；家庭扩权数只用于总体估计；未答复家庭单列，不并入均值。")
    add("")

    observed = payload["observed"]
    evidence = payload["evidence"]
    base = payload["comparison"]["base"]

    add("## 1. 抽样框、答复与未答复家庭")
    add("")
    add(
        "| 指标 | 户数 | 权重 |\n| --- | ---: | ---: |\n"
        f"| 抽样框 | {observed['frame_households']} | {observed['frame_weight']} |\n"
        f"| 日记（随访截止后） | {observed['diary_households']} | {observed['diary_weight']} |\n"
        f"| 未答复 | {len(observed['nonresponse_households'])} | {observed['nonresponse_weight']} |\n"
        f"| 初次截止后补回 | {len(observed['late_receipts'])} | {observed['late_receipts_weight']} |"
    )
    add("")
    add(
        "未答复家庭不并入均值。受保护组中的未答复家庭："
        + ("、".join(observed["protected_nonresponse"]) or "无")
        + "；这些户在两种安排下的负担与未完成量都无法判断，列入需访问清单。"
    )
    add("")

    add("## 2. 资料能证明什么")
    add("")
    coverage_info = evidence["activity_coverage"]
    add(
        f"- 事项清单：{coverage_info['requested']}；事件日志中有办件起止的只有 "
        f"{coverage_info['observed_service_events']}。"
    )
    semantics = evidence["complete_semantics"]
    add(
        f"- `complete` 为真的 {semantics['complete']} 户；为假的 {semantics['incomplete']} 户，"
        f"其中 {len(semantics['missing_return_households'])} 户返程结束缺失。"
        f"{semantics['note']}"
    )
    conflicts = evidence["availability_conflicts"]
    add(
        "- 可到场时段与实际事件的冲突："
        f"代办人办件 {len(conflicts['proxy_service_outside_availability'])} 户、"
        f"当事人等候 {len(conflicts['elder_wait_outside_availability'])} 户、"
        f"陪同照看 {len(conflicts['companion_care_outside_availability'])} 户。"
        f"{conflicts['note']}"
    )
    shared = evidence["shared_trips"]
    add(
        f"- 行程共 {shared['trips']} 个，其中 {shared['shared_trips']} 个同车跨户共用。"
        f"{shared['note']}"
    )
    add("")

    add("## 3. 日记里的角色时间（观测口径，不跨角色相加当总负担）")
    add("")
    rows = []
    for role in (ELDER, PROXY, COMPANION, "三人记录之和"):
        cell = observed["engaged"][role]
        rows.append(
            [
                role,
                _fmt(cell["lo"]),
                _fmt(cell["hi"]),
                _fmt(cell["p50_lo"]),
                _fmt(cell["p50_hi"]),
            ]
        )
    lines += _table(["角色", "记录段均值下界", "上界", "中位下界", "中位上界"], rows)
    add("")
    add(
        "当事人这一段只有“在点等候”；不代表当事人在外时间。事件日志又只记录了“证件”的办件起止，"
        "没有“登记”的办件事件，因此**无法只凭日记判断当事人是否到场**。"
        "只用等候比较“老人少走一趟”，会漏掉整段到场；只用代办人往返比较“总负担”，又只看到一个人的一趟。"
    )
    add("")

    add("## 4. 跨日、缺失区间与分摊")
    add("")
    add(f"- 跨统计日行程：{'、'.join(observed['cross_day']) or '无'}。")
    add(
        f"- 返程结束缺失：{'、'.join(observed['missing_return_end']) or '无'}；"
        "按政策给定的上下界定界，不当作零，日历视图见附录。"
    )
    allocation = payload["allocation"]
    add(
        f"- 同车分摊：三种口径下家庭合计均为 {list(allocation['methods'].values())[0]:.0f} 分钟，"
        f"每户合计是否随口径变化：{'否' if allocation['per_household_equal'] else '是（需复核）'}。"
    )
    add("")

    add("## 5. 两种安排的合法替代行程")
    add("")
    add(
        "合法性只依赖可核对的运行规则：受理时间窗、办理时长、该窗口是否接受委托、"
        "两事项能否同办、每户可用车次。委托失效时，事项改由当事人到场，陪同者按需重新排入；"
        "不是给原行程加常数，也不删除办不完的家庭。"
    )
    add("")
    rows = []
    for arrangement in ARRANGEMENTS:
        cell = base["cells"][arrangement]
        roles = cell["roles"]
        rows.append(
            [
                arrangement,
                _fmt(cell["total"]),
                _fmt(roles[ELDER]),
                _fmt(roles[PROXY]),
                _fmt(roles[COMPANION]),
                str(cell["unfinished_households"]),
                _fmt(cell["unfinished_weight"]),
                _fmt(cell["delegated_but_barred_weight"]),
            ]
        )
    lines += _table(
        ["安排", "家庭分钟均值", "当事人", "代办人", "陪同者", "未完成家庭数", "未完成权重", "委托被取消权重"],
        rows,
    )
    add("")
    add(
        f"结论：**{base['verdict']}**（{base['reason']}）。"
        f"加权总体变化 {base['overall_delta']:+.1f} 分钟/户；"
        f"当事人 {base['role_delta'][ELDER]:+.1f}、代办人 {base['role_delta'][PROXY]:+.1f}、"
        f"陪同者 {base['role_delta'][COMPANION]:+.1f}。"
    )
    add("")
    add(
        "集中办把代办人的出行压到很低，代价是当事人与陪同者必须到场："
        "总量即使接近，负担也已经从代办人转到老人和被迫陪同的人身上。"
    )
    add("")
    add("### 可比户：两种安排都能完整办完的家庭")
    add("")
    add(
        f"分开办有 {base['cells']['分开办']['unfinished_households']} 户办不完"
        f"（加权 {_fmt(base['cells']['分开办']['unfinished_weight'])}），"
        "这些户的分钟被剔除，会让“分开办”的均值显得更低，属于口径造成的偏差。"
        f"只看两种安排都能办完的 {base['matched_households']} 户："
        f"分开办 {_fmt(base['matched_cells']['分开办']['total'])}、"
        f"集中办 {_fmt(base['matched_cells']['集中办']['total'])}，"
        f"变化 {base['matched_delta']:+.1f} 分钟/户。"
    )
    add("")
    add(
        f"可比户中的角色变化：当事人 {base['matched_cells']['集中办']['roles'][ELDER] - base['matched_cells']['分开办']['roles'][ELDER]:+.1f}、"
        f"代办人 {base['matched_cells']['集中办']['roles'][PROXY] - base['matched_cells']['分开办']['roles'][PROXY]:+.1f}、"
        f"陪同者 {base['matched_cells']['集中办']['roles'][COMPANION] - base['matched_cells']['分开办']['roles'][COMPANION]:+.1f}。"
        "即使总量几乎不变，负担在角色之间重新分配了。"
    )
    add("")

    add("### 受保护组（固定工作时段无法请假的家庭）")
    add("")
    rows = []
    for arrangement in ARRANGEMENTS:
        cell = base["groups"]["受保护"][arrangement]
        rows.append([arrangement, _fmt(cell["total"]), _fmt(cell["unfinished_weight"])])
    lines += _table(["安排", "家庭分钟均值", "未完成权重"], rows)
    add("")
    add(
        f"保护组未完成量变化 {base['protected_unfinished_delta']:+.0f}（上限 {base['limit']}）。"
        "若总体改善却让保护组未完成量越线，则不能推荐。"
    )
    add("")

    for title, key in (
        ("敏感性：把可到场时段当硬约束", "strict_availability_sensitivity"),
        ("敏感性：严格口径且周末放开受保护家庭代办人时段", "strict_weekend_sensitivity"),
        ("反事实：若集中办窗口仍接受原有委托", "counterfactual_central_keeps_delegation"),
    ):
        add(f"### {title}")
        add("")
        rec = payload["comparison"][key]
        rows = []
        for arrangement in ARRANGEMENTS:
            cell = rec["cells"][arrangement]
            rows.append(
                [
                    arrangement,
                    _fmt(cell["total"]),
                    _fmt(cell["roles"][ELDER]),
                    _fmt(cell["roles"][PROXY]),
                    _fmt(cell["roles"][COMPANION]),
                    _fmt(cell["unfinished_weight"]),
                ]
            )
        lines += _table(["安排", "家庭分钟均值", "当事人", "代办人", "陪同者", "未完成权重"], rows)
        add("")
        add(f"该口径下结论为 **{rec['verdict']}**（{rec['reason']}）。")
        add("")

    add("## 6. 分布：同一户里负担给了谁")
    add("")
    rows = []
    for arrangement in ARRANGEMENTS:
        for role, cell in payload["comparison"]["quantiles"][arrangement].items():
            rows.append([arrangement, role, _fmt(cell["p50"]), _fmt(cell["p90"]), _fmt(cell["max"])])
    lines += _table(["安排", "角色", "p50", "p90", "最大"], rows)
    add("")
    add("均值之外看 p90 与最大值：总量下降也可能是把负担集中到少数陪同者身上。")
    add("")

    add("## 7. 需访问核对的家庭类型")
    add("")
    rows = []
    for row in payload["comparison"]["needs_visit"]:
        rows.append(
            [
                row["household"],
                row["stratum"],
                str(row["weight"]),
                "是" if row["protected"] else "",
                "是" if row["requires_accompaniment"] else "",
                row["reason"],
            ]
        )
    lines += _table(["家庭", "片区", "权重", "受保护", "需陪同", "原因"], rows)
    add("")
    add(
        "严格口径下额外出现的未完成家庭："
        f"分开办 {len(payload['comparison']['availability_strict_conflicts']['分开办'])} 户、"
        f"集中办 {len(payload['comparison']['availability_strict_conflicts']['集中办'])} 户；"
        "这些户不是被删除，而是证据不足、无法判断是否越线。"
    )
    add("")

    add("## 8. 现行建议依赖哪些无法确认的假设")
    add("")
    rows = [
        [row["结论要素"], row["依赖假设"], row["为何依赖"]] for row in payload["dependencies"]
    ]
    lines += _table(["结论要素", "依赖假设", "为何依赖"], rows)
    add("")

    add("## 9. 假设台账")
    add("")
    rows = [
        [row["假设"], row["资料是否可确认"], row["是否影响推荐"], row["影响"]]
        for row in payload["assumptions"]
    ]
    lines += _table(["假设", "资料是否可确认", "是否影响推荐", "影响"], rows)
    add("")

    add("## 10. 逐户替代行程（负担变化最大的户）")
    add("")
    households = {row["household"]: row for row in payload["comparison"]["households"]}
    ordered = sorted(households.values(), key=lambda r: r["delta"])
    selected = ordered[:3] + ordered[-3:]
    seen = set()
    for row in selected:
        hh = row["household"]
        if hh in seen:
            continue
        seen.add(hh)
        add(f"### {hh}（合计变化 {row['delta']:+.0f} 分钟）")
        for arrangement, key in (("分开办", "separate"), ("集中办", "central")):
            cell = row[key]
            add(
                f"- {arrangement}：角色 {cell['roles']}，合计 {cell['total']}，"
                f"未完成 {cell['unfinished'] or '无'}，委托被取消 {cell['barred'] or '无'}"
            )
        add("")

    add("## 11. 附录：整次行程日历视图（跨日与返程缺失）")
    add("")
    rows = []
    for row in payload["timeline"]:
        if row["家庭"] in observed["cross_day"] or row["返程到达"] == "未记":
            rows.append(
                [
                    row["家庭"],
                    row["片区"],
                    row["出发"],
                    row["到达"],
                    row["办件"],
                    row["返程发车"],
                    row["返程到达"],
                    row["跨日"] or "",
                    row["说明"],
                ]
            )
    lines += _table(
        ["家庭", "片区", "出发", "到达", "办件", "返程发车", "返程到达", "跨日", "说明"],
        rows,
    )
    add("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成角色时间账与两种安排的比较")
    parser.add_argument("--json", type=Path, help="把完整结果写到 JSON")
    parser.add_argument("--md", type=Path, help="把会议材料写到 Markdown")
    args = parser.parse_args(argv)

    samples = load_samples()
    payload = build_payload(samples)
    markdown = render_markdown(payload)
    if args.json:
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.md:
        args.md.write_text(markdown, encoding="utf-8")
    if not args.json and not args.md:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
