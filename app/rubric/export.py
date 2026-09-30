"""岗位专属加权评分：Excel 五表导出（对齐「简历初筛分析表」结构）。"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import CandidateScore, WeightedRubric


def build_rubric_workbook(rubric: WeightedRubric, results: list[CandidateScore]) -> BytesIO:
    wb = Workbook()
    _sheet_overview(wb.active, rubric, results)
    _sheet_veto(wb.create_sheet("2.一票否决项"), rubric, results)
    _sheet_scores(wb.create_sheet("3.简历评分表"), rubric, results)
    _sheet_interview(wb.create_sheet("4.待面名单与电话提问"), results)
    _sheet_rubric(wb.create_sheet("5.评分标准与使用说明"), rubric, results)

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream


def _title(ws, text: str, span: int):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    ws.cell(1, 1, text).font = Font(bold=True, size=13)
    return 3  # 下一行


def _sheet_overview(ws, rubric, results):
    ws.title = "1.岗位与筛选口径"
    row = _title(ws, f"{rubric.job_title} · 岗位定位与筛选口径", 3)
    ws.append(["项目", "内容"])
    ws.append(["岗位名称", rubric.job_title])
    if rubric.job_context:
        ws.append(["岗位定位与筛选口径", rubric.job_context])
    # 结论摘要
    counts: dict[str, int] = {}
    for r in results:
        counts[r.grade] = counts.get(r.grade, 0) + 1
    passed = sum(1 for r in results if r.action == "✅ 通过")
    pending = sum(1 for r in results if r.action == "⚠ 待确认")
    rejected = sum(1 for r in results if r.action == "❌ 淘汰")
    summary = "｜".join(f"{g} 级 {n} 人" for g, n in sorted(counts.items()))
    ws.append(["结果分布", f"{summary}；✅ 通过 {passed} 人 · ⚠ 待确认 {pending} 人 · ❌ 淘汰 {rejected} 人"])
    _style(ws)


def _sheet_veto(ws, rubric, results):
    ws.append(["一票否决项与本次命中情况"])
    ws.merge_cells("A1:D1")
    ws.cell(1, 1).font = Font(bold=True)
    ws.append(["序号", "否决项", "判定口径", "命中人数"])
    for i, veto in enumerate(rubric.veto_rules):
        hits = [r for r in results if veto.name in r.veto_hits]
        ws.append([str(i), veto.name, veto.criterion, f"{len(hits)} 人"])
    if rubric.warning_rules:
        ws.append([])
        ws.append(["降级为「待确认」的卡点（不否决，但必须电话问清楚）"])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=4)
        ws.append(["编号", "卡点", "判定口径", "命中人数"])
        for i, warn in enumerate(rubric.warning_rules, start=1):
            hits = [r for r in results if warn.name in r.warnings]
            ws.append([f"W{i}", warn.name, warn.criterion, f"{len(hits)} 人"])
    _style(ws)


def _sheet_scores(ws, rubric, results):
    items = rubric.all_items()
    title = f"{rubric.job_title} · 简历评分表（{rubric.total} 分制 · {len(items)} 项 · {len(results)} 人）"
    ws.append([title])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(items) + 8)
    ws.cell(1, 1).font = Font(bold=True)
    header = ["排名", "姓名"] + [f"{key}{item.name}" for key, item in items] + ["基础分", "加分", "总分", "等级", "一票否决/待确认", "建议动作", "优先级"]
    ws.append(header)
    ordered = sorted(results, key=lambda r: -r.total)
    for rank, r in enumerate(ordered, start=1):
        scores = {s.item_id: s.score for s in r.item_scores}
        row = [rank, r.candidate_name]
        for item_id, _item in items:
            row.append(scores.get(item_id, ""))
        row += [r.base_score, r.bonus, r.total, f"{r.grade}级", r.action, r.highlight, r.priority]
        ws.append(row)
    _style(ws)


def _sheet_interview(ws, results):
    ws.append(["待面名单与电话提问清单（✅ 通过 + ⚠ 待确认）"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)
    ws.cell(1, 1).font = Font(bold=True)
    ws.append(["排名", "姓名", "等级 · 总分", "核心亮点（照着夸）", "主要风险（必须问到）", "必问题 1", "必问题 2", "必问题 3"])
    candidates = [r for r in results if r.action in {"✅ 通过", "⚠ 待确认"}]
    candidates.sort(key=lambda r: -r.total)
    for rank, r in enumerate(candidates, start=1):
        questions = (r.phone_questions + ["", "", ""])[:3]
        ws.append([rank, r.candidate_name, f"{r.grade}级 · {r.total}", r.highlight, r.risk, questions[0], questions[1], questions[2]])
    _style(ws)


def _sheet_rubric(ws, rubric, results):
    ws.append([f"评分标准（{len(rubric.all_items())} 项 {rubric.total} 分）与使用说明"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)
    ws.cell(1, 1).font = Font(bold=True)
    ws.append(["维度", "评分项", "权重", "5 分 / 3 分 / 1 分怎么判"])
    for group in rubric.groups:
        for item in group.items:
            ws.append([group.name, f"{item.id} {item.name}", item.weight, item.rubric])
    if rubric.bonus_rules:
        ws.append([])
        ws.append(["加分项（上限 10 分）", "", "", ""])
        for bonus in rubric.bonus_rules:
            ws.append([bonus.name, f"+{bonus.points}", "", bonus.criterion])
    _style(ws)


def _style(ws):
    header_fill = PatternFill("solid", fgColor="E2E8F0")
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="F8FAFC")
    if ws.max_row >= 2:
        for cell in ws[2]:
            cell.fill = header_fill
            cell.font = Font(bold=True)
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for col in range(1, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(col)].width = 22 if col <= 2 else 40
