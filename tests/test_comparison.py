"""分层比较、评分版次隔离与 DiD 的口径测试。"""
import tempfile
import unittest

from ethics_assess.bundle import load_bundle
from ethics_assess.attendance import build_ledger, paired_students
from ethics_assess.exposure import session_profiles, CLEAN
from ethics_assess.comparison import (
    within_term_gains, between_terms, practice_effect_did,
    rubric_version_split,
)
from tests.fixtures import minimal_bundle


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = minimal_bundle(self.tmp.name)
        self.bundle = load_bundle(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rubric_split_keeps_old_score_separate(self):
        split = rubric_version_split(self.bundle, "F-post")
        self.assertEqual(split["old_score"], ["T2"])
        self.assertIn("T1", split["common"])
        self.assertNotIn("T2", split["common"])

    def test_paired_gain_estimable_on_clean_common_stratum(self):
        ledger = build_ledger(self.bundle)
        pre_p = session_profiles(self.bundle, "F-pre")
        post_p = session_profiles(self.bundle, "F-post")
        paired = paired_students(self.bundle, "F-pre", "F-post", ledger)
        reports = within_term_gains(
            self.bundle, ledger, "F-pre", "F-post", pre_p, post_p, paired)
        # 干净 common 题层应有可估计结果；old_score 不进入任何 common 层
        estimable = [r for r in reports if r.estimate.mean is not None]
        self.assertTrue(estimable)
        for report in reports:
            self.assertEqual(report.scale, "common")
            self.assertNotIn("T2", report.tasks)

    def test_stratum_without_shared_task_is_not_estimable(self):
        # 让后测 T1 全部变为缺考（保留出件记录），使 C1 层失去有效共同题
        import csv
        from tests.fixtures import FIELDS
        answer_path = self.root / "answer.csv"
        rows = list(csv.reader(answer_path.open(encoding="utf-8")))
        body = []
        for row in rows[1:]:
            if row[1] == "F-post" and row[2] == "T1":
                row = [row[0], row[1], row[2], row[3], "",
                       "2026-09-05T09:00:00+08:00", "absent"]
            body.append(row)
        with answer_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, strict=True)
            writer.writerow(FIELDS["answer.csv"])
            writer.writerows(body)
        bundle = load_bundle(self.root)
        ledger = build_ledger(bundle)
        pre_p = session_profiles(bundle, "F-pre")
        post_p = session_profiles(bundle, "F-post")
        paired = paired_students(bundle, "F-pre", "F-post", ledger)
        reports = within_term_gains(
            bundle, ledger, "F-pre", "F-post", pre_p, post_p, paired)
        c1 = [r for r in reports if r.category == "C1"
              and r.exposure_bucket == CLEAN]
        self.assertTrue(c1)
        for report in c1:
            # 后测 T1 缺考使该层无可用学生分：要么不可估计，要么显式列出限制
            self.assertTrue(report.estimate.mean is None or report.limitations
                            or report.estimate.note)

    def test_between_terms_returns_not_estimable_without_clean_overlap(self):
        # 单届资料包内比较同场（无届间干净共同题时应标注而非编造差异）
        ledger = build_ledger(self.bundle)
        profiles = session_profiles(self.bundle, "F-post")
        reports = between_terms(
            self.bundle, ledger, "F-post", "F-post",
            profiles, profiles, {"S1"}, {"S2"})
        self.assertTrue(reports)
        self.assertTrue(any(r.estimate.note or r.limitations for r in reports))

    def test_did_structure_and_assumptions_documented(self):
        ledger = build_ledger(self.bundle)
        pre_p = session_profiles(self.bundle, "F-pre")
        post_p = session_profiles(self.bundle, "F-post")
        paired = paired_students(self.bundle, "F-pre", "F-post", ledger)
        did = practice_effect_did(
            self.bundle, ledger, "F-pre", "F-post", pre_p, post_p, paired)
        self.assertIn("by_task", did)
        self.assertIn("treated_definition", did)
        # 无接触时没有候选处理题，结论为空而不是伪造效应
        self.assertEqual(did["by_task"], {})


if __name__ == "__main__":
    unittest.main()
