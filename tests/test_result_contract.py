"""结果预览契约：等级以 grade 机器值输出，conclusion 只作为结论文案。"""

from __future__ import annotations

import unittest

from app.main import public_job
from app.models import CandidateEvaluation
from app.pipeline import conclusion_grade, result_preview


def _evaluation(conclusion: str) -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate_name="张三",
        conclusion=conclusion,
        one_line="经验匹配",
        next_action="约面",
        source_file="a.pdf",
    )


class ConclusionGradeTest(unittest.TestCase):
    def test_maps_each_conclusion_to_letter(self) -> None:
        self.assertEqual(conclusion_grade("S电话沟通"), "S")
        self.assertEqual(conclusion_grade("A优先约面"), "A")
        self.assertEqual(conclusion_grade("B电话确认"), "B")
        self.assertEqual(conclusion_grade("C不推进"), "C")

    def test_unknown_or_empty_conclusion_falls_back_to_c(self) -> None:
        self.assertEqual(conclusion_grade(""), "C")
        self.assertEqual(conclusion_grade(None), "C")
        self.assertEqual(conclusion_grade("无法判断"), "C")


class ResultPreviewTest(unittest.TestCase):
    def test_preview_carries_grade_and_conclusion(self) -> None:
        preview = result_preview(_evaluation("B电话确认"))
        self.assertEqual(preview["grade"], "B")
        self.assertEqual(preview["conclusion"], "B电话确认")


class PublicJobTest(unittest.TestCase):
    def test_legacy_results_without_grade_get_one(self) -> None:
        payload = public_job(
            {"id": "j1", "results": [{"source_file": "a.pdf", "conclusion": "A优先约面"}]}
        )
        self.assertEqual(payload["results"][0]["grade"], "A")

    def test_existing_grade_is_preserved(self) -> None:
        payload = public_job(
            {"id": "j1", "results": [{"source_file": "a.pdf", "conclusion": "A优先约面", "grade": "S"}]}
        )
        self.assertEqual(payload["results"][0]["grade"], "S")

    def test_non_list_results_are_untouched(self) -> None:
        payload = public_job({"id": "j1", "results": None})
        self.assertIsNone(payload["results"])


if __name__ == "__main__":
    unittest.main()
