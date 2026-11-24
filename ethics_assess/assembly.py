"""下一轮题组的组卷、合法性校验与方案评估。

机会可比的硬约束（来自资料口径与需求）：
1. 情境类别覆盖不低于 next_form.min_categories，总时长不超过 minutes。
2. 锚题只能取 anchor.csv 登记的 common 题；其历史独立使用场次须达到
   min_independent_uses；同轮出现的题组数不超过 max_forms；
   每套锚题数不超过 anchor_capacity。
3. 非锚题即使共享同源情境，也不能在同轮两套卷里同时充当"独立场景"：
   同 root 在同轮至多被一个题组用作非锚独立题（独立场景预算）。
4. 试讲（sealed 记录中的 rehearsed）即消耗该题对其他班的机会，
   按已占用处理；已 used 的题不再进入新卷。
5. 开封时受众中若有人确认/未知接触过某题，该题不得作为该卷锚题或独立题。
6. 批改安排：题组分项工作量必须能被 graders 在其可用日容量内覆盖。
7. 跨组链接只有合法共同锚题支持；没有共同锚题的两套卷不共享可比尺度。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from .bundle import Bundle, NextForm, SKILLS
from .exposure import build_profile, CONFIRMED, UNKNOWN, CLEAN


@dataclass
class Violation:
    code: str
    detail: str


@dataclass
class FormPlan:
    form: str
    spec: NextForm
    audience_students: list[str]
    anchors: list[str]
    independent: list[str]
    violations: list[Violation] = field(default_factory=list)

    @property
    def tasks(self) -> list[str]:
        return self.anchors + self.independent

    def is_legal(self) -> bool:
        return not self.violations


@dataclass
class PlanSet:
    plans: dict[str, FormPlan]
    cross_form_links: dict[str, list[str]]   # 题组对 -> 可合法链接的共同锚题
    set_violations: list[Violation]

    def bank_consumption(self, bundle: Bundle) -> dict:
        used = {t for p in self.plans.values() for t in p.tasks if t in bundle.tasks}
        anchor_load: dict[str, int] = {}
        for plan in self.plans.values():
            for task_id in plan.anchors:
                anchor_load[task_id] = anchor_load.get(task_id, 0) + 1
        return {
            "distinct_tasks": sorted(used),
            "distinct_roots": sorted({bundle.tasks[t].root for t in used}),
            "anchor_load": dict(sorted(anchor_load.items())),
            "n_tasks": len(used),
        }

    def contingent_loss(self, candidate_overlaps: list[tuple[str, str]]) -> dict:
        """若候选重合被确认，当前方案可能失去的覆盖（候选边→受影响题组）。"""
        task_owner: dict[str, list[str]] = {}
        for form, plan in self.plans.items():
            for task_id in plan.tasks:
                task_owner.setdefault(task_id, []).append(form)
        loss: dict[str, list[str]] = {}
        for a, b in candidate_overlaps:
            hit: set[str] = set()
            for task_id in (a, b):
                hit.update(task_owner.get(task_id, ()))
            if hit:
                loss[f"{a}~{b}"] = sorted(hit)
        return loss

    def resolution_score(self, question_strata: dict[str, set[str]]) -> dict:
        """方案对现有疑问的解决程度。

        question_strata：疑问 id -> 回答它所需的支持层（题类/分项/锚题键）。
        一个疑问被"解决"当且仅当某套合法卷覆盖其全部所需层。
        """
        covered_layers: set[str] = set()
        for plan in self.plans.values():
            if plan.is_legal():
                covered_layers.update(plan.tasks)
        per_question = {}
        for question, layers in question_strata.items():
            per_question[question] = sorted(layers & covered_layers)
        solved = sum(1 for layers in question_strata.values()
                     if layers and layers <= covered_layers)
        return {
            "questions": list(question_strata),
            "solved": solved,
            "total": len(question_strata),
            "covered_per_question": per_question,
        }


def audience_for(bundle: Bundle, spec: NextForm, audience_map: dict[str, list[str]]) -> list[str]:
    """题组受众名册：由教务配置的 audience_map 给出，避免引擎臆测班级映射。"""
    if spec.form not in audience_map:
        raise KeyError(f"题组 {spec.form} 的受众未在 audience_map 配置")
    ids = audience_map[spec.form]
    unknown = [s for s in ids if s not in bundle.students]
    if unknown:
        raise KeyError(f"题组 {spec.form} 受众含无名册学生：{unknown}")
    return ids


def check_form(
    bundle: Bundle,
    spec: NextForm,
    anchors: list[str],
    independent: list[str],
    audience_students: list[str],
    round_task_roots: dict[str, str],
    round_anchor_forms: dict[str, set[str]],
    audience_flags: dict[str, dict[str, str]],
) -> FormPlan:
    """对单套候选题组做全部硬约束检查，返回带违规清单的方案（不抛异常）。

    audience_flags：预计算的 {学生: {题: 接触档位}}，按该卷开封时点口径。
    """
    plan = FormPlan(spec.form, spec, audience_students,
                    sorted(anchors), sorted(independent))
    tasks = anchors + independent
    if len(tasks) != len(set(tasks)):
        plan.violations.append(Violation("duplicate_task", "题组内题目重复"))

    unknown_tasks = [t for t in tasks if t not in bundle.tasks]
    for task_id in unknown_tasks:
        plan.violations.append(Violation("unknown_task", f"{task_id} 不在题库"))
    known = [t for t in tasks if t in bundle.tasks]

    total_minutes = sum(bundle.tasks[t].minutes for t in known)
    if total_minutes > spec.minutes:
        plan.violations.append(Violation(
            "minutes_exceeded", f"总时长 {total_minutes} > {spec.minutes}"))
    categories = {bundle.tasks[t].category for t in known}
    if len(categories) < spec.min_categories:
        plan.violations.append(Violation(
            "category_coverage", f"题类数 {len(categories)} < 最少 {spec.min_categories}"))

    # ---- 锚题合法性 ----
    if len(anchors) > spec.anchor_capacity:
        plan.violations.append(Violation(
            "anchor_capacity", f"锚题数 {len(anchors)} > 容量 {spec.anchor_capacity}"))
    anchor_codes = set()
    for task_id in anchors:
        rule = bundle.anchor_rules.get(task_id)
        if rule is None:
            plan.violations.append(Violation("anchor_not_registered", f"{task_id} 非登记锚题"))
            continue
        anchor_codes.add(task_id)
        if bundle.tasks[task_id].scale != "common":
            plan.violations.append(Violation("anchor_scale", f"{task_id} 非共同量表"))
        n_uses = len(bundle.independent_uses(task_id))
        if n_uses < rule.min_independent_uses:
            plan.violations.append(Violation(
                "anchor_history", f"{task_id} 独立使用 {n_uses} < {rule.min_independent_uses}"))
        forms_using = round_anchor_forms.get(task_id, set()) | {spec.form}
        if len(forms_using) > rule.max_forms:
            plan.violations.append(Violation(
                "anchor_max_forms", f"{task_id} 同轮 {len(forms_using)} 套 > {rule.max_forms}"))

    # ---- 受众接触（按题组开封时点的当时口径）----
    for task_id in known:
        flags = {audience_flags[sid].get(task_id, CLEAN) for sid in audience_students}
        if CONFIRMED in flags:
            plan.violations.append(Violation(
                "exposed_audience", f"{task_id} 对受众存在确认接触，不能入卷"))
        elif UNKNOWN in flags:
            plan.violations.append(Violation(
                "unknown_receipt_audience",
                f"{task_id} 对受众存在送达未知，须先澄清或换题"))

    # ---- 同轮独立场景预算：同 root 的非锚题至多在一个题组出现 ----
    for task_id in independent:
        if task_id not in bundle.tasks:
            continue
        root = bundle.tasks[task_id].root
        owner = round_task_roots.get(root)
        if owner is not None and owner != spec.form:
            plan.violations.append(Violation(
                "shared_root_independent",
                f"root {root} 的独立场景已被 {owner} 占用，{task_id} 不能再作独立题"))

    # ---- 已 used / 已 rehearsed 的题机会已消耗 ----
    consumed = {s.task for s in bundle.seals if s.status in {"used", "rehearsed"}}
    for task_id in known:
        if task_id in consumed:
            plan.violations.append(Violation(
                "opportunity_consumed", f"{task_id} 已使用或试讲，机会已消耗"))

    # ---- 批改容量：每题每分项一件，grader 当日容量为可批改件数 ----
    for skill in SKILLS:
        load = len(known)
        capacity = 0
        for slot in bundle.grader_slots:
            if (slot.grader in spec.graders
                    and skill in slot.skills
                    and slot.available_day == spec.opens.date().isoformat()):
                capacity += slot.capacity
        if capacity < load:
            plan.violations.append(Violation(
                "grader_capacity",
                f"{spec.opens.date()} 分项 {skill} 容量 {capacity} < 工作量 {load}"))
    return plan


def evaluate_plan_set(
    bundle: Bundle,
    assignments: dict[str, tuple[list[str], list[str]]],
    audience_map: dict[str, list[str]],
    question_strata: dict[str, set[str]] | None = None,
) -> PlanSet:
    """组装并整体评估一轮方案：逐卷合法性 + 同轮预算 + 跨卷链接。"""
    round_roots: dict[str, str] = {}
    round_anchors: dict[str, set[str]] = {}
    plans: dict[str, FormPlan] = {}
    set_violations: list[Violation] = []

    for form in sorted(assignments):
        spec = bundle.next_forms[form]
        anchors, independent = assignments[form]
        audience = audience_for(bundle, spec, audience_map)
        # 按该卷开封时点预计算受众接触画像
        audience_flags = {
            sid: build_profile(
                bundle, sid, spec.opens, spec.opens, form=form
            ).classification
            for sid in audience
        }
        plan = check_form(bundle, spec, anchors, independent, audience,
                          round_roots, round_anchors, audience_flags)
        plans[form] = plan
        for task_id in independent:
            if task_id in bundle.tasks:
                round_roots.setdefault(bundle.tasks[task_id].root, form)
        for task_id in anchors:
            round_anchors.setdefault(task_id, set()).add(form)

    # 跨卷链接：共同合法锚题才支持同尺度
    links: dict[str, list[str]] = {}
    illegal_anchor_forms = {
        form: {v.detail.split()[0] for v in plan.violations
               if v.code in {"anchor_not_registered", "anchor_history",
                             "anchor_max_forms", "anchor_scale", "exposed_audience"}}
        for form, plan in plans.items()
    }
    for f1, f2 in combinations(sorted(plans), 2):
        common = sorted(set(plans[f1].anchors) & set(plans[f2].anchors))
        legal = [t for t in common
                 if t not in illegal_anchor_forms[f1]
                 and t not in illegal_anchor_forms[f2]]
        links[f"{f1}|{f2}"] = legal
        if not legal:
            set_violations.append(Violation(
                "no_shared_scale", f"{f1} 与 {f2} 无共同合法锚题，不共享可比尺度"))

    plan_set = PlanSet(plans, links, set_violations)
    if question_strata:
        plan_set.resolution_score(question_strata)
    return plan_set
