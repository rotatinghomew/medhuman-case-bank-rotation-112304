"""命令行入口：validate / exposure / compare / assemble / review。

所有输出为 UTF-8 JSON，数字保留 4 位小数；不产生写回资料包的副作用。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

from .assembly import evaluate_plan_set
from .attendance import build_ledger, paired_students
from .bundle import Bundle, load_bundle
from .comparison import (
    between_terms, practice_effect_did, rubric_version_split, within_term_gains,
)
from .exposure import CLEAN, CONFIRMED, UNKNOWN, exposure_summary, session_profiles
from .review import replacement_disclosure, review_with_overlap


def _default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"不可序列化的对象：{type(obj)}")


def _sanitize(node):
    """递归把非有限浮点转为 None，保证输出始终是合法 JSON。"""
    import math
    if isinstance(node, float):
        return node if math.isfinite(node) else None
    if isinstance(node, dict):
        return {k: _sanitize(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_sanitize(v) for v in node]
    return node


def dump(payload) -> str:
    return json.dumps(_sanitize(payload), ensure_ascii=False, indent=2,
                      default=_default, allow_nan=False)


def cmd_validate(bundle: Bundle, args) -> dict:
    return {
        "tables": {
            "tasks": len(bundle.tasks), "students": len(bundle.students),
            "uses": len(bundle.uses), "answers": len(bundle.answers),
            "issues": len(bundle.issues), "overlaps": len(bundle.overlaps),
            "next_forms": len(bundle.next_forms), "seals": len(bundle.seals),
            "anchor_rules": len(bundle.anchor_rules),
            "grader_slots": len(bundle.grader_slots),
        },
        "warnings": bundle.warnings,
        "status": "ok",
    }


def cmd_exposure(bundle: Bundle, args) -> dict:
    profiles = session_profiles(bundle, args.form)
    summary = exposure_summary(bundle, args.form)
    return {
        "form": args.form,
        "students": len(profiles),
        "task_buckets": summary,
        "bucket_legend": {CLEAN: "干净（独立证据）", CONFIRMED: "确认接触",
                          UNKNOWN: "未知接触（敏感性分析）"},
    }


def _term_forms(bundle: Bundle, term: str, stage: str) -> list[str]:
    return sorted({u.form for u in bundle.uses
                   if u.term == term and u.stage == stage})


def cmd_compare(bundle: Bundle, args) -> dict:
    ledger = build_ledger(bundle)
    pre_forms = _term_forms(bundle, args.term, "pre")
    post_forms = _term_forms(bundle, args.term, "post")
    # 前/后测画像分字典保存，避免同学生键互相覆盖
    pre_profiles, post_profiles = {}, {}
    for form in pre_forms:
        pre_profiles.update(session_profiles(bundle, form))
    for form in post_forms:
        post_profiles.update(session_profiles(bundle, form))

    # 逐班配对：各班的前后增益独立估计，不跨班凑样本
    gains, paired_all = [], set()
    for pre_form, post_form in zip(pre_forms, post_forms):
        paired = paired_students(bundle, pre_form, post_form, ledger)
        paired_all |= paired
        gains.extend(within_term_gains(
            bundle, ledger, pre_form, post_form,
            pre_profiles, post_profiles, paired))

    # DiD 在届级识别：处理班与对照班来自同届不同班，画像按学生本人场次取
    did = practice_effect_did(
        bundle, ledger, pre_forms[0], post_forms[0],
        pre_profiles, post_profiles, paired_all)
    did["form_pairs"] = [f"{a}->{b}" for a, b in zip(pre_forms, post_forms)]
    treated_total = sum(
        skill["treated_n"]
        for task in did["by_task"].values() for skill in task.values())

    output = {
        "term": args.term,
        "quality_ledger": ledger.counts(),
        "paired_students": len(paired_all),
        "treated_students": treated_total,
        "rubric_split": {f: rubric_version_split(bundle, f)
                         for f in [*pre_forms, *post_forms]},
        "within_term_strata": [asdict(r) for r in gains],
        "practice_effect_did": did,
    }

    if args.other_term:
        other_pre = _term_forms(bundle, args.other_term, "pre")
        other_post = _term_forms(bundle, args.other_term, "post")
        other_pre_profiles, other_post_profiles = {}, {}
        for form in other_pre:
            other_pre_profiles.update(session_profiles(bundle, form))
        for form in other_post:
            other_post_profiles.update(session_profiles(bundle, form))
        current_students = {
            s for s in bundle.students if bundle.students[s].term == args.term}
        other_students = {
            s for s in bundle.students if bundle.students[s].term == args.other_term}
        between = []
        for form_a, form_b in zip(other_post, post_forms):
            between.extend(between_terms(
                bundle, ledger, form_a, form_b,
                other_post_profiles, post_profiles, other_students, current_students))
        output["between_terms"] = [asdict(r) for r in between]
    return output


def _load_config(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def cmd_assemble(bundle: Bundle, args) -> dict:
    audience_map = _load_config(args.audience_map)
    assignments = _load_config(args.plan)
    question_strata_raw = _load_config(args.questions) if args.questions else {}
    question_strata = {k: set(v) for k, v in question_strata_raw.items()}
    plan_set = evaluate_plan_set(bundle, assignments, audience_map, question_strata)
    return {
        "forms": {
            form: {
                "anchors": plan.anchors,
                "independent": plan.independent,
                "legal": plan.is_legal(),
                "violations": [asdict(v) for v in plan.violations],
            }
            for form, plan in plan_set.plans.items()
        },
        "cross_form_links": plan_set.cross_form_links,
        "set_violations": [asdict(v) for v in plan_set.set_violations],
        "bank_consumption": plan_set.bank_consumption(bundle),
        "resolution": plan_set.resolution_score(question_strata) if question_strata else None,
        "contingent_loss": plan_set.contingent_loss([
            (o.a, o.b) for o in bundle.overlaps if o.status == "candidate"]),
    }


def cmd_review(bundle: Bundle, args) -> dict:
    audience_map = _load_config(args.audience_map)
    edges_raw = json.loads(Path(args.edges).read_text(encoding="utf-8"))
    edges_raw = [e for e in edges_raw if isinstance(e, dict) and "a" in e]
    from datetime import datetime as _dt
    edges = [(e["a"], e["b"], e["status"], _dt.fromisoformat(e["received"]))
             for e in edges_raw]
    as_of = _dt.fromisoformat(args.as_of)
    report = review_with_overlap(bundle, edges, as_of, audience_map)
    payload = {
        "as_of": report.as_of,
        "findings": [asdict(f) for f in report.findings],
        "opened_claims": report.opened_claims,
        "contaminated_forms": report.contaminated_forms(),
    }
    if args.replacements:
        assignments = _load_config(args.replacements)
        plan_set = evaluate_plan_set(bundle, assignments, audience_map)
        extra_tasks = {form: plan.tasks for form, plan in plan_set.plans.items()}
        # 用含替代卷拟用题的范围重新复查，确保新证据对未封存卷也触发检查
        report = review_with_overlap(bundle, edges, as_of, audience_map, extra_tasks)
        payload = {
            "as_of": report.as_of,
            "findings": [asdict(f) for f in report.findings],
            "opened_claims": report.opened_claims,
            "sealed_actions": report.sealed_actions,
            "contaminated_forms": report.contaminated_forms(),
        }
        payload["disclosure"] = replacement_disclosure(
            bundle, report, plan_set.plans)
        payload["replacement_legality"] = {
            form: {"legal": plan.is_legal(),
                   "violations": [asdict(v) for v in plan.violations]}
            for form, plan in plan_set.plans.items()
        }
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="伦理课程跨届测量与题组轮换工具")
    parser.add_argument("bundle", type=Path, help="资料交换包目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="装载并校验资料包")

    p_exp = sub.add_parser("exposure", help="输出某场次的材料接触分档")
    p_exp.add_argument("form")

    p_cmp = sub.add_parser("compare", help="分层前后变化、届间差异与练习效应")
    p_cmp.add_argument("term")
    p_cmp.add_argument("--other-term", default=None)

    p_asm = sub.add_parser("assemble", help="校验并评估一轮组卷方案")
    p_asm.add_argument("plan", help="组卷方案 JSON：{form: [anchors, independent]}")
    p_asm.add_argument("--audience-map", required=True)
    p_asm.add_argument("--questions", default=None,
                       help="疑问→所需支持层映射 JSON")

    p_rev = sub.add_parser("review", help="新重合证据触发的出件后复查")
    p_rev.add_argument("edges", help="新增重合边 JSON 列表")
    p_rev.add_argument("--as-of", required=True)
    p_rev.add_argument("--audience-map", required=True)
    p_rev.add_argument("--replacements", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        bundle = load_bundle(args.bundle)
        payload = {
            "validate": cmd_validate,
            "exposure": cmd_exposure,
            "compare": cmd_compare,
            "assemble": cmd_assemble,
            "review": cmd_review,
        }[args.command](bundle, args)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"运行失败：{exc}\n")
    print(dump(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
