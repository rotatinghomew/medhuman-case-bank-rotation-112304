"""材料接触判定的口径测试。"""
import unittest
from datetime import datetime

from ethics_assess.bundle import load_bundle
from ethics_assess.exposure import (
    build_profile, exposure_summary,
    CONFIRMED, UNKNOWN, CLEAN,
)

BUNDLE = "data"
TS = lambda s: datetime.fromisoformat(s)  # noqa: E731


class ExposureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(BUNDLE)

    def test_delivered_issue_before_session_confirms_root_tasks(self):
        # S2: R02 delivered to term3 group1 at 09-27, F31-post opens 09-29
        profile = build_profile(self.bundle, "K3100",
                                TS("2026-09-29T09:00:00+08:00"),
                                form="F31-post")
        # R02 下的 I04/I05 均应判确认接触（同源，不是只看同题号）
        self.assertEqual(profile.classification["I04"], CONFIRMED)
        self.assertEqual(profile.classification["I05"], CONFIRMED)
        self.assertIn("S2", [e.ref for e in profile.confirmed_evidence["I04"]])

    def test_uncertain_issue_yields_unknown_not_confirmed(self):
        # S3: R03 uncertain to term3 group2 -> I06/I07 未知接触
        profile = build_profile(self.bundle, "K3200",
                                TS("2026-09-29T09:00:00+08:00"),
                                form="F32-post")
        self.assertEqual(profile.classification["I06"], UNKNOWN)
        self.assertEqual(profile.classification["I07"], UNKNOWN)
        # 未知档不得被当作干净
        self.assertNotEqual(profile.classification["I06"], CLEAN)

    def test_returned_issue_does_not_expose(self):
        # S4: R04 returned to group3 -> I08/I09 仍干净
        profile = build_profile(self.bundle, "K3300",
                                TS("2026-09-29T09:00:00+08:00"),
                                form="F33-post")
        self.assertEqual(profile.classification["I08"], CLEAN)
        self.assertEqual(profile.classification["I09"], CLEAN)

    def test_issue_sent_after_session_is_ignored(self):
        # S5: R08 sent 09-30 09:00，对 09-29 后测不构成接触
        profile = build_profile(self.bundle, "K3100",
                                TS("2026-09-29T09:00:00+08:00"),
                                form="F31-post")
        self.assertEqual(profile.classification["I16"], CLEAN)
        # 但对更晚的 N1 开封（10-11）构成接触
        later = build_profile(self.bundle, "K3100",
                              TS("2026-10-11T09:00:00+08:00"),
                              form="N1")
        self.assertEqual(later.classification["I16"], CONFIRMED)

    def test_confirmed_overlap_closes_over_roots(self):
        # I16~I22 confirmed（10-11 收件）；R08 已发材料经闭包传到 I22
        profile = build_profile(
            self.bundle, "K3100", TS("2026-10-12T10:00:00+08:00"),
            as_of=TS("2026-10-12T10:00:00+08:00"), form="N2")
        self.assertEqual(profile.classification["I22"], CONFIRMED)

    def test_candidate_overlap_only_creates_unknown_and_does_not_transfer(self):
        # I04~I16 candidate：对一名本无接触的学生，只产生未知边，不进确认闭包
        profile = build_profile(
            self.bundle, "K3309", TS("2026-09-29T09:00:00+08:00"),
            as_of=TS("2026-09-29T18:00:00+08:00"), form="F33-post")
        self.assertEqual(profile.classification["I16"], UNKNOWN)
        self.assertEqual(profile.classification["I04"], UNKNOWN)

    def test_evidence_received_after_session_not_applied_retroactively(self):
        # I16~I22 确认边 10-11 才收件；对 09-29 的 F31-post 当时口径不可用
        profile = build_profile(
            self.bundle, "K3100", TS("2026-09-29T09:00:00+08:00"),
            as_of=TS("2026-09-29T09:00:00+08:00"), form="F31-post")
        self.assertEqual(profile.classification["I22"], CLEAN)
        # 用晚于证据收件的 as_of 复查：边此时已可用，经 R08 闭包作用到 I22
        later = build_profile(
            self.bundle, "K3100", TS("2026-09-29T09:00:00+08:00"),
            as_of=TS("2026-10-12T10:00:00+08:00"), form="F31-post")
        self.assertNotEqual(later.classification["I22"], CLEAN)

    def test_summary_counts_cover_whole_roster(self):
        summary = exposure_summary(self.bundle, "F32-post")
        self.assertEqual(sum(summary["I06"].values()), 18)
        self.assertEqual(summary["I06"][UNKNOWN], 18)


if __name__ == "__main__":
    unittest.main()
