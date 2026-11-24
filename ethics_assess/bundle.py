"""资料交换包的只读装载、结构校验与索引。

设计原则：
- 只读：装载过程不修改任何输入文件；调用方拿到的是不可变记录。
- 严格：表头、字段数、取值词表、引用完整性和时间线在装载时即校验，
  不符合口径的资料包直接拒绝，避免后续分析在脏数据上静默运行。
- 空白即信息：空白字段不擅自填默认值，按资料口径在各自模块解释。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 资料口径中约定的受控词表。新增状态必须先在口径中登记，再进入这里。
ANSWER_STATES = frozenset({
    "valid",          # 正常有效作答
    "duplicate",      # 重复作答（取指定保留件，其余剔除）
    "absent",         # 确认未参加
    "overdue",        # 已到期但缺交
    "receipt_unknown",  # 未知是否收到材料/试卷
})
ISSUE_STATES = frozenset({"delivered", "uncertain", "returned"})
OVERLAP_STATES = frozenset({"confirmed", "candidate"})
FORM_STATES = frozenset({"draft", "sealed", "opened", "used", "cancelled"})
SEAL_STATES = frozenset({"sealed", "rehearsed", "used", "released", "pulled"})
SKILLS = ("reason", "alternatives")
STAGES = ("pre", "post")


class BundleError(ValueError):
    """资料包不符合交换口径。"""


def parse_ts(value: str) -> datetime | None:
    """解析口径中的 ISO-8601 时间；空白返回 None（不臆造时间）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise BundleError(f"无法解析时间 {value!r}：{exc}") from exc


def parse_int(value: str, what: str) -> int:
    if not value:
        raise BundleError(f"{what} 不允许为空白")
    try:
        return int(value)
    except ValueError as exc:
        raise BundleError(f"{what} 应为整数，实际为 {value!r}") from exc


def split_set(value: str) -> tuple[str, ...]:
    """逗号分隔的集合字段，整体按 CSV 转义后再切分。"""
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class Task:
    id: str
    root: str
    category: str
    minutes: int
    scale: str          # common：共同量表；old_score：旧版评分
    status: str


@dataclass(frozen=True)
class Student:
    id: str
    term: str
    group: str
    background: str


@dataclass(frozen=True)
class Use:
    form: str
    term: str
    group: str
    stage: str
    task: str
    opened: datetime
    state: str


@dataclass(frozen=True)
class Answer:
    student: str
    form: str
    task: str
    skill: str
    score: float | None
    received: datetime | None
    status: str


@dataclass(frozen=True)
class LessonIssue:
    id: str
    root: str
    term: str
    groups: tuple[str, ...]
    sent: datetime
    state: str


@dataclass(frozen=True)
class Overlap:
    a: str
    b: str
    status: str
    received: datetime


@dataclass(frozen=True)
class NextForm:
    form: str
    audience: str
    opens: datetime
    state: str
    min_categories: int
    minutes: int
    anchor_capacity: int
    graders: tuple[str, ...]


@dataclass(frozen=True)
class Seal:
    form: str
    task: str
    status: str
    time: datetime


@dataclass(frozen=True)
class AnchorRule:
    task: str
    scope: str
    min_independent_uses: int
    max_forms: int


@dataclass(frozen=True)
class GraderSlot:
    grader: str
    available_day: str
    skills: tuple[str, ...]
    capacity: int


@dataclass
class Bundle:
    """已校验资料包的内存表示及查询索引。"""

    tasks: dict[str, Task]
    students: dict[str, Student]
    uses: list[Use]
    answers: list[Answer]
    issues: list[LessonIssue]
    overlaps: list[Overlap]
    next_forms: dict[str, NextForm]
    seals: list[Seal]
    anchor_rules: dict[str, AnchorRule]
    grader_slots: list[GraderSlot]
    warnings: list[str] = field(default_factory=list)

    # ---- 派生索引 ----
    def use_index(self) -> dict[tuple[str, str], Use]:
        return {(u.form, u.task): u for u in self.uses}

    def form_uses(self) -> dict[str, list[Use]]:
        out: dict[str, list[Use]] = {}
        for use in self.uses:
            out.setdefault(use.form, []).append(use)
        return out

    def session_open(self, form: str) -> datetime:
        opens = {u.opened for u in self.uses if u.form == form}
        if not opens:
            raise BundleError(f"场次 {form} 没有出件记录")
        if len(opens) > 1:
            raise BundleError(f"场次 {form} 的开封时间不一致")
        return next(iter(opens))

    def form_stage(self, form: str) -> str:
        stages = {u.stage for u in self.uses if u.form == form}
        if len(stages) != 1:
            raise BundleError(f"场次 {form} 的阶段不唯一：{stages}")
        return next(iter(stages))

    def root_tasks(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for task in self.tasks.values():
            out.setdefault(task.root, set()).add(task.id)
        return out

    def roster(self, term: str, group: str | None = None) -> list[Student]:
        return [
            s for s in self.students.values()
            if s.term == term and (group is None or s.group == group)
        ]

    def independent_uses(self, task: str) -> set[str]:
        """该题历史上在多少个独立场次（不同 form）实际使用过。"""
        return {u.form for u in self.uses if u.task == task and u.state == "received"}


def _read_table(root: Path, filename: str, fields: list[str]) -> list[dict[str, str]]:
    path = root / filename
    if not path.exists():
        raise BundleError(f"缺少数据表 {filename}")
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.reader(source, strict=True)
        header = next(reader, None)
        if header != fields:
            raise BundleError(f"{filename} 的表头与资料清单不符")
        rows: list[dict[str, str]] = []
        for line, raw in enumerate(reader, start=2):
            if len(raw) != len(fields):
                raise BundleError(f"{filename} 第 {line} 行的字段数量不符")
            rows.append(dict(zip(fields, raw)))
    return rows


def load_bundle(root: str | Path) -> Bundle:
    """按 manifest 装载并完整校验一个资料交换包。"""
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    declared = set(manifest)
    actual = {p.name for p in root.glob("*.csv")}
    if declared != actual:
        raise BundleError(f"清单与目录不一致：缺 {sorted(declared - actual)}，多 {sorted(actual - declared)}")

    warnings: list[str] = []

    task_rows = _read_table(root, "task.csv", manifest["task.csv"])
    tasks: dict[str, Task] = {}
    for row in task_rows:
        if row["id"] in tasks:
            raise BundleError(f"task.csv 中 {row['id']} 重复")
        if row["scale"] not in {"common", "old_score"} or row["status"] != "active":
            raise BundleError(f"task.csv 中 {row['id']} 的量表或状态不在词表内")
        tasks[row["id"]] = Task(
            id=row["id"], root=row["root"], category=row["category"],
            minutes=parse_int(row["minutes"], f"task {row['id']} minutes"),
            scale=row["scale"], status=row["status"],
        )

    student_rows = _read_table(root, "student.csv", manifest["student.csv"])
    students: dict[str, Student] = {}
    for row in student_rows:
        if row["id"] in students:
            raise BundleError(f"student.csv 中 {row['id']} 重复")
        students[row["id"]] = Student(**row)

    use_rows = _read_table(root, "use.csv", manifest["use.csv"])
    uses: list[Use] = []
    seen_use: set[tuple[str, str]] = set()
    for row in use_rows:
        if row["task"] not in tasks:
            raise BundleError(f"use.csv 引用了不存在的题 {row['task']}")
        if row["stage"] not in STAGES or row["state"] != "received":
            raise BundleError(f"use.csv 中 {row['form']}/{row['task']} 的阶段或状态非法")
        key = (row["form"], row["task"])
        if key in seen_use:
            raise BundleError(f"use.csv 中 {row['form']}/{row['task']} 重复")
        seen_use.add(key)
        opened = parse_ts(row["opened"])
        if opened is None:
            raise BundleError(f"use.csv 中 {row['form']}/{row['task']} 缺开封时间")
        uses.append(Use(
            form=row["form"], term=row["term"], group=row["group"],
            stage=row["stage"], task=row["task"], opened=opened, state=row["state"],
        ))

    answer_rows = _read_table(root, "answer.csv", manifest["answer.csv"])
    answers: list[Answer] = []
    answer_keys: dict[tuple[str, str, str, str], int] = {}
    use_index = {(u.form, u.task): u for u in uses}
    for row in answer_rows:
        if row["student"] not in students:
            raise BundleError(f"answer.csv 引用了不存在的学生 {row['student']}")
        if (row["form"], row["task"]) not in use_index:
            raise BundleError(
                f"answer.csv 中 {row['student']}/{row['form']}/{row['task']} 没有出件记录")
        if row["skill"] not in SKILLS:
            raise BundleError(f"answer.csv 出现未知评分分项 {row['skill']}")
        if row["status"] not in ANSWER_STATES:
            raise BundleError(f"answer.csv 出现未知作答状态 {row['status']}")
        key = (row["student"], row["form"], row["task"], row["skill"])
        if key in answer_keys:
            if row["status"] == "duplicate":
                warnings.append(f"重复作答 {key} 已按口径标记为 duplicate")
            else:
                raise BundleError(f"answer.csv 中同一作答键重复且未标记 duplicate：{key}")
        answer_keys[key] = answer_keys.get(key, 0) + 1
        score = float(row["score"]) if row["score"] else None
        if score is not None and not 0.0 <= score <= 10.0:
            raise BundleError(f"分项分超出 0-10：{key}={score}")
        if row["status"] == "valid" and score is None:
            raise BundleError(f"有效作答缺分数：{key}")
        answers.append(Answer(
            student=row["student"], form=row["form"], task=row["task"],
            skill=row["skill"], score=score,
            received=parse_ts(row["received"]), status=row["status"],
        ))

    issue_rows = _read_table(root, "lesson_issue.csv", manifest["lesson_issue.csv"])
    issues: list[LessonIssue] = []
    for row in issue_rows:
        if row["state"] not in ISSUE_STATES:
            raise BundleError(f"lesson_issue.csv 出现未知状态 {row['state']}")
        sent = parse_ts(row["sent"])
        if sent is None:
            raise BundleError(f"讲评材料 {row['id']} 缺出件时间")
        groups = split_set(row["groups"])
        issues.append(LessonIssue(
            id=row["id"], root=row["root"], term=row["term"],
            groups=groups, sent=sent, state=row["state"],
        ))

    overlap_rows = _read_table(root, "overlap.csv", manifest["overlap.csv"])
    overlaps: list[Overlap] = []
    for row in overlap_rows:
        if row["status"] not in OVERLAP_STATES:
            raise BundleError(f"overlap.csv 出现未知状态 {row['status']}")
        for task_id in (row["a"], row["b"]):
            if task_id not in tasks:
                raise BundleError(f"overlap.csv 引用了不存在的题 {task_id}")
        received = parse_ts(row["received"])
        if received is None:
            raise BundleError("overlap.csv 的证据收件时间不允许空白")
        overlaps.append(Overlap(row["a"], row["b"], row["status"], received))

    next_rows = _read_table(root, "next_form.csv", manifest["next_form.csv"])
    next_forms: dict[str, NextForm] = {}
    for row in next_rows:
        if row["state"] not in FORM_STATES:
            raise BundleError(f"next_form.csv 出现未知状态 {row['state']}")
        opens = parse_ts(row["opens"])
        if opens is None:
            raise BundleError(f"题组 {row['form']} 缺开封时间")
        next_forms[row["form"]] = NextForm(
            form=row["form"], audience=row["audience"], opens=opens, state=row["state"],
            min_categories=parse_int(row["min_categories"], "min_categories"),
            minutes=parse_int(row["minutes"], "minutes"),
            anchor_capacity=parse_int(row["anchor_capacity"], "anchor_capacity"),
            graders=split_set(row["graders"]),
        )

    seal_rows = _read_table(root, "seal.csv", manifest["seal.csv"])
    seals: list[Seal] = []
    for row in seal_rows:
        if row["form"] not in next_forms:
            raise BundleError(f"seal.csv 引用了未登记题组 {row['form']}")
        if row["task"] not in tasks:
            raise BundleError(f"seal.csv 引用了不存在的题 {row['task']}")
        if row["status"] not in SEAL_STATES:
            raise BundleError(f"seal.csv 出现未知状态 {row['status']}")
        time = parse_ts(row["time"])
        if time is None:
            raise BundleError("seal.csv 的时间不允许空白")
        seals.append(Seal(row["form"], row["task"], row["status"], time))

    anchor_rows = _read_table(root, "anchor.csv", manifest["anchor.csv"])
    anchor_rules: dict[str, AnchorRule] = {}
    for row in anchor_rows:
        if row["task"] not in tasks:
            raise BundleError(f"anchor.csv 引用了不存在的题 {row['task']}")
        anchor_rules[row["task"]] = AnchorRule(
            task=row["task"], scope=row["scope"],
            min_independent_uses=parse_int(row["min_independent_uses"], "min_independent_uses"),
            max_forms=parse_int(row["max_forms"], "max_forms"),
        )

    team_rows = _read_table(root, "team.csv", manifest["team.csv"])
    grader_slots: list[GraderSlot] = []
    for row in team_rows:
        skills = split_set(row["skill"])
        unknown = set(skills) - set(SKILLS)
        if unknown:
            raise BundleError(f"team.csv 出现未知分项 {unknown}")
        grader_slots.append(GraderSlot(
            grader=row["grader"], available_day=row["available_day"],
            skills=tuple(skills), capacity=parse_int(row["capacity"], "capacity"),
        ))

    # 场次与名册一致性：use 中出现的 term/group 必须能在 student 中找到人
    roster_lookup = {(s.term, s.group) for s in students.values()}
    for use in uses:
        if (use.term, use.group) not in roster_lookup:
            raise BundleError(f"use.csv 的 {use.form} 指向无名册的 {use.term} 届 {use.group} 班")

    return Bundle(
        tasks=tasks, students=students, uses=uses, answers=answers, issues=issues,
        overlaps=overlaps, next_forms=next_forms, seals=seals,
        anchor_rules=anchor_rules, grader_slots=grader_slots, warnings=warnings,
    )
