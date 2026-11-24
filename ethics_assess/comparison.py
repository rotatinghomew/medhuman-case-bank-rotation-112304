"""分层比较与效应估计。

口径要点：
- 只在"独立证据仍够"的题层上估计：共同量表（common）+ 该生该场干净档
  （confirmed 接触剔除，unknown 接触进敏感性分析而非主分析）。
- old_score 题与 common 题永不合并；评分版次变化单列，不能用一个校正总分掩盖。
- 每个支持层单独报告样本量、点估计、置信区间、效应量与未识别差异；
  样本不足的层标记 not_estimable，而不是借用别层结果。
- 练习效应（同源材料带来的提高）用差分中的差分（DiD）识别：
  干净题为对照、确认接触题为处理，处理组与对照组做双重差分。
- 届间差异只用两组都干净的共同题，避免把练习效应误算成群组差异。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from .attendance import QualityLedger
from .bundle import Bundle, SKILLS
from .exposure import ExposureProfile, CONFIRMED, UNKNOWN, CLEAN

MIN_SAMPLE = 2
Z95 = stats.norm.ppf(0.975)


@dataclass
class Estimate:
    n: int
    mean: float | None
    se: float | None
    ci95: tuple[float | None, float | None]
    effect: str          # paired_gain / welch_diff / did
    cohens_d: float | None
    p_value: float | None
    note: str = ""


@dataclass
class StratumReport:
    """一个可支持层（题类 × 分项 × 接触档）的完整证据说明。"""

    label: str
    category: str
    skill: str
    exposure_bucket: str
    tasks: list[str]
    scale: str
    estimate: Estimate
    unknown_bucket_note: dict[str, int] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)


def _mean_ci(values: list[float]) -> tuple[float | None, float | None, tuple[float | None, float | None]]:
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    if len(arr) <= 1:
        return mean, None, (None, None)
    se = float(arr.std(ddof=1) / np.sqrt(len(arr)))
    ci = (mean - Z95 * se, mean + Z95 * se)
    return mean, se, ci


def _paired(pre: list[float], post: list[float]) -> Estimate:
    if not pre or not post:
        return Estimate(len(pre), None, None, (None, None), "paired_gain",
                        None, None, "该层无可用学生分，不可估计")
    pre_arr, post_arr = np.asarray(pre), np.asarray(post)
    if len(pre_arr) < MIN_SAMPLE or len(post_arr) < MIN_SAMPLE:
        return Estimate(len(pre_arr), None, None, (None, None), "paired_gain",
                        None, None, "配对样本不足或该层无共同题，不可估计")
    diff = post_arr - pre_arr
    n = len(diff)
    mean, se, ci = _mean_ci(diff.tolist())
    sd = float(diff.std(ddof=1))
    if sd == 0:
        return Estimate(n, mean, 0.0, (mean, mean), "paired_gain", 0.0, None,
                        "差值零方差：点估计确定，无法计算 p 值")
    t, p = stats.ttest_rel(post_arr, pre_arr)
    d = float(diff.mean() / sd)
    return Estimate(n, mean, se, ci, "paired_gain", d, float(p))


def _welch(a: list[float], b: list[float]) -> Estimate:
    a_arr, b_arr = np.asarray(a), np.asarray(b)
    if len(a_arr) < MIN_SAMPLE or len(b_arr) < MIN_SAMPLE:
        return Estimate(len(a_arr) + len(b_arr), None, None, (None, None),
                        "welch_diff", None, None,
                        "某一届样本不足或该层无共同干净题，不可估计")
    mean = float(b_arr.mean() - a_arr.mean())
    va, vb = a_arr.var(ddof=1), b_arr.var(ddof=1)
    se = float(np.sqrt(va / len(a_arr) + vb / len(b_arr)))
    ci = (mean - Z95 * se, mean + Z95 * se)
    pooled_sd = float(np.sqrt(((len(a_arr) - 1) * va + (len(b_arr) - 1) * vb)
                              / (len(a_arr) + len(b_arr) - 2)))
    if pooled_sd == 0:
        return Estimate(len(a_arr) + len(b_arr), mean, se, ci, "welch_diff",
                        0.0, None, "两组零方差：无法计算 p 值")
    res = stats.ttest_ind(b_arr, a_arr, equal_var=False)
    d = mean / pooled_sd
    return Estimate(len(a_arr) + len(b_arr), mean, se, ci, "welch_diff", d, float(res.pvalue))


def _student_stratum_score(
    bundle: Bundle,
    ledger: QualityLedger,
    student_id: str,
    form: str,
    tasks: list[str],
    skill: str,
    profile: ExposureProfile,
    bucket: str,
) -> float | None:
    """该生在某层（指定题集×分项×接触档）上的题均分；任一题缺失则该层不可用。"""
    if not tasks:
        return None
    values = []
    for task in tasks:
        if profile.classification.get(task) != bucket:
            return None
        answer = ledger.retained.get((student_id, form, task, skill))
        if answer is None or answer.score is None:
            return None
        values.append(answer.score)
    return float(np.mean(values))


def eligible_tasks(
    bundle: Bundle, form: str, profiles: dict[str, ExposureProfile],
    bucket: str, scale: str = "common",
) -> list[str]:
    """该场次中所有应考学生在指定接触档与量表下一致可用的题。"""
    form_tasks = [u.task for u in bundle.form_uses()[form]]
    out = []
    for task in form_tasks:
        task_obj = bundle.tasks[task]
        if task_obj.scale != scale:
            continue
        if all(p.classification[task] == bucket for p in profiles.values()):
            out.append(task)
    return sorted(out)


def within_term_gains(
    bundle: Bundle,
    ledger: QualityLedger,
    pre_form: str,
    post_form: str,
    pre_profiles: dict[str, ExposureProfile],
    post_profiles: dict[str, ExposureProfile],
    paired: set[str],
) -> list[StratumReport]:
    """本届前后变化：按题类×分项×接触档分别做配对增益。"""
    reports = []
    pre_tasks = {u.task for u in bundle.form_uses()[pre_form]}
    post_tasks = {u.task for u in bundle.form_uses()[post_form]}
    shared = sorted(pre_tasks & post_tasks)
    categories = sorted({bundle.tasks[t].category for t in shared})
    for bucket, bucket_name in ((CLEAN, "干净题（独立证据）"),
                                (CONFIRMED, "确认接触题（练习效应候选）"),
                                (UNKNOWN, "未知接触题（敏感性分析）")):
        for category in categories:
            tasks = [t for t in shared
                     if bundle.tasks[t].category == category
                     and bundle.tasks[t].scale == "common"
                     and all(pre_profiles[s].classification[t] == bucket
                             and post_profiles[s].classification[t] == bucket
                             for s in paired)]
            for skill in SKILLS:
                pre_vals, post_vals = [], []
                unknown_counts: dict[str, int] = {}
                for sid in sorted(paired):
                    x = _student_stratum_score(
                        bundle, ledger, sid, pre_form, tasks, skill,
                        pre_profiles[sid], bucket)
                    y = _student_stratum_score(
                        bundle, ledger, sid, post_form, tasks, skill,
                        post_profiles[sid], bucket)
                    if x is not None and y is not None:
                        pre_vals.append(x)
                        post_vals.append(y)
                limitations = []
                if not tasks:
                    limitations.append("该层无前后共同题，无法估计")
                if bucket == UNKNOWN:
                    limitations.append("未知送达材料：主分析剔除，本结果仅作敏感性边界")
                if tasks and not pre_vals:
                    limitations.append("该层学生均缺有效分，无法估计")
                estimate = _paired(pre_vals, post_vals)
                reports.append(StratumReport(
                    label=f"{category}/{skill}/{bucket_name}",
                    category=category, skill=skill, exposure_bucket=bucket,
                    tasks=tasks, scale="common", estimate=estimate,
                    unknown_bucket_note=unknown_counts, limitations=limitations,
                ))
    return reports


def between_terms(
    bundle: Bundle,
    ledger: QualityLedger,
    form_a: str,
    form_b: str,
    profiles_a: dict[str, ExposureProfile],
    profiles_b: dict[str, ExposureProfile],
    students_a: set[str],
    students_b: set[str],
) -> list[StratumReport]:
    """届间差异：只取两场都干净的 common 题，按题类×分项做 Welch 比较。"""
    tasks_a = {u.task for u in bundle.form_uses()[form_a]}
    tasks_b = {u.task for u in bundle.form_uses()[form_b]}
    shared = sorted(tasks_a & tasks_b)
    reports = []
    categories = sorted({bundle.tasks[t].category for t in shared})
    for category in categories:
        tasks = [t for t in shared
                 if bundle.tasks[t].category == category
                 and bundle.tasks[t].scale == "common"
                 and all(profiles_a[s].classification[t] == CLEAN for s in students_a)
                 and all(profiles_b[s].classification[t] == CLEAN for s in students_b)]
        for skill in SKILLS:
            vals_a, vals_b = [], []
            for sid, vals, form, profiles in (
                (students_a, vals_a, form_a, profiles_a),
                (students_b, vals_b, form_b, profiles_b),
            ):
                for student_id in sorted(sid):
                    score = _student_stratum_score(
                        bundle, ledger, student_id, form, tasks, skill,
                        profiles[student_id], CLEAN)
                    if score is not None:
                        vals.append(score)
            limitations = []
            if not tasks:
                limitations.append("两届在该题类没有共同的干净题，届间差异不可识别")
            reports.append(StratumReport(
                label=f"{category}/{skill}/届间干净题",
                category=category, skill=skill, exposure_bucket=CLEAN,
                tasks=tasks, scale="common",
                estimate=_welch(vals_a, vals_b), limitations=limitations,
            ))
    return reports


def practice_effect_did(
    bundle: Bundle,
    ledger: QualityLedger,
    pre_form: str,
    post_form: str,
    pre_profiles: dict[str, ExposureProfile],
    post_profiles: dict[str, ExposureProfile],
    paired: set[str],
    treated: set[str] | None = None,
) -> dict:
    """差分中的差分：接触组相对对照组，在接触题相对干净题上的额外增益。

    处理组由数据逐题自动定义（也可用 ``treated`` 覆盖）：该题后测确认接触、
    本人前测干净的学生；对照组为该题前后测均干净的学生。当某班全员接触时，
    对照组自然来自同届其他班，因此本函数按传入的全体配对学生识别，
    不假设处理/对照必须同处一班。
    返回每个分项的 DiD 估计与四格均值；识别假设（平行趋势、无溢出）
    作为限制条件随结果一并输出，不把假设当结论。
    """
    pre_tasks = {u.task for u in bundle.form_uses()[pre_form]}
    post_tasks = {u.task for u in bundle.form_uses()[post_form]}
    shared = sorted(pre_tasks & post_tasks)

    # 学生 -> 本人前/后测画像（届级分析时画像按场次合并传入，键为学生）
    def pre_of(sid: str) -> ExposureProfile:
        return pre_profiles[sid]

    def post_of(sid: str) -> ExposureProfile:
        return post_profiles[sid]

    # 后测有人确认接触的 common 共同题；处理/对照组在题内按前测条件筛
    candidate_exposed = [
        t for t in shared if bundle.tasks[t].scale == "common"
        and any(post_of(s).classification[t] == CONFIRMED for s in paired)
    ]
    clean_tasks = sorted(
        t for t in shared if bundle.tasks[t].scale == "common"
        and all(pre_of(s).classification[t] == CLEAN
                and post_of(s).classification[t] == CLEAN for s in paired))
    out = {"treated_definition": "后测确认接触且本人前测干净",
           "clean_control_tasks": clean_tasks, "by_task": {}}

    for exposed_task in candidate_exposed:
        task_treated = {
            s for s in paired
            if post_of(s).classification[exposed_task] == CONFIRMED
            and pre_of(s).classification[exposed_task] == CLEAN}
        task_control = {
            s for s in paired - task_treated
            if post_of(s).classification[exposed_task] == CLEAN
            and pre_of(s).classification[exposed_task] == CLEAN}
        if treated is not None:  # 显式覆盖时尊重外部处理组定义
            task_treated = treated & task_treated
            task_control = task_control - treated
        skill_out = {}
        for skill in SKILLS:
            # 每格存"学生层面的后测-前测增益"
            gains: dict[tuple[str, str], list[float]] = {
                ("treated", "exposed"): [], ("treated", "clean"): [],
                ("control", "exposed"): [], ("control", "clean"): [],
            }
            cell_info: dict[tuple[str, str], dict] = {}
            for group, members in (("treated", task_treated), ("control", task_control)):
                for bucket_name, taskset in (
                    ("exposed", [exposed_task]), ("clean", clean_tasks),
                ):
                    # 处理组在接触题上确认接触；对照组在同一题上是干净档
                    bucket = (CONFIRMED if group == "treated" and bucket_name == "exposed"
                              else CLEAN)
                    pre_vals, post_vals = [], []
                    for sid in members:
                        x = _student_stratum_score(
                            bundle, ledger, sid, pre_of(sid).form,
                            taskset, skill, pre_of(sid), CLEAN)
                        y = _student_stratum_score(
                            bundle, ledger, sid, post_of(sid).form,
                            taskset, skill, post_of(sid), bucket)
                        if x is not None and y is not None:
                            pre_vals.append(x)
                            post_vals.append(y)
                            gains[(group, bucket_name)].append(y - x)
                    cell_gains = gains[(group, bucket_name)]
                    cell_info[(group, bucket_name)] = {
                        "pre_mean": float(np.mean(pre_vals)) if pre_vals else None,
                        "post_mean": float(np.mean(post_vals)) if post_vals else None,
                        "gain_mean": float(np.mean(cell_gains)) if cell_gains else None,
                        "n": len(cell_gains),
                    }
            gain_means = {k: v["gain_mean"] for k, v in cell_info.items()}
            did = se = None
            ci = (None, None)
            if all(v is not None for v in gain_means.values()):
                treated_delta = (gain_means[("treated", "exposed")]
                                 - gain_means[("treated", "clean")])
                control_delta = (gain_means[("control", "exposed")]
                                 - gain_means[("control", "clean")])
                did = treated_delta - control_delta
                se_parts = []
                for group in ("treated", "control"):
                    ge = np.asarray(gains[(group, "exposed")], dtype=float)
                    gc = np.asarray(gains[(group, "clean")], dtype=float)
                    n = min(len(ge), len(gc))
                    if n >= MIN_SAMPLE:
                        delta = ge[:n] - gc[:n]
                        se_parts.append(float(delta.var(ddof=1) / n))
                if len(se_parts) == 2:
                    se = float(np.sqrt(sum(se_parts)))
                    ci = (did - Z95 * se, did + Z95 * se)
            skill_out[skill] = {
                "treated_n": len(task_treated),
                "control_n": len(task_control),
                "cells": {f"{g}/{b}": v for (g, b), v in cell_info.items()},
                "did_point": did,
                "did_se": se,
                "did_ci95": ci,
                "assumptions": [
                    "接触班与对照班在无材料反事实上增益平行（平行趋势）",
                    "讲评材料未在班组间溢出（对照组未接触同源情境）",
                    "接触题与干净题的评分版次一致（均为 common）",
                    "跨班对照：班组背景差异未另作调整，残余差异见限制说明",
                ],
            }
        out["by_task"][exposed_task] = skill_out
    return out


def rubric_version_split(bundle: Bundle, form: str) -> dict[str, list[str]]:
    """列出一场考试中两种评分版次的题，供报告显式分版呈现。"""
    out = {"common": [], "old_score": []}
    for use in bundle.form_uses()[form]:
        out[bundle.tasks[use.task].scale].append(use.task)
    return {k: sorted(v) for k, v in out.items()}
