# -*- coding: utf-8 -*-
"""按错误类型纠正：规则优先 → 分类型 LLM 指令 → 不变量验收。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from codes.table_repair.error_types import (
    GLOBAL_PRINCIPLES,
    ErrorTypeSpec,
    build_typed_llm_instructions,
    errors_from_checklist_findings,
    partition_errors,
)
from codes.table_repair.invariants import (
    locate_data_zone,
    strip_title_rows,
    validate_structure_invariants,
)
from codes.table_repair.validator import normalize_grid, validate_repair


@dataclass
class TypedRepairResult:
    success: bool = False
    applied: bool = False
    repaired_table: List[List[str]] = field(default_factory=list)
    confidence: float = 0.0
    error_ids: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)
    llm_error: str = ""
    escalate_human: bool = False
    report_text: str = ""
    reasoning_summary: str = ""

    # 兼容 Facade / UI
    @property
    def problem_tags(self) -> List[str]:
        return list(self.error_ids)


def apply_typed_rule_fixes(
    data: Sequence[Sequence[Any]],
    errors: Sequence[ErrorTypeSpec],
) -> tuple:
    """对 disposition=auto 的类型做确定性规则修。返回 (grid, notes, fixed_ids)。"""
    grid = normalize_grid(data)
    notes: List[str] = []
    fixed: List[str] = []
    ids = {e.error_id for e in errors}

    if "H_TITLE" in ids or "G_MIX" in ids:
        new_grid, removed, n = strip_title_rows(grid)
        if n:
            grid = new_grid
            notes.extend(n)
            fixed.append("H_TITLE")

    return grid, notes, fixed


def run_typed_repair_on_table(
    table: Dict[str, Any],
    *,
    findings: Optional[Sequence[Dict]] = None,
    run_llm: bool = False,
    apply: bool = False,
) -> TypedRepairResult:
    """按错误类型修复单表。

    findings: 来自 _repair_checklist.findings；为空则从 checklist 现算。
    """
    out = TypedRepairResult()
    if not table or table.get("type") in ("text", "paragraph"):
        out.escalate_human = False
        out.success = True
        return out

    try:
        from codes.table_repair.table_kind import attach_table_kind, should_run_structure_repair

        kind = attach_table_kind(table)
        if not should_run_structure_repair(table):
            out.success = True
            out.actions.append(f"skip_{kind.kind}:" + ";".join(kind.reasons[:3]))
            out.repaired_table = normalize_grid(table.get("data") or [])
            table["repair_status"] = "skipped_non_data"
            return out
    except Exception as exc:
        out.actions.append(f"kind_error:{exc}")

    data = normalize_grid(table.get("data") or [])
    before = deepcopy(data)

    if findings is None:
        try:
            from codes.table_repair.checklist import run_full_checklist

            cl = run_full_checklist(table)
            findings = cl.get("findings") or []
            table["_repair_checklist"] = cl
        except Exception as exc:
            out.llm_error = f"checklist_failed: {exc}"
            out.escalate_human = True
            return out

    errors = errors_from_checklist_findings(findings)
    out.error_ids = [e.error_id for e in errors]
    buckets = partition_errors(errors)

    if buckets.get("human"):
        # 有丢数类：禁止 LLM 补数，直接升级人工（仍可先做无害规则）
        out.actions.append(
            "human_errors=" + ",".join(e.error_id for e in buckets["human"])
        )

    # Phase A typed rules
    grid, notes, fixed = apply_typed_rule_fixes(data, errors)
    out.actions.extend(notes)
    if fixed:
        out.actions.append("rule_fixed_types=" + ",".join(fixed))
        data = grid

    # 标签折行/粘连等仍走现有 wrap/glue（与 error 类型对齐）
    auto_ids = {e.error_id for e in buckets.get("auto") or []}
    if auto_ids & {"L_WRAP", "L_GLUE", "L_SERIAL", "H_WRAP", "G_GLUE"}:
        try:
            from codes.table_repair.column_roles import infer_column_roles
            from codes.table_repair.wrap_repair import repair_table_wrap_split

            # 临时写回再修
            table["data"] = data
            label_col = int(infer_column_roles(data).primary_label_col or 0)
            wnotes = repair_table_wrap_split(table, label_col=label_col)
            if wnotes:
                out.actions.extend(wnotes)
                data = normalize_grid(table.get("data") or data)
        except Exception as exc:
            out.actions.append(f"wrap_error: {exc}")

        if "G_GLUE" in auto_ids and not table.get("_glue_repaired"):
            try:
                from codes.v2_steps.table_glue_repair import (
                    repair_table_numeric_text_glue,
                )

                table["data"] = data
                gnotes = repair_table_numeric_text_glue(table)
                if gnotes:
                    out.actions.extend(gnotes)
                    data = normalize_grid(table.get("data") or data)
            except Exception as exc:
                out.actions.append(f"glue_error: {exc}")

    # 若仅 human 且无 llm 需求
    llm_errors = [e for e in (buckets.get("llm") or []) if e.llm_task]
    if buckets.get("human") and not llm_errors and not fixed:
        out.escalate_human = True
        out.repaired_table = data
        out.success = False
        out.llm_error = "存在必须人工处理的错误（如疑似丢数）"
        table["data"] = data
        return out

    if not llm_errors or not run_llm:
        # 只规则
        ok, reasons = validate_structure_invariants(before, data)
        out.repaired_table = data
        out.validation_errors = reasons
        out.success = ok or (data != before and not amounts_block(before, data))
        if data != before:
            table["data"] = data
            table["rows"] = len(data)
            table["cols"] = max((len(r) for r in data), default=0)
            if out.success and not buckets.get("human") and not llm_errors:
                table["repair_status"] = "rule_fixed"
                out.applied = True
            elif llm_errors and not run_llm:
                table["repair_status"] = "llm_candidate"
                out.actions.append("awaiting_typed_llm=" + ",".join(
                    e.error_id for e in llm_errors
                ))
            elif buckets.get("human"):
                table["repair_status"] = "human_needed"
                out.escalate_human = True
        else:
            if buckets.get("human"):
                table["repair_status"] = "human_needed"
                out.escalate_human = True
            elif llm_errors:
                table["repair_status"] = "llm_candidate"
            out.success = not (llm_errors or buckets.get("human"))
        out.report_text = "\n".join(GLOBAL_PRINCIPLES[:3] + tuple(out.actions[:8]))
        return out

    # Phase B：按类型指令一次 LLM
    typed_ctx = build_typed_llm_instructions(llm_errors)
    zone = locate_data_zone(data)
    typed_ctx += (
        f"\n\n数据区提示: start_row={zone.start_row}, cols={zone.n_cols}, "
        f"value_cols={zone.value_cols}, label_col={zone.label_col}"
    )

    try:
        from codes.table_repair.llm_facade import repair_with_facade
        from codes.table_repair.human_queue import store_llm_proposal

        tags = []
        for e in llm_errors:
            tags.extend(e.problem_tags)
        tags = list(dict.fromkeys(tags)) or ["misalignment", "merge_lost"]

        fac = repair_with_facade(
            data,
            problem_tags=tags,
            context=typed_ctx,
        )
        out.actions.append(
            f"typed_llm tasks={[e.llm_task for e in llm_errors]} "
            f"success={fac.success}"
        )
        out.confidence = float(fac.confidence or 0.0)
        out.reasoning_summary = fac.reasoning_summary or ""
        out.report_text = fac.report_text or typed_ctx[:500]

        candidate = normalize_grid(fac.repaired_table or [])
        ok1, reasons1 = validate_repair(
            before, candidate, confidence=fac.confidence,
        )
        ok2, reasons2 = validate_structure_invariants(before, candidate)
        out.validation_errors = list(reasons1) + list(reasons2)

        if fac.success and candidate and ok1 and ok2:
            out.success = True
            out.repaired_table = candidate
            store_llm_proposal(
                table,
                before_data=before,
                repaired_table=candidate,
                success=True,
                confidence=out.confidence,
                validation_errors=[],
                problem_tags=tags,
                reasoning_summary=out.reasoning_summary,
                report_text=out.report_text,
                set_status=True,
            )
            if apply:
                from codes.table_repair.human_queue import apply_proposal_to_table

                apply_proposal_to_table(table, candidate, status="llm_applied")
                out.applied = True
            else:
                table["repair_status"] = "llm_proposed"
                # 保持原 data，提案在 _llm_proposal
                table["data"] = before
        else:
            out.success = False
            out.escalate_human = True
            out.llm_error = fac.llm_error or "; ".join(out.validation_errors) or "typed_llm_failed"
            out.repaired_table = data
            table["data"] = data
            table["repair_status"] = "human_needed"
            store_llm_proposal(
                table,
                before_data=before,
                repaired_table=candidate or data,
                success=False,
                confidence=out.confidence,
                validation_errors=out.validation_errors,
                llm_error=out.llm_error,
                problem_tags=tags,
                set_status=True,
            )
    except Exception as exc:
        out.success = False
        out.escalate_human = True
        out.llm_error = str(exc)
        out.repaired_table = data
        table["data"] = data
        table["repair_status"] = "human_needed"

    return out


def amounts_block(before, after) -> bool:
    from codes.table_repair.validator import amounts_invented

    return bool(amounts_invented(before, after))
