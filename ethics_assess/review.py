"""出件后的证据复查与替代题组口径。

触发规则：题组实际出件（seal 状态进入 used/released）之后，
新到达的材料重合证据（overlap 收件时间晚于出件时间）触发复查。

复查结论按题组状态分流：
- opened/used：成绩不溯及作废，但披露证据到达后哪些说法仍成立、
  哪些不再成立；历史公示依据（当时口径）保留。
- sealed：开封前必须完成复查，受污染题更换或整卷撤回。
- draft：直接修订，不留历史口径负担。

替代题组上线时校方需要两份并行说明：
1. 旧成绩公示：按出件当时适用的证据与评分版次，说明当时依据仍然有效；
2. 当前可比性说明：替代卷与旧卷之间哪些层可链接、哪些差异未识别。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from .assembly import FormPlan
from .bundle import Bundle
from .exposure import build_profile, CONFIRMED, UNKNOWN, CLEAN


@dataclass
class ReviewFinding:
    form: str
    form_state: str
    task: str
    old_bucket: str          # 证据到达前（当时口径）的接触档
    new_bucket: str          # 纳入新证据后的接触档
    evidence: str
    action_required: str
    score_claim_still_valid: bool
    affected_students: list[str] = field(default_factory=list)


@dataclass
class ReviewReport:
    as_of: datetime
    findings: list[ReviewFinding] = field(default_factory=list)
    opened_claims: dict[str, list[str]] = field(default_factory=dict)
    sealed_actions: dict[str, list[str]] = field(default_factory=dict)

    def contaminated_forms(self) -> set[str]:
        return {f.form for f in self.findings
                if f.new_bucket in {CONFIRMED, UNKNOWN} and f.old_bucket == CLEAN}


def _augment_overlaps(
    bundle: Bundle,
    new_edges: list[tuple[str, str, str, datetime]],
    as_of: datetime,
) -> Bundle:
    """返回含增补重合边的副本（内存对象，不触碰原始资料）。"""
    extra = [type(bundle.overlaps[0])(a, b, status, received)
             for a, b, status, received in new_edges if received <= as_of]
    return replace(bundle, overlaps=[*bundle.overlaps, *extra])


def review_with_overlap(
    bundle: Bundle,
    new_edges: list[tuple[str, str, str, datetime]],
    as_of: datetime,
    audience_map: dict[str, list[str]],
    extra_form_tasks: dict[str, list[str]] | None = None,
) -> ReviewReport:
    """对纳入新重合边后的全部题组做复查。

    new_edges：(题a, 题b, confirmed/candidate, 收件时间)。
    复查同时比较"题组开封当时口径"与"as_of 口径"，差异即复查发现。
    检查范围包括 seal.csv 已记录的题，以及 extra_form_tasks 中
    替代/候选题组拟用但尚未封存的题。
    """
    report = ReviewReport(as_of=as_of)
    augmented = _augment_overlaps(bundle, new_edges, as_of)

    form_tasks: dict[str, set[str]] = {}
    for seal in augmented.seals:
        form_tasks.setdefault(seal.form, set()).add(seal.task)
    for form, tasks in (extra_form_tasks or {}).items():
        form_tasks.setdefault(form, set()).update(tasks)

    for form in sorted(form_tasks):
        if form not in augmented.next_forms:
            raise KeyError(f"复查题组 {form} 未在 next_form 登记")
        spec = augmented.next_forms[form]
        # 复查面向题组配置受众；草稿/封存卷可能尚无 seal 受众记录，回退到配置名册
        audience = audience_map.get(form, [])
        if not audience:
            continue
        unknown_audience = [s for s in audience if s not in augmented.students]
        if unknown_audience:
            raise KeyError(f"题组 {form} 受众含无名册学生：{unknown_audience}")
        for task_id in sorted(form_tasks[form]):
            if task_id not in augmented.tasks:
                raise KeyError(f"复查题 {task_id} 不在题库")
            # 按 (题, 旧档, 新档) 聚合受影响学生，避免逐人重复同一条发现
            buckets: dict[tuple[str, str], list[str]] = {}
            for sid in audience:
                old = build_profile(augmented, sid, spec.opens, spec.opens)
                new = build_profile(augmented, sid, spec.opens, as_of)
                old_flag = old.classification.get(task_id, CLEAN)
                new_flag = new.classification.get(task_id, CLEAN)
                if old_flag != new_flag:
                    buckets.setdefault((old_flag, new_flag), []).append(sid)
            for (old_flag, new_flag), students in sorted(buckets.items()):
                direct = [f"{a}~{b}" for a, b, _, _ in new_edges
                          if task_id in (a, b)]
                edge_ref = ",".join(direct) if direct else "新证据闭包传播"
                if spec.state in {"opened", "used"}:
                    action = "成绩保留；该题相关比较结论降级为受限说法"
                    still_valid = new_flag != CONFIRMED
                elif spec.state == "sealed":
                    action = "开封前撤换该题或整卷撤回，否则不得开封"
                    still_valid = False
                else:
                    action = "草稿阶段直接修订并记录修订原因"
                    still_valid = False
                report.findings.append(ReviewFinding(
                    form=form, form_state=spec.state, task=task_id,
                    old_bucket=old_flag, new_bucket=new_flag,
                    evidence=edge_ref, action_required=action,
                    score_claim_still_valid=still_valid,
                    affected_students=sorted(students),
                ))

    # 已开考成绩仍能支持的说法；封存卷的处置动作单独汇总
    for finding in report.findings:
        if finding.form_state in {"opened", "used"}:
            report.opened_claims.setdefault(finding.form, [])
            if finding.score_claim_still_valid:
                report.opened_claims[finding.form].append(
                    f"{finding.task}：分数与当时口径下的排名仍可公示，"
                    "但跨题组链接结论需加注未知接触")
            else:
                report.opened_claims[finding.form].append(
                    f"{finding.task}：分数本身有效（评分过程未变），"
                    "但不得再用于证明'陌生情境下的能力'，练习效应无法排除")
        elif finding.form_state == "sealed":
            report.sealed_actions.setdefault(finding.form, []).append(
                f"{finding.task}（{finding.evidence}）：{finding.action_required}")
    return report


def replacement_disclosure(
    bundle: Bundle,
    review: ReviewReport,
    replacements: dict[str, FormPlan],
) -> dict:
    """生成替代题组上线时的双轨公示文本依据。"""
    historic, current = [], []
    for form in sorted(replacements):
        plan = replacements[form]
        historic.append(
            f"【{form} 旧成绩公示依据】成绩按 {plan.spec.opens.date()} 开封当时"
            "适用的重合证据、收件范围与评分版次评定；当时未知的证据不溯及既往，"
            "原始评分与公示结论继续有效。")
        if plan.is_legal():
            current.append(
                f"【{form} 当前可比性】替代卷锚题 {plan.anchors} 经现时点证据复查"
                "为干净共同量表题，可支持与同轮合法卷的尺度链接；"
                "被替换题涉及的历史差异标记为未识别，不并入校正总分。")
        else:
            codes = sorted({v.code for v in plan.violations})
            current.append(
                f"【{form} 当前可比性】替代卷仍有未决违规 {codes}，"
                "在清除前不得宣称与旧卷同尺度。")
    return {"historic_basis": historic, "current_comparability": current}
