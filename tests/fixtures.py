"""测试用最小资料包构造器：在临时目录生成符合口径的合成资料。"""
from __future__ import annotations

import json
from pathlib import Path

FIELDS = {
    "task.csv": ["id", "root", "category", "minutes", "scale", "status"],
    "student.csv": ["id", "term", "group", "background"],
    "use.csv": ["form", "term", "group", "stage", "task", "opened", "state"],
    "answer.csv": ["student", "form", "task", "skill", "score", "received", "status"],
    "lesson_issue.csv": ["id", "root", "term", "groups", "sent", "state"],
    "overlap.csv": ["a", "b", "status", "received"],
    "next_form.csv": ["form", "audience", "opens", "state", "min_categories",
                      "minutes", "anchor_capacity", "graders"],
    "seal.csv": ["form", "task", "status", "time"],
    "anchor.csv": ["task", "scope", "min_independent_uses", "max_forms"],
    "team.csv": ["grader", "available_day", "skill", "capacity"],
}


def write_csv(path: Path, filename: str, rows: list[list[str]]) -> None:
    import csv
    with (path / filename).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, strict=True)
        writer.writerow(FIELDS[filename])
        writer.writerows(rows)


def minimal_bundle(root: str | Path) -> Path:
    """两题（一 common 一 old_score）、两名学生、一场前测一场后测的最小包。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps({name: fields for name, fields in FIELDS.items()}),
        encoding="utf-8")

    write_csv(root, "task.csv", [
        ["T1", "R1", "C1", "8", "common", "active"],
        ["T2", "R2", "C2", "9", "old_score", "active"],
        ["T3", "R3", "C3", "10", "common", "active"],
    ])
    write_csv(root, "student.csv", [
        ["S1", "1", "1", "early"],
        ["S2", "1", "1", "late"],
    ])
    pre, post = "2026-09-01T09:00:00+08:00", "2026-09-05T09:00:00+08:00"
    use_rows = []
    for form, stage, opened in (("F-pre", "pre", pre), ("F-post", "post", post)):
        for task in ("T1", "T2", "T3"):
            use_rows.append([form, "1", "1", stage, task, opened, "received"])
    write_csv(root, "use.csv", use_rows)

    answer_rows = []
    for student in ("S1", "S2"):
        for form in ("F-pre", "F-post"):
            for task in ("T1", "T2", "T3"):
                for skill in ("reason", "alternatives"):
                    answer_rows.append(
                        [student, form, task, skill, "5.0",
                         pre if form == "F-pre" else post, "valid"])
    write_csv(root, "answer.csv", answer_rows)
    write_csv(root, "lesson_issue.csv", [])
    write_csv(root, "overlap.csv", [])
    write_csv(root, "next_form.csv", [])
    write_csv(root, "seal.csv", [])
    write_csv(root, "anchor.csv", [])
    write_csv(root, "team.csv", [])
    return root
