"""伦理课程跨届测量与题组轮换工具包。

模块分工：
- bundle：资料包装载与口径校验（只读）
- exposure：材料接触三档判定与证据链
- attendance：作答状态处理与有效样本派生
- comparison：分层前后测/届间比较、DiD 练习效应、评分版次分列
- assembly：下一轮组卷硬约束与方案评估
- review：出件后证据复查与替代卷双轨公示
"""
from .bundle import Bundle, BundleError, load_bundle
from .exposure import build_profile, session_profiles, exposure_summary
from .attendance import build_ledger, session_attendance, paired_students
from .comparison import (
    within_term_gains, between_terms, practice_effect_did, rubric_version_split,
)
from .assembly import evaluate_plan_set, audience_for
from .review import review_with_overlap, replacement_disclosure

__all__ = [
    "Bundle", "BundleError", "load_bundle",
    "build_profile", "session_profiles", "exposure_summary",
    "build_ledger", "session_attendance", "paired_students",
    "within_term_gains", "between_terms", "practice_effect_did",
    "rubric_version_split", "evaluate_plan_set", "audience_for",
    "review_with_overlap", "replacement_disclosure",
]
