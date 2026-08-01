# -*- coding: utf-8 -*-
"""格式纠错编排引擎（独立于 ai_correction）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Set

from .candidates import build_candidate_tasks
from .cross_page_merge import apply_merges, propose_merge
from .empty_split import apply_empty_split_tasks, propose_empty_split
from .liteparse_bridge import (
    load_liteparse_dict,
    load_tables_from_mid,
    meaningful_text_lines,
    page_gap_text,
    save_report,
)
from .llm_referee import refine_glue_with_llm, refine_merge_task_with_llm
from .models import FormatCorrectionReport, FormatTask, TaskStatus, TaskType
from .structure_pre_split import expand_tables_with_structure_split
from .text_boundary import apply_text_flags


class FormatCorrectorEngine:
    """独立格式纠错流程：

    0. 先用 TE 结构拆分切开粘连表（小节/重复表头）
    1. 扫描可疑表（缺表头 / 空行空列 / 跨页 / 文表）
    2. 生成提案（规则；可选 LLM 裁判）
    3. 守恒校验
    4. 可选应用（默认只自动应用高置信且守恒的空列/空行删除与跨页合并）
    """

    def __init__(
        self,
        pdf_path: str = "",
        *,
        use_llm: bool = False,
        auto_apply: bool = False,
        pre_structure_split: bool = True,
    ):
        self.pdf_path = pdf_path
        self.use_llm = use_llm
        self.auto_apply = auto_apply
        self.pre_structure_split = pre_structure_split
        # 最近一次扫描后的工作表（含结构预拆分），供 UI 写回
        self.last_working_tables: Optional[List[dict]] = None

    def run_on_tables(
        self,
        tables: List[dict],
        liteparse_data: Optional[dict] = None,
    ) -> FormatCorrectionReport:
        tables = tables or []
        if liteparse_data is None and self.pdf_path:
            liteparse_data = load_liteparse_dict(self.pdf_path)

        split_notes: List[str] = []
        if self.pre_structure_split:
            tables, split_notes = expand_tables_with_structure_split(tables)
        # 数值+文本粘连（如「19,079,642 成都」）先拆列，避免误挂跨页合并
        try:
            from codes.v2_steps.table_glue_repair import (
                repair_tables_numeric_text_glue,
            )

            tables, glue_notes = repair_tables_numeric_text_glue(tables)
            split_notes.extend(glue_notes)
        except Exception:
            pass
        self.last_working_tables = tables

        tasks = build_candidate_tasks(tables, liteparse_data)

        # 提案阶段
        proposed: List[FormatTask] = []
        for task in tasks:
            if task.task_type == TaskType.CROSS_PAGE_MERGE:
                if self.use_llm and task.related_indices:
                    j = task.related_indices[0]
                    gap = page_gap_text(
                        liteparse_data,
                        int(tables[task.table_index].get("page") or 0),
                        int(tables[j].get("page") or 0),
                        tables[task.table_index],
                        tables[j],
                    )
                    task = refine_merge_task_with_llm(
                        task,
                        tables[task.table_index],
                        tables[j],
                        meaningful_text_lines(gap)[:5],
                    )
                task = propose_merge(task, tables)
            elif task.task_type == TaskType.EMPTY_SPLIT:
                task = propose_empty_split(task, tables[task.table_index], liteparse_data)
                if self.use_llm:
                    task = refine_glue_with_llm(task, tables[task.table_index])
                    # 重新校验补丁
                    task = propose_empty_split(task, tables[task.table_index], liteparse_data)
            elif task.task_type == TaskType.HEADER_CROSS_PAGE:
                task.status = TaskStatus.PROPOSED
            elif task.task_type == TaskType.TEXT_TABLE_SPLIT:
                task.status = TaskStatus.PROPOSED
            proposed.append(task)

        notes = list(split_notes)
        notes.append(
            "仅生成提案，未写回" if not self.auto_apply else "将尝试自动应用高置信项"
        )
        if split_notes:
            notes.insert(
                0,
                f"结构预拆分：已按 TE 逻辑切开粘连表（{len(split_notes)} 处），"
                "避免前表内残留（五）/重复表头",
            )
        report = FormatCorrectionReport(
            pdf_path=self.pdf_path,
            tasks=proposed,
            summary=self._summarize(proposed),
            notes=notes,
        )
        report.summary["working_table_count"] = len(tables)
        report.summary["structure_presplit_count"] = len(split_notes)

        if self.auto_apply:
            new_tables, report = self.apply(
                tables,
                report,
                only_auto=True,
                liteparse_data=liteparse_data,
            )
            report.summary["tables_out"] = len(new_tables)
            report.summary["applied_preview"] = True
            # 把应用后的 tables 暂存在 summary 侧车，避免改 models
            report.summary["_tables_result_ref"] = "use apply() return value"

        if self.pdf_path:
            try:
                save_report(self.pdf_path, report.to_dict())
            except Exception as e:
                report.notes.append(f"保存报告失败: {e}")

        return report

    def run_from_pdf_cache(self) -> FormatCorrectionReport:
        tables, _payload = load_tables_from_mid(self.pdf_path)
        liteparse = load_liteparse_dict(self.pdf_path)
        return self.run_on_tables(tables, liteparse)

    def apply(
        self,
        tables: List[dict],
        report: FormatCorrectionReport,
        *,
        only_auto: bool = False,
        accepted_ids: Optional[Set[str]] = None,
        liteparse_data: Optional[dict] = None,
        remove_text_rows: bool = False,
        compact_hidden: bool = False,
    ) -> tuple:
        """应用提案，返回 (new_tables, updated_report)。"""
        new_tables = deepcopy(tables)
        notes = list(report.notes)

        new_tables, tasks, n1 = apply_merges(
            new_tables,
            report.tasks,
            only_auto=only_auto,
            accepted_ids=accepted_ids,
        )
        notes.extend(n1)

        new_tables, tasks, n2 = apply_empty_split_tasks(
            new_tables,
            tasks,
            only_auto=only_auto,
            accepted_ids=accepted_ids,
            liteparse_data=liteparse_data,
        )
        notes.extend(n2)

        # 文表：默认只标记；显式 accepted + remove_text_rows 才移出
        text_ids = accepted_ids
        if only_auto:
            text_ids = set()  # 自动模式不移出正文
        new_tables, tasks, n3 = apply_text_flags(
            new_tables,
            tasks,
            accepted_ids=text_ids if not only_auto else set(),
            remove_from_table=remove_text_rows,
        )
        # only_auto 时仍标记所有 text 任务
        if only_auto:
            new_tables, tasks, n3b = apply_text_flags(
                new_tables,
                tasks,
                accepted_ids={t.task_id for t in tasks if t.task_type == TaskType.TEXT_TABLE_SPLIT},
                remove_from_table=False,
            )
            notes.extend(n3b)
        else:
            notes.extend(n3)

        if compact_hidden:
            from .cross_page_merge import compact_hidden_tables

            new_tables = compact_hidden_tables(new_tables)
            notes.append("已清理合并占位表（索引已变）")

        report.tasks = tasks
        report.notes = notes
        report.summary = self._summarize(tasks)
        report.summary["result_table_count"] = len(new_tables)

        if self.pdf_path:
            try:
                save_report(self.pdf_path, report.to_dict())
            except Exception:
                pass

        return new_tables, report

    def write_back_mid_cache(
        self,
        new_tables: List[dict],
        *,
        payload: Optional[dict] = None,
    ) -> str:
        """将纠错后的 tables 写回 mid_cache data.json（显式调用才写）。"""
        from codes.pdf_extractor.utils import load_mid_data, save_mid_data

        if not self.pdf_path:
            raise ValueError("pdf_path 为空，无法写回")
        data = payload or load_mid_data(self.pdf_path) or {}
        data = deepcopy(data)
        data["tables"] = new_tables
        data["total_tables"] = len(new_tables)
        data["_format_corrector_applied"] = True
        path = save_mid_data(self.pdf_path, data)
        return str(path)

    @staticmethod
    def _summarize(tasks: List[FormatTask]) -> Dict[str, Any]:
        by_type: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for t in tasks:
            by_type[t.task_type.value] = by_type.get(t.task_type.value, 0) + 1
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
        return {
            "task_count": len(tasks),
            "by_type": by_type,
            "by_status": by_status,
            "high_confidence": sum(1 for t in tasks if t.confidence.value == "high"),
        }
