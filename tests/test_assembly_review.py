"""组卷硬约束与出件后复查的口径测试。"""
import unittest
from datetime import datetime

from ethics_assess.bundle import load_bundle
from ethics_assess.assembly import evaluate_plan_set
from ethics_assess.review import review_with_overlap

BUNDLE = "data"
TS = lambda s: datetime.fromisoformat(s)  # noqa: E731


class AssemblyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(BUNDLE)
        cls.audience = {
            "N1": [s.id for s in cls.bundle.roster("3")],
            "N2": [s.id for s in cls.bundle.roster("3")],
            "N3": [s.id for s in cls.bundle.roster("3")],
        }

    def test_anchor_below_independent_use_threshold_rejected(self):
        # I02 历史独立使用 0 次，低于 min_independent_uses=2
        plan_set = evaluate_plan_set(
            self.bundle, {"N1": (["I02"], ["I08"])}, self.audience)
        codes = {v.code for v in plan_set.plans["N1"].violations}
        self.assertIn("anchor_history", codes)

    def test_exposed_anchor_rejected(self):
        # I16 对 term3 全体在 N1 开封时已确认接触（S5/R08）
        plan_set = evaluate_plan_set(
            self.bundle, {"N1": (["I16"], ["I08"])}, self.audience)
        codes = {v.code for v in plan_set.plans["N1"].violations}
        self.assertIn("exposed_audience", codes)

    def test_shared_root_independent_budget(self):
        # I08 与 I09 同 root R04，不能在两套卷各当独立题
        plan_set = evaluate_plan_set(
            self.bundle,
            {"N1": (["I18"], ["I08"]), "N2": (["I18"], ["I09", "I27"])},
            self.audience)
        n2_codes = {v.code for v in plan_set.plans["N2"].violations}
        self.assertIn("shared_root_independent", n2_codes)

    def test_consumed_task_from_seal_rejected(self):
        # I24 在 seal.csv 中已 rehearsed，机会已消耗
        plan_set = evaluate_plan_set(
            self.bundle, {"N3": (["I21"], ["I24"])}, self.audience)
        codes = {v.code for v in plan_set.plans["N3"].violations}
        self.assertIn("opportunity_consumed", codes)

    def test_grader_capacity_enforced(self):
        # N2 的 reason 容量仅 2，放 3 道题即超载
        plan_set = evaluate_plan_set(
            self.bundle,
            {"N2": (["I18"], ["I27", "I28", "I29"])}, self.audience)
        codes = {v.code for v in plan_set.plans["N2"].violations}
        self.assertIn("grader_capacity", codes)

    def test_legal_plan_links_only_via_common_anchor(self):
        plan_set = evaluate_plan_set(
            self.bundle,
            {"N1": (["I18"], ["I13", "I08"]),
             "N2": (["I18"], ["I27", "I28"])},
            self.audience)
        self.assertTrue(plan_set.plans["N1"].is_legal())
        self.assertEqual(plan_set.cross_form_links["N1|N2"], ["I18"])

    def test_forms_without_shared_anchor_have_no_common_scale(self):
        plan_set = evaluate_plan_set(
            self.bundle,
            {"N1": (["I18"], ["I13", "I08"]),
             "N3": (["I21"], ["I14", "I11"])},
            self.audience)
        self.assertEqual(plan_set.cross_form_links["N1|N3"], [])
        self.assertTrue(
            any(v.code == "no_shared_scale" for v in plan_set.set_violations))


class ReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(BUNDLE)
        cls.audience = {
            "N1": [s.id for s in cls.bundle.roster("3")],
            "N2": [s.id for s in cls.bundle.roster("3")],
            "N3": [s.id for s in cls.bundle.roster("3")],
        }

    def test_new_confirmed_edge_flags_opened_form_but_keeps_scores(self):
        edges = [("I18", "I04", "confirmed",
                  TS("2026-10-11T15:00:00+08:00"))]
        report = review_with_overlap(
            self.bundle, edges, TS("2026-10-13T18:00:00+08:00"),
            self.audience, extra_form_tasks={"N1": ["I18"]})
        findings = [f for f in report.findings if f.task == "I18"]
        self.assertTrue(findings)
        self.assertTrue(all(f.new_bucket == "confirmed_exposure" for f in findings))
        # 已开考：分数有效，但陌生情境能力说法不再成立
        self.assertTrue(all(not f.score_claim_still_valid for f in findings))
        self.assertIn("N1", report.opened_claims)

    def test_sealed_form_requires_action_before_opening(self):
        # N2 封存中、10-12 开封；证据 10-12 14:00（开封后）才到，把干净题 I28
        # 与已发材料 R02(I04) 确认连起来，触发对仍在封存流程题组的复查处置
        edges = [("I28", "I04", "confirmed",
                  TS("2026-10-12T14:00:00+08:00"))]
        report = review_with_overlap(
            self.bundle, edges, TS("2026-10-12T18:00:00+08:00"),
            self.audience, extra_form_tasks={"N2": ["I28"]})
        sealed = [f for f in report.findings if f.form == "N2"]
        self.assertTrue(sealed)
        self.assertTrue(all("撤换" in f.action_required or "撤回" in f.action_required
                            for f in sealed))

    def test_evidence_before_as_of_boundary_respected(self):
        # as_of 等于 N1 开封时刻、早于证据收件：证据不纳入，N1 无发现
        edges = [("I18", "I04", "confirmed",
                  TS("2026-10-11T15:00:00+08:00"))]
        report = review_with_overlap(
            self.bundle, edges, TS("2026-10-11T09:00:00+08:00"),
            self.audience, extra_form_tasks={"N1": ["I18"]})
        n1 = [f for f in report.findings if f.form == "N1"]
        self.assertEqual(n1, [])


if __name__ == "__main__":
    unittest.main()
