"""服务时间账的行为测试。

这些测试锁定几件容易在后续改动中悄悄破坏的事：
- 缺失区间不能当零；
- 分摊只改归属、不改家庭合计；
- 两种安排的替代行程必须合法（时间窗、承办角色、车次）；
- 委托失效会重排当事人与陪同者，而不是给原行程加常数；
- 未完成事项进入 unfinished，不被删除，也不被算成零；
- 未答复家庭不进入均值，权重只用于总体估计。
"""

from __future__ import annotations

import unittest

from service_time.allocation import allocation_invariance, split_minutes
from service_time.burden import ELDER, PROXY, COMPANION, clock, observed_ledger, observed_summary
from service_time.calendar import integrity_findings, timeline_table
from service_time.compare import compare, recommendation
from service_time.data import load_samples, validate
from service_time.evidence import complete_semantics
from service_time.itinerary import build_itinerary
from service_time.response import assumption_ledger, coverage


class SampleFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = load_samples()
        cls.base = compare(cls.samples)
        cls.rec = recommendation(cls.base, cls.samples.policy)


class DataIntegrityTests(SampleFixture):
    def test_structure_is_consistent(self):
        self.assertEqual(validate(self.samples), [])

    def test_roles_and_trips_follow_frame(self):
        frame = self.samples.frame
        for household, diary in self.samples.diary.items():
            self.assertIn(household, frame)
            for event in self.samples.events[(household, diary.trip)].values():
                self.assertIn(event.person, frame[household].persons)

    def test_missing_return_is_not_zero(self):
        summary = observed_summary(self.samples)
        self.assertTrue(summary["missing_return_end"])
        for household in summary["missing_return_end"]:
            ledger = observed_ledger(self.samples, household)
            low, high = self.samples.policy["missing_minutes_bounds"]
            self.assertGreaterEqual(ledger.engaged[PROXY].hi, ledger.engaged[PROXY].lo)
            self.assertGreaterEqual(ledger.engaged[PROXY].hi - ledger.engaged[PROXY].lo, high - low)

    def test_cross_day_is_labelled(self):
        self.assertTrue(self.samples.policy["missing_minutes_bounds"])
        self.assertGreaterEqual(clock(1440), "次日 ")
        self.assertEqual(clock(600), "10:00")
        summary = observed_summary(self.samples)
        self.assertTrue(summary["cross_day"])
        findings = integrity_findings(self.samples)
        self.assertTrue(any(f["问题"].startswith("返程结束缺失") for f in findings))

    def test_complete_matches_missing_return(self):
        semantics = complete_semantics(self.samples)
        self.assertTrue(semantics["complete_equals_missing_return"])


class LedgerTests(SampleFixture):
    def test_role_segments_are_durations_not_clock_times(self):
        for household in self.samples.diary:
            ledger = observed_ledger(self.samples, household)
            for value in ledger.engaged.values():
                self.assertLess(value.lo, 1440)
                self.assertLessEqual(value.lo, value.hi)

    def test_observed_summary_does_not_double_count_weights(self):
        summary = observed_summary(self.samples)
        self.assertEqual(summary["diary_weight"], 640)
        self.assertEqual(summary["nonresponse_weight"], summary["frame_weight"] - summary["diary_weight"])


class AllocationTests(SampleFixture):
    def test_household_totals_are_invariant(self):
        result = allocation_invariance(self.samples)
        self.assertTrue(result["per_household_equal"])

    def test_split_only_changes_attribution(self):
        completed = ["证件", "登记"]
        equal = split_minutes(60, completed, "equal_completed")
        lumped = split_minutes(60, completed, "first_only")
        self.assertAlmostEqual(sum(a.minutes for a in equal), 60)
        self.assertAlmostEqual(sum(a.minutes for a in lumped), 60)

    def test_no_completed_activities_gets_nothing(self):
        self.assertEqual(split_minutes(60, [], "equal_completed"), [])


class ItineraryTests(SampleFixture):
    def test_every_household_has_an_entry_for_both_arrangements(self):
        for household in self.samples.diary:
            for arrangement in ("分开办", "集中办"):
                itinerary = build_itinerary(self.samples, household, arrangement)
                self.assertIsNotNone(itinerary)
                self.assertEqual(
                    set(itinerary.completed) | set(itinerary.unfinished),
                    set(self.samples.diary[household].activities),
                )

    def test_trips_respect_windows_and_transport(self):
        for household, diary in self.samples.diary.items():
            frame = self.samples.frame[household]
            transport = self.samples.transport[frame.stratum]
            for arrangement in ("分开办", "集中办"):
                itinerary = build_itinerary(self.samples, household, arrangement)
                options = self.samples.options[arrangement]
                for trip in itinerary.trips:
                    self.assertIn([trip.depart, trip.arrive], transport["outbound"])
                    self.assertIn([trip.return_depart, trip.return_arrive], transport["return"])
                    for activity, (start, end) in trip.starts.items():
                        window = options[activity]["window"]
                        self.assertGreaterEqual(start, window[0])
                        self.assertLessEqual(end, window[1])
                        self.assertEqual(end - start, options[activity]["duration"])
                    self.assertLessEqual(max(e for _, e in trip.starts.values()), trip.return_depart)

    def test_elder_travel_requires_companion_when_needed(self):
        for household, diary in self.samples.diary.items():
            if not self.samples.frame[household].requires_accompaniment:
                continue
            for arrangement in ("分开办", "集中办"):
                for trip in build_itinerary(self.samples, household, arrangement).trips:
                    if ELDER in trip.travelers:
                        self.assertIn(COMPANION, trip.travelers)

    def test_delegation_loss_moves_the_elder(self):
        central = build_itinerary(self.samples, "户001", "集中办")
        self.assertTrue(central.delegated_but_barred)
        self.assertTrue(any(ELDER in trip.travelers for trip in central.trips))

    def test_parallel_trips_do_not_share_a_traveller(self):
        for household in self.samples.diary:
            for arrangement in ("分开办", "集中办"):
                trips = build_itinerary(self.samples, household, arrangement).trips
                for i in range(len(trips)):
                    for j in range(i + 1, len(trips)):
                        if set(trips[i].travelers) & set(trips[j].travelers):
                            self.assertTrue(
                                trips[i].return_arrive <= trips[j].depart
                                or trips[j].return_arrive <= trips[i].depart
                            )


class ComparisonTests(SampleFixture):
    def test_unfinished_is_preserved_and_weighted(self):
        itineraries = self.base.per_household["分开办"]
        unfinished = self.base.unfinished_by_arrangement["分开办"]
        self.assertTrue(unfinished)
        reported = {household for household, itinerary in itineraries.items() if itinerary.unfinished}
        for household in unfinished:
            self.assertTrue(build_itinerary(self.samples, household, "分开办").unfinished)
            self.assertIn(household, reported)
            # 未完成户仍有记录，不是被静默删除
            self.assertIn(household, itineraries)

    def test_matched_comparison_excludes_unfinished(self):
        for household in self.base.matched_households:
            for arrangement in ("分开办", "集中办"):
                self.assertFalse(self.base.per_household[arrangement][household].unfinished)
        self.assertLess(len(self.base.matched_households), len(self.samples.diary))

    def test_recommendation_respects_protected_limit(self):
        limit = self.samples.policy["protected_max_increase_unfinished"]
        increase = (
            self.rec["protected_unfinished"]["集中办"]
            - self.rec["protected_unfinished"]["分开办"]
        )
        if increase > limit:
            self.assertEqual(self.rec["verdict"], "不推荐")

    def test_sensitivity_changes_are_reported(self):
        strict = compare(self.samples, availability_mode="strict")
        self.assertNotEqual(
            strict.cells["分开办"].total, self.base.cells["分开办"].total
        )
        counter = compare(self.samples, delegate_override={"集中办": {"证件": True}})
        self.assertEqual(counter.cells["集中办"].delegated_but_barred_weight, 0.0)


class ResponseTests(SampleFixture):
    def test_nonresponse_is_separate_from_mean(self):
        full = coverage(self.samples)
        early = coverage(self.samples, received_by=self.samples.policy["initial_cutoff"])
        self.assertGreater(len(early.nonresponse), len(full.nonresponse))
        self.assertEqual(
            full.frame_households,
            full.diary_households + len(full.nonresponse),
        )

    def test_assumption_ledger_flags_unconfirmable_items(self):
        ledger = assumption_ledger()
        self.assertTrue(any(row["资料是否可确认"].startswith("否") for row in ledger))
        self.assertTrue(any("分摊" in row["假设"] for row in ledger))


class CalendarTests(SampleFixture):
    def test_timeline_bounds_missing_returns(self):
        rows = {row["家庭"]: row for row in timeline_table(self.samples)}
        for household in observed_summary(self.samples)["missing_return_end"]:
            self.assertEqual(rows[household]["返程到达"], "未记")
            self.assertIn("定界", rows[household]["说明"])
            self.assertEqual(rows[household]["可定界"], "是")


if __name__ == "__main__":
    unittest.main()
