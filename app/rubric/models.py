"""岗位专属加权评分：数据模型与计分逻辑。

与现有「四维证据打分」并存的一套体系：AI 针对每个岗位自由生成评分维度、
权重、判分规则、一票否决项、待确认卡点与加分项，然后逐项打分并计算加权总分。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScoreItem(BaseModel):
    """单个评分项，如「① 餐饮行业垂直深度 18 分」。"""

    id: str
    name: str
    weight: int
    rubric: str  # 5/3/1 判分规则


class ScoreGroup(BaseModel):
    """评分维度组，如「A 餐饮垂直度 28 分 ★第一道筛子」。"""

    name: str
    items: list[ScoreItem]


class VetoRule(BaseModel):
    """一票否决项：命中直接淘汰。"""

    name: str
    criterion: str


class WarningRule(BaseModel):
    """待确认卡点：不否决，但必须电话问清楚。"""

    name: str
    criterion: str


class BonusRule(BaseModel):
    """加分项：命中加分，合计上限 10 分。"""

    name: str
    points: int
    criterion: str = ""


class WeightedRubric(BaseModel):
    """岗位专属评分标准（AI 生成）。"""

    job_title: str
    job_context: str = ""  # 岗位定位与筛选口径
    groups: list[ScoreGroup]
    veto_rules: list[VetoRule] = Field(default_factory=list)
    warning_rules: list[WarningRule] = Field(default_factory=list)
    bonus_rules: list[BonusRule] = Field(default_factory=list)
    total: int = 100
    grade_thresholds: dict[str, int] = Field(default_factory=lambda: {"S": 85, "A": 75, "B": 60})

    def all_items(self) -> list[tuple[str, ScoreItem]]:
        return [(item.id, item) for group in self.groups for item in group.items]

    def item_map(self) -> dict[str, ScoreItem]:
        return {item.id: item for group in self.groups for item in group.items}


class ItemScore(BaseModel):
    """某个评分项的得分（5 / 3 / 1）与依据。"""

    item_id: str
    score: int
    evidence: str = ""


class CandidateScore(BaseModel):
    """单个候选人的评分结果（AI 输出原始 + 程序计算总分）。"""

    candidate_name: str
    item_scores: list[ItemScore]
    bonus: int = 0
    bonus_reason: str = ""
    veto_hits: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    highlight: str = ""
    risk: str = ""
    phone_questions: list[str] = Field(default_factory=list)

    # 程序计算
    base_score: float = 0
    total: float = 0
    grade: str = ""
    action: str = ""  # ✅ 通过 / ⚠ 待确认 / ❌ 淘汰
    priority: str = ""


def compute_score(candidate: CandidateScore, rubric: WeightedRubric) -> CandidateScore:
    """按权重计算基础分与总分，判定等级与建议动作。"""
    item_map = rubric.item_map()
    base = 0.0
    for item_score in candidate.item_scores:
        item = item_map.get(item_score.item_id)
        if item is None:
            continue
        base += item.weight * item_score.score / 5.0
    candidate.base_score = round(base, 1)
    candidate.total = round(base + candidate.bonus, 1)

    if candidate.veto_hits:
        candidate.grade = "D"
        candidate.action = "❌ 淘汰"
    else:
        thresholds = rubric.grade_thresholds
        if candidate.total >= thresholds.get("S", 85):
            candidate.grade = "S"
        elif candidate.total >= thresholds.get("A", 75):
            candidate.grade = "A"
        elif candidate.total >= thresholds.get("B", 60):
            candidate.grade = "B"
        else:
            candidate.grade = "D"
        candidate.action = "⚠ 待确认" if candidate.warnings else "✅ 通过"

    # 优先级：S 和 A 优先约面
    candidate.priority = "P1" if candidate.grade in {"S", "A"} else ("P2" if candidate.grade == "B" else "P3")
    return candidate
