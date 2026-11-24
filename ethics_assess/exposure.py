"""材料接触（练习／泄漏）判定。

核心口径（见 data-contract.md 与需求说明）：

1. 接触的是"同源情境"，不是同题号。讲评材料按 root 发放，
   一个 root 下的所有题都视为该材料的改写版本；只删同题号会漏判。
2. 判定必须结合该次出件时间与收件范围：
   - 讲评材料 ``sent`` 早于场次开封、且状态为 delivered 才构成确认接触；
   - uncertain（未知送达）构成"未知接触"，不能当干净也不能当练过；
   - returned（退回）不构成接触；
   - overlap 证据只有在分析时点 ``as_of`` 之前、且不晚于场次开封收件，
     才允许用于该场次的判定。考后才到的证据不改变当时的适用口径，
     但会在 review 模块触发对未开封题组的复查。
3. confirmed 关系可沿同源与重合图传递闭包；candidate 只是候选，
   不进入确认闭包，只产生单独的未知接触边，且不继续传递。
4. 接触过同源情境与使用共同评分标准是两个正交维度：
   本模块只回答"见没见过材料"，量表可比性在 comparison 模块处理。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .bundle import Bundle, Overlap

CONFIRMED = "confirmed_exposure"
UNKNOWN = "unknown_exposure"
CLEAN = "clean"


@dataclass(frozen=True)
class Evidence:
    """一条接触判定的证据链，供公示与复查追溯。"""
    kind: str            # lesson_issue / overlap / root
    ref: str             # 材料号或重合边
    state: str
    time: datetime | None


@dataclass
class ExposureProfile:
    """单个学生在单个分析时点下，对每道题的接触判定。"""

    student: str
    term: str
    group: str
    as_of: datetime
    # task -> 判定档位
    classification: dict[str, str]
    # 该画像对应的场次（届级跨班分析时用于回查本人试卷）
    form: str = ""
    # task -> 支持证据（确认档）
    confirmed_evidence: dict[str, list[Evidence]] = field(default_factory=dict)
    # task -> 造成未知档的证据
    unknown_evidence: dict[str, list[Evidence]] = field(default_factory=dict)

    def tasks(self, bucket: str) -> set[str]:
        return {t for t, flag in self.classification.items() if flag == bucket}


def _confirmed_components(bundle: Bundle, as_of: datetime) -> list[set[str]]:
    """截至 as_of 已收件的 confirmed 边（root 同源 + 重合）构成的连通分量。"""
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    # root 同源：同 root 的题永远确认同源（root 是源情境标记，非候选）
    for root, task_ids in bundle.root_tasks().items():
        members = sorted(task_ids)
        for other in members[1:]:
            union(members[0], other)

    # 已确认且按时收件的重合边
    for edge in bundle.overlaps:
        if edge.status == "confirmed" and edge.received <= as_of:
            union(edge.a, edge.b)

    components: dict[str, set[str]] = {}
    for node in list(parent):
        components.setdefault(find(node), set()).add(node)
    return list(components.values())


def _candidate_edges(bundle: Bundle, as_of: datetime) -> list[Overlap]:
    return [
        edge for edge in bundle.overlaps
        if edge.status == "candidate" and edge.received <= as_of
    ]


def build_profile(
    bundle: Bundle,
    student_id: str,
    session_open: datetime,
    as_of: datetime | None = None,
    form: str = "",
) -> ExposureProfile:
    """判定某学生在某场考试（按开封时间）面对每道题的接触档位。

    ``as_of`` 默认为场次开封时间，即"当时适用口径"的判定；
    传入更晚时间可做纳入新证据后的复查判定，两者差异正是复查要披露的内容。
    """
    as_of = as_of or session_open
    student = bundle.students[student_id]
    profile = ExposureProfile(
        student=student_id, term=student.term, group=student.group,
        as_of=as_of, classification={t: CLEAN for t in bundle.tasks},
        form=form,
    )

    # ---- 1. 讲评材料（按出件时间与收件范围）----
    delivered_roots: set[str] = set()
    uncertain_roots: set[str] = set()
    for issue in bundle.issues:
        if issue.term != student.term or student.group not in issue.groups:
            continue
        if issue.sent >= session_open:
            continue  # 考后才发的材料不影响本场
        if issue.state == "delivered":
            delivered_roots.add(issue.root)
        elif issue.state == "uncertain":
            uncertain_roots.add(issue.root)
        # returned：明确退回，不构成接触

    def mark(task_id: str, bucket: str, evidence: Evidence) -> None:
        # 确认档优先于未知档；未知档不得覆盖确认档
        if profile.classification.get(task_id) == CONFIRMED and bucket == UNKNOWN:
            return
        profile.classification[task_id] = bucket
        table = profile.confirmed_evidence if bucket == CONFIRMED else profile.unknown_evidence
        table.setdefault(task_id, []).append(evidence)

    root_lookup = bundle.root_tasks()
    for root in delivered_roots:
        issue = next(i for i in bundle.issues
                     if i.root == root and i.term == student.term
                     and student.group in i.groups and i.state == "delivered")
        for task_id in root_lookup.get(root, ()):  # 同源题全部命中
            mark(task_id, CONFIRMED,
                 Evidence("lesson_issue", issue.id, issue.state, issue.sent))
    for root in uncertain_roots:
        issue = next(i for i in bundle.issues
                     if i.root == root and i.term == student.term
                     and student.group in i.groups and i.state == "uncertain")
        for task_id in root_lookup.get(root, ()):
            mark(task_id, UNKNOWN,
                 Evidence("lesson_issue", issue.id, issue.state, issue.sent))

    # ---- 2. confirmed 图闭包：把接触沿已证实重合传播 ----
    components = _confirmed_components(bundle, as_of)
    seed_tasks: set[str] = set()
    for task_id, bucket in profile.classification.items():
        if bucket == CONFIRMED:
            seed_tasks.add(task_id)
    for component in components:
        if component & seed_tasks:
            evidence_seed = sorted(component & seed_tasks)
            for task_id in component:
                if profile.classification[task_id] != CONFIRMED:
                    mark(task_id, CONFIRMED, Evidence(
                        "overlap", f"closure via {evidence_seed[0]}",
                        "confirmed", as_of))

    # ---- 3. candidate 边：只产生未知档，不传递，不降级确认档 ----
    for edge in _candidate_edges(bundle, as_of):
        ev = Evidence("overlap", f"{edge.a}~{edge.b}", edge.status, edge.received)
        a_flag = profile.classification[edge.a]
        b_flag = profile.classification[edge.b]
        if a_flag == CONFIRMED:
            mark(edge.b, UNKNOWN, ev)
        elif b_flag == CONFIRMED:
            mark(edge.a, UNKNOWN, ev)
        else:
            mark(edge.a, UNKNOWN, ev)
            mark(edge.b, UNKNOWN, ev)

    return profile


def session_profiles(
    bundle: Bundle, form: str, as_of: datetime | None = None
) -> dict[str, ExposureProfile]:
    """一场考试全体应考学生（按该 form 的 term/group 名册）的接触画像。"""
    uses = [u for u in bundle.uses if u.form == form]
    if not uses:
        raise KeyError(f"资料包中没有场次 {form}")
    term, group = uses[0].term, uses[0].group
    opened = bundle.session_open(form)
    return {
        s.id: build_profile(bundle, s.id, opened, as_of, form=form)
        for s in bundle.roster(term, group)
    }


def exposure_summary(bundle: Bundle, form: str, as_of: datetime | None = None) -> dict:
    """按题汇总一场考试中确认接触／未知／干净的学生数，供报告直接引用。"""
    profiles = session_profiles(bundle, form, as_of)
    tasks = sorted({u.task for u in bundle.uses if u.form == form})
    out = {}
    for task_id in tasks:
        counts = {CONFIRMED: 0, UNKNOWN: 0, CLEAN: 0}
        for profile in profiles.values():
            counts[profile.classification[task_id]] += 1
        out[task_id] = counts
    return out
