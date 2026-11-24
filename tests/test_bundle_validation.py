"""资料包严格校验：不符合口径的输入必须被拒绝，而不是静默运行。"""
import csv
import tempfile
import unittest

from ethics_assess.bundle import load_bundle, BundleError
from tests.fixtures import minimal_bundle, FIELDS


class BundleValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = minimal_bundle(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _rewrite(self, filename: str, rows: list[list[str]]) -> None:
        with (self.root / filename).open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, strict=True)
            writer.writerow(FIELDS[filename])
            writer.writerows(rows)

    def test_valid_bundle_loads(self):
        bundle = load_bundle(self.root)
        self.assertEqual(len(bundle.tasks), 3)

    def test_bad_header_rejected(self):
        path = self.root / "task.csv"
        path.write_text("wrong,header\n1,2\n", encoding="utf-8")
        with self.assertRaises(BundleError):
            load_bundle(self.root)

    def test_score_out_of_range_rejected(self):
        rows = list(csv.reader((self.root / "answer.csv").open(encoding="utf-8")))
        body = [rows[1][:]]
        body[0][4] = "12.5"  # 超出 0-10
        self._rewrite("answer.csv", body)
        with self.assertRaises(BundleError):
            load_bundle(self.root)

    def test_valid_answer_without_score_rejected(self):
        rows = list(csv.reader((self.root / "answer.csv").open(encoding="utf-8")))
        body = [rows[1][:]]
        body[0][4] = ""  # valid 状态却缺分数
        self._rewrite("answer.csv", body)
        with self.assertRaises(BundleError):
            load_bundle(self.root)

    def test_unknown_answer_status_rejected(self):
        rows = list(csv.reader((self.root / "answer.csv").open(encoding="utf-8")))
        body = [r[:] for r in rows[1:]]
        body[0][6] = "mystery_state"
        self._rewrite("answer.csv", body)
        with self.assertRaises(BundleError):
            load_bundle(self.root)

    def test_dangling_reference_rejected(self):
        rows = list(csv.reader((self.root / "answer.csv").open(encoding="utf-8")))
        body = [r[:] for r in rows[1:]]
        body[0][0] = "GHOST"  # 名册中不存在的学生
        self._rewrite("answer.csv", body)
        with self.assertRaises(BundleError):
            load_bundle(self.root)

    def test_duplicate_valid_rows_rejected(self):
        rows = list(csv.reader((self.root / "answer.csv").open(encoding="utf-8")))
        body = [r[:] for r in rows[1:]]
        body.append(body[0][:])  # 同键第二件且未标 duplicate
        self._rewrite("answer.csv", body)
        with self.assertRaises(BundleError):
            load_bundle(self.root)


if __name__ == "__main__":
    unittest.main()
