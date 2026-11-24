"""作答状态处理与有效比较样本派生。

四类非正常情形对比较的影响不同，绝不合并成一个"缺考"口径：

- ``duplicate``（重复作答）：同键多件，按保留规则取一件有效分，
  其余剔除并在数据质量台账留痕；学生仍属有效样本。
- ``absent``（确认未参加）：该场次无成绩，配对增益剔除该生，
  届间单阶段比较同样剔除，但计入到场率分母。
- ``overdue``（已到期缺交）：与 absent 同样无成绩，但区分报告，
  因为它反映的是提交流程而非到场，可能与班组/题组安排相关。
- ``receipt_unknown``（未知是否收到材料）：成绩处理同缺失，
  但在接触判定中对应"未知接触"档，需单独披露其对误差的贡献。

空白分数且状态为 valid 的资料包在装载阶段已被拒绝，
因此这里看到的缺失必然携带上述某个显式状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .bundle import Bundle, Answer, SKILLS

# 数据质量台账里的缺失原因编码
DUP_DROPPED = "duplicate_dropped"
ABSENT = "absent"
OVERDUE = "overdue"
RECEIPT_UNKNOWN = "receipt_unknown"
NO_RECORD = "no_record"          # 名册有学生、整场无任何作答行
MISSING_REASONS = frozenset({DUP_DROPPED, ABSENT, OVERDUE, RECEIPT_UNKNOWN, NO_RECORD})


@dataclass
class QualityLedger:
    """数据质量台账：每个剔除/保留决定都可追溯。"""

    retained: dict[tuple[str, str, str, str], Answer] = field(default_factory=dict)
    dropped: list[tuple[Answer, str]] = field(default_factory=list)
    missing: list[tuple[str, str, str, str, str]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {reason: 0 for reason in MISSING_REASONS}
        out[DUP_DROPPED] = len(self.dropped)
        for *_, reason in self.missing:
            out[reason] += 1
        return out


def build_ledger(bundle: Bundle) -> QualityLedger:
    """按学生×场次×题×分项归并作答，落实重复件保留与缺失留痕。"""
    ledger = QualityLedger()
    groups: dict[tuple[str, str, str, str], list[Answer]] = {}
    for answer in bundle.answers:
        groups.setdefault(
            (answer.student, answer.form, answer.task, answer.skill), []
        ).append(answer)

    for key, items in groups.items():
        valid_items = [a for a in items if a.status == "valid"]
        dup_items = [a for a in items if a.status == "duplicate"]
        if len(valid_items) > 1:
            # 同键多件有效分本身是口径冲突，拒绝静默选择
            raise ValueError(f"同一作答键存在多件有效分，无法确定保留件：{key}")
        for item in dup_items:
            ledger.dropped.append((item, DUP_DROPPED))
        if valid_items:
            ledger.retained[key] = valid_items[0]
            continue
        # 无有效分：按显式状态归类
        status = items[0].status
        reason = {
            "absent": ABSENT,
            "overdue": OVERDUE,
            "receipt_unknown": RECEIPT_UNKNOWN,
            "duplicate": DUP_DROPPED,
        }[status]
        ledger.missing.append((*key, reason))

    # 名册上有、但整场连作答行都没有的学生
    indexed = {(a.student, a.form) for a in bundle.answers}
    form_pop = {}
    for use in bundle.uses:
        form_pop.setdefault(use.form, (use.term, use.group))
    for form, (term, group) in sorted(form_pop.items()):
        for student in bundle.roster(term, group):
            if (student.id, form) not in indexed:
                for use in bundle.form_uses()[form]:
                    for skill in SKILLS:
                        ledger.missing.append(
                            (student.id, form, use.task, skill, NO_RECORD)
                        )
    return ledger


def session_attendance(bundle: Bundle, form: str, ledger: QualityLedger) -> dict[str, dict]:
    """每场考试按学生给出出席状态与有效题数，供比较模块决定样本去留。"""
    term_group = {(u.term, u.group) for u in bundle.uses if u.form == form}
    if len(term_group) != 1:
        raise ValueError(f"场次 {form} 的届/班不唯一")
    term, group = next(iter(term_group))
    tasks = [u.task for u in bundle.form_uses()[form]]
    out: dict[str, dict] = {}
    for student in bundle.roster(term, group):
        reasons: list[str] = []
        valid_cells = 0
        for task in tasks:
            for skill in SKILLS:
                key = (student.id, form, task, skill)
                if key in ledger.retained:
                    valid_cells += 1
                else:
                    match = [m for m in ledger.missing
                             if (m[0], m[1], m[2], m[3]) == key]
                    reasons.extend(m[4] for m in match)
        attended = valid_cells > 0
        out[student.id] = {
            "attended": attended,
            "valid_cells": valid_cells,
            "expected_cells": len(tasks) * len(SKILLS),
            "missing_reasons": sorted(set(reasons)),
        }
    return out


def paired_students(
    bundle: Bundle, pre_form: str, post_form: str, ledger: QualityLedger
) -> set[str]:
    """前后测都至少有有效作答的学生集合（配对增益的样本框）。"""
    pre = session_attendance(bundle, pre_form, ledger)
    post = session_attendance(bundle, post_form, ledger)
    return {sid for sid in pre if pre[sid]["attended"] and post[sid]["attended"]}
