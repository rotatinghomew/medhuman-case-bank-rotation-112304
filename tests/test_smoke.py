"""端到端冒烟测试：CLI 各子命令与报告生成在真实资料包上可运行且输出自洽。"""
import json
import unittest
from pathlib import Path

from ethics_assess.cli import build_parser
from ethics_assess.report import build_report, _load_config

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = str(ROOT / "data")
CONFIG = ROOT / "config"


class SmokeTest(unittest.TestCase):
    def _run(self, argv):
        parser = build_parser()
        args = parser.parse_args(argv)
        # 复用模块内部命令函数，避免捕获 stdout 的复杂性
        from ethics_assess.cli import (
            cmd_validate, cmd_exposure, cmd_compare, cmd_assemble, cmd_review,
        )
        from ethics_assess.bundle import load_bundle
        bundle = load_bundle(args.bundle)
        return {
            "validate": cmd_validate, "exposure": cmd_exposure,
            "compare": cmd_compare, "assemble": cmd_assemble,
            "review": cmd_review,
        }[args.command](bundle, args)

    def test_validate_reports_all_tables(self):
        payload = self._run([BUNDLE, "validate"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["tables"]["tasks"], 30)

    def test_exposure_buckets_sum_to_roster(self):
        payload = self._run([BUNDLE, "exposure", "F32-post"])
        for task_id, counts in payload["task_buckets"].items():
            self.assertEqual(sum(counts.values()), 18, task_id)

    def test_compare_contains_required_sections(self):
        payload = self._run([BUNDLE, "compare", "3", "--other-term", "2"])
        for key in ("quality_ledger", "within_term_strata",
                    "practice_effect_did", "between_terms", "rubric_split"):
            self.assertIn(key, payload)
        # DiD 应识别出 I04 接触题
        self.assertIn("I04", payload["practice_effect_did"]["by_task"])

    def test_assemble_flags_actual_plan_violations(self):
        payload = self._run([
            BUNDLE, "assemble", str(CONFIG / "plan_actual.json"),
            "--audience-map", str(CONFIG / "audience_map.json"),
            "--questions", str(CONFIG / "questions.json")])
        self.assertTrue(any(not f["legal"] for f in payload["forms"].values()))
        self.assertIn("bank_consumption", payload)

    def test_review_detects_post_opening_evidence(self):
        payload = self._run([
            BUNDLE, "review", str(CONFIG / "new_edges.json"),
            "--as-of", "2026-10-13T18:00:00+08:00",
            "--audience-map", str(CONFIG / "audience_map.json"),
            "--replacements", str(CONFIG / "plan_replacement.json")])
        self.assertIn("N1", payload["contaminated_forms"])
        self.assertIn("disclosure", payload)

    def test_report_is_markdown_with_all_sections(self):
        from ethics_assess.bundle import load_bundle
        bundle = load_bundle(BUNDLE)
        audience = _load_config(str(CONFIG / "audience_map.json"))
        actual = _load_config(str(CONFIG / "plan_actual.json"))
        replacement = _load_config(str(CONFIG / "plan_replacement.json"))
        edges = [e for e in json.loads(
            (CONFIG / "new_edges.json").read_text(encoding="utf-8"))
            if isinstance(e, dict) and "a" in e]
        report = build_report(
            bundle, "3", "2", audience, actual, replacement, edges,
            "2026-10-13T18:00:00+08:00")
        for section in ("数据质量", "材料接触审计", "评分版次", "本届前后变化",
                        "练习效应", "届间差异", "组卷方案", "复查"):
            self.assertIn(section, report)
        self.assertNotIn("nan", report)


if __name__ == "__main__":
    unittest.main()
