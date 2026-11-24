"""生成教研组可读的中文分析报告（Markdown）。

把分散在 validate/exposure/compare/assemble/review 的机器可读结论，
按"先讲支持范围与缺口、再给数字"的顺序组织，避免用一个校正总分掩盖问题。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .assembly import evaluate_plan_set
from .attendance import build_ledger, paired_students
from .bundle import Bundle, load_bundle
from .comparison import (
    between_terms, practice_effect_did, rubric_version_split, within_term_gains,
)
from .exposure import CLEAN, CONFIRMED, UNKNOWN, session_profiles, exposure_summary
from .review import review_with_overlap, replacement_disclosure

BUCKET_CN = {
    CLEAN: "干净（独立证据）",
    CONFIRMED: "确认接触",
    UNKNOWN: "未知接触（仅敏感性分析）",
}


def _load_config(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _fmt(value, digits=3):
    if value is None:
        return "不可估计"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _term_forms(bundle: Bundle, term: str, stage: str) -> list[str]:
    return sorted({u.form for u in bundle.uses
                   if u.term == term and u.stage == stage})


def build_report(
    bundle: Bundle,
    term: str,
    other_term: str | None,
    audience_map: dict,
    actual_plan: dict | None,
    replacement_plan: dict | None,
    new_edges: list | None,
    review_as_of: str | None,
) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# 伦理课程测量与题组轮换分析报告（第 {term} 届）\n")
    add("> 本报告所有结论均标注支持范围；未识别差异不并入任何校正总分。\n")

    # ---------- 1. 数据质量 ----------
    ledger = build_ledger(bundle)
    add("## 一、数据质量与有效样本\n")
    add("| 情形 | 件数 | 对比较的影响 |")
    add("|---|---|---|")
    impact = {
        "duplicate_dropped": "重复件剔除，保留一件有效分，学生仍在样本内",
        "absent": "确认未参加，配对与届间比较均剔除，计入到场率分母",
        "overdue": "到期缺交，处理同缺考但单列，反映提交流程问题",
        "receipt_unknown": "收件未知，成绩按缺失处理并归入未知接触敏感性分析",
        "no_record": "整场无作答记录，按名册补记缺失",
    }
    for reason, count in ledger.counts().items():
        add(f"| {reason} | {count} | {impact[reason]} |")
    add("")

    # ---------- 2. 接触审计 ----------
    pre_forms = _term_forms(bundle, term, "pre")
    post_forms = _term_forms(bundle, term, "post")
    pre_profiles, post_profiles = {}, {}
    for form in pre_forms:
        pre_profiles.update(session_profiles(bundle, form))
    for form in post_forms:
        post_profiles.update(session_profiles(bundle, form))

    add("## 二、材料接触审计（按场次，区分三档）\n")
    for form in [*pre_forms, *post_forms]:
        summary = exposure_summary(bundle, form)
        add(f"### 场次 {form}")
        add("| 题 | 干净 | 确认接触 | 未知接触 |")
        add("|---|---|---|---|")
        for task_id, counts in sorted(summary.items()):
            add(f"| {task_id} | {counts[CLEAN]} | {counts[CONFIRMED]} "
                f"| {counts[UNKNOWN]} |")
        add("")

    # ---------- 3. 评分版次 ----------
    add("## 三、评分版次（共同量表与旧版分列，不合并）\n")
    for form in [*pre_forms, *post_forms]:
        split = rubric_version_split(bundle, form)
        add(f"- {form}：common 题 {split['common']}；old_score 题 {split['old_score']}"
            "（old_score 不参与跨届链接）")
    add("")

    # ---------- 4. 本届前后变化 ----------
    add("## 四、本届前后变化（仅独立证据足够的层）\n")
    paired_all: set[str] = set()
    gains = []
    for pre_form, post_form in zip(pre_forms, post_forms):
        paired = paired_students(bundle, pre_form, post_form, ledger)
        paired_all |= paired
        gains.extend(within_term_gains(
            bundle, ledger, pre_form, post_form,
            pre_profiles, post_profiles, paired))
    add(f"前后测均有有效作答的配对学生：{len(paired_all)} 人。\n")
    add("| 支持层 | 题 | 样本 | 平均增益 | 95% 置信区间 | 限制 |")
    add("|---|---|---|---|---|---|")
    seen = set()
    for report in gains:
        key = (report.label, tuple(report.tasks))
        if key in seen:
            continue
        seen.add(key)
        est = report.estimate
        ci = ("—" if est.ci95[0] is None
              else f"[{est.ci95[0]:.3f}, {est.ci95[1]:.3f}]")
        limit = "；".join(report.limitations) or est.note or ""
        add(f"| {report.label} | {report.tasks or '无'} | {est.n} "
            f"| {_fmt(est.mean)} | {ci} | {limit} |")
    add("")

    # ---------- 5. 练习效应 DiD ----------
    add("## 五、练习效应识别（差分中的差分）\n")
    did = practice_effect_did(
        bundle, ledger, pre_forms[0], post_forms[0],
        pre_profiles, post_profiles, paired_all)
    if not did["by_task"]:
        add("本届后测没有『前测干净、后测确认接触』的共同题，练习效应不可识别。\n")
    for task_id, skills in did["by_task"].items():
        add(f"### 接触题 {task_id}（对照题：{did['clean_control_tasks']}）")
        add("| 分项 | 处理组n | 对照组n | DiD 点估计 | 95% CI |")
        add("|---|---|---|---|---|")
        for skill, v in skills.items():
            ci = ("—" if v["did_ci95"][0] is None
                  else f"[{v['did_ci95'][0]:.3f}, {v['did_ci95'][1]:.3f}]")
            add(f"| {skill} | {v['treated_n']} | {v['control_n']} "
                f"| {_fmt(v['did_point'])} | {ci} |")
        add("\n识别假设（任一不成立则结论降级）：")
        for assumption in next(iter(skills.values()))["assumptions"]:
            add(f"- {assumption}")
        add("")

    # ---------- 6. 届间差异 ----------
    if other_term:
        add(f"## 六、届间差异（第 {other_term} 届 → 第 {term} 届，仅干净共同题）\n")
        other_post = _term_forms(bundle, other_term, "post")
        other_profiles = {}
        for form in other_post:
            other_profiles.update(session_profiles(bundle, form))
        current_students = {s for s in bundle.students
                            if bundle.students[s].term == term}
        other_students = {s for s in bundle.students
                          if bundle.students[s].term == other_term}
        add("| 支持层 | 共同干净题 | 总样本 | 差异(本届-对照届) | 限制 |")
        add("|---|---|---|---|---|")
        seen = set()
        for form_a, form_b in zip(other_post, post_forms):
            for report in between_terms(
                bundle, ledger, form_a, form_b,
                other_profiles, post_profiles, other_students, current_students):
                key = (report.label, tuple(report.tasks))
                if key in seen:
                    continue
                seen.add(key)
                est = report.estimate
                limit = "；".join(report.limitations) or est.note or ""
                add(f"| {report.label} | {report.tasks or '无'} | {est.n} "
                    f"| {_fmt(est.mean)} | {limit} |")
        add("")

    # ---------- 7. 组卷方案 ----------
    if actual_plan or replacement_plan:
        add("## 七、下一轮组卷方案评估\n")
        for name, assignments in (("实际封存方案", actual_plan),
                                  ("合规替代方案", replacement_plan)):
            if not assignments:
                continue
            plan_set = evaluate_plan_set(
                bundle, assignments, audience_map)
            add(f"### {name}")
            for form, plan in plan_set.plans.items():
                status = "合法" if plan.is_legal() else "不合规"
                add(f"- {form}（{status}）锚题 {plan.anchors}，独立题 {plan.independent}")
                for v in plan.violations:
                    add(f"    - 违规 [{v.code}] {v.detail}")
            add("- 跨组链接（仅共同合法锚题支持同尺度）：")
            for pair, anchors in plan_set.cross_form_links.items():
                add(f"    - {pair}: {anchors or '无共同尺度'}")
            consumption = plan_set.bank_consumption(bundle)
            add(f"- 题库消耗：{consumption['n_tasks']} 题，"
                f"覆盖 root {len(consumption['distinct_roots'])} 个，"
                f"锚题负载 {consumption['anchor_load']}")
            add("")

    # ---------- 8. 出件后复查 ----------
    if new_edges and review_as_of:
        from datetime import datetime as _dt
        edges = [(e["a"], e["b"], e["status"], _dt.fromisoformat(e["received"]))
                 for e in new_edges]
        extra = None
        if replacement_plan:
            ps = evaluate_plan_set(bundle, replacement_plan, audience_map)
            extra = {f: p.tasks for f, p in ps.plans.items()}
        review = review_with_overlap(
            bundle, edges, _dt.fromisoformat(review_as_of), audience_map, extra)
        add("## 八、新重合证据复查\n")
        if not review.findings:
            add("截至复查时点，未发现题组接触档位变化。\n")
        for finding in review.findings:
            add(f"- {finding.form}（{finding.form_state}）题 {finding.task}："
                f"{BUCKET_CN[finding.old_bucket]} → {BUCKET_CN[finding.new_bucket]}，"
                f"证据 {finding.evidence}，受影响 {len(finding.affected_students)} 人；"
                f"处置：{finding.action_required}")
        if review.opened_claims:
            add("\n已开考成绩仍可支持的说法：")
            for form, claims in review.opened_claims.items():
                for claim in dict.fromkeys(claims):
                    add(f"- {form}: {claim}")
        if replacement_plan:
            ps = evaluate_plan_set(bundle, replacement_plan, audience_map)
            disclosure = replacement_disclosure(bundle, review, ps.plans)
            add("\n### 双轨公示口径")
            for text in disclosure["historic_basis"]:
                add(f"- {text}")
            for text in disclosure["current_comparability"]:
                add(f"- {text}")
        add("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成教研组中文分析报告")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--term", default="3")
    parser.add_argument("--other-term", default="2")
    parser.add_argument("--audience-map", required=True)
    parser.add_argument("--actual-plan")
    parser.add_argument("--replacement-plan")
    parser.add_argument("--new-edges")
    parser.add_argument("--review-as-of")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    bundle = load_bundle(args.bundle)
    audience_map = _load_config(args.audience_map)
    actual = _load_config(args.actual_plan) if args.actual_plan else None
    replacement = _load_config(args.replacement_plan) if args.replacement_plan else None
    new_edges = None
    if args.new_edges:
        raw = json.loads(Path(args.new_edges).read_text(encoding="utf-8"))
        new_edges = [e for e in raw if isinstance(e, dict) and "a" in e]

    report = build_report(
        bundle, args.term, args.other_term, audience_map,
        actual, replacement, new_edges, args.review_as_of)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"报告已写入 {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
