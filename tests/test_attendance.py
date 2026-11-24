"""作答状态处理：重复件、缺考、缺交、未知收件、整场无记录的区分。"""
import csv
import tempfile
import unittest
from pathlib import Path

from ethics_assess.bundle import load_bundle
from ethics_assess.attendance import (
    build_ledger, session_attendance,
    DUP_DROPPED, ABSENT, OVERDUE, RECEIPT_UNKNOWN, NO_RECORD,
)
from tests.fixtures import minimal_bundle, FIELDS


def _rewrite_answers(root: Path, rows: list[list[str]]) -> None:
    with (root / "answer.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, strict=True)
        writer.writerow(FIELDS["answer.csv"])
        writer.writerows(rows)


class AttendanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = minimal_bundle(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_bundle_has_no_missing(self):
        bundle = load_bundle(self.root)
        ledger = build_ledger(bundle)
        self.assertEqual(ledger.counts(),
                         {DUP_DROPPED: 0, ABSENT: 0, OVERDUE: 0,
                          RECEIPT_UNKNOWN: 0, NO_RECORD: 0})

    def test_duplicate_retains_one_and_logs_drop(self):
        bundle = load_bundle(self.root)
        rows = list(csv.reader((self.root / "answer.csv").open(encoding="utf-8")))
        header, body = rows[0], rows[1:]
        # 给 S1/F-pre/T1/reason 增加一件 duplicate
        body.append(["S1", "F-pre", "T1", "reason", "4.1",
                     "2026-09-01T09:00:00+08:00", "duplicate"])
        _rewrite_answers(self.root, body)
        bundle = load_bundle(self.root)
        ledger = build_ledger(bundle)
        self.assertEqual(ledger.counts()[DUP_DROPPED], 1)
        # 有效件保留，学生仍属到场
        att = session_attendance(bundle, "F-pre", ledger)
        self.assertTrue(att["S1"]["attended"])

    def test_absent_overdue_unknown_are_distinct(self):
        rows = list(csv.reader((self.root / "answer.csv").open(encoding="utf-8")))
        body = rows[1:]
        rewritten = []
        for row in body:
            student, form, task, skill = row[0], row[1], row[2], row[3]
            if student == "S2" and form == "F-post":
                if task == "T1" and skill == "reason":
                    row = [student, form, task, skill, "",
                           "2026-09-05T09:00:00+08:00", "absent"]
                elif task == "T1" and skill == "alternatives":
                    row = [student, form, task, skill, "",
                           "2026-09-05T09:00:00+08:00", "overdue"]
                elif task == "T2" and skill == "reason":
                    row = [student, form, task, skill, "",
                           "", "receipt_unknown"]
            rewritten.append(row)
        _rewrite_answers(self.root, rewritten)
        bundle = load_bundle(self.root)
        ledger = build_ledger(bundle)
        counts = ledger.counts()
        self.assertGreaterEqual(counts[ABSENT], 1)
        self.assertGreaterEqual(counts[OVERDUE], 1)
        self.assertGreaterEqual(counts[RECEIPT_UNKNOWN], 1)
        # S2 仍有 T3 有效分，配对资格保留，但 T1/T2 题层在估计时被剔除
        att = session_attendance(bundle, "F-post", ledger)
        self.assertTrue(att["S2"]["attended"])
        self.assertLess(att["S2"]["valid_cells"], att["S2"]["expected_cells"])
        reasons = set(att["S2"]["missing_reasons"])
        self.assertTrue({ABSENT, OVERDUE, RECEIPT_UNKNOWN} & reasons)

    def test_student_without_any_rows_is_no_record(self):
        rows = list(csv.reader((self.root / "answer.csv").open(encoding="utf-8")))
        body = [r for r in rows[1:] if not (r[0] == "S2" and r[1] == "F-post")]
        _rewrite_answers(self.root, body)
        bundle = load_bundle(self.root)
        ledger = build_ledger(bundle)
        self.assertGreaterEqual(ledger.counts()[NO_RECORD], 1)
        att = session_attendance(bundle, "F-post", ledger)
        self.assertFalse(att["S2"]["attended"])
        self.assertIn(NO_RECORD, att["S2"]["missing_reasons"])


if __name__ == "__main__":
    unittest.main()
