# -*- coding: utf-8 -*-
"""结构类任务：消费还原主链快照/提案（不再独立做「凡有错就 AI」决策）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Optional, Set, Tuple

from .models import Confidence, FormatTask, TaskStatus, TaskType


def summarize_table_changes(
    before: List[List],
    after: List[List],
    *,
    max_items: int = 40,
) -> Dict:
    """对比修复前后，生成可读变更摘要（供 UI「到底改了什么」）。"""
    b = before if isinstance(before, list) else []
    a = after if isinstance(after, list) else []
    changes: List[str] = []
    cells: List[Tuple[int, int]] = []
    n_b, n_a = len(b), len(a)
    cols_b = max((len(r) for r in b if isinstance(r, list)), default=0)
    cols_a = max((len(r) for r in a if isinstance(r, list)), default=0)
    if n_b != n_a:
        changes.append(f"行数 {n_b} → {n_a}")
    if cols_b != cols_a:
        changes.append(f"列数 {cols_b} → {cols_a}")

    for ri in range(max(n_b, n_a)):
        row_b = b[ri] if ri < n_b and isinstance(b[ri], list) else []
        row_a = a[ri] if ri < n_a and isinstance(a[ri], list) else []
        width = max(len(row_b), len(row_a), cols_b, cols_a)
        for ci in range(width):
            vb = str(row_b[ci] if ci < len(row_b) else "") or ""
            va = str(row_a[ci] if ci < len(row_a) else "") or ""
            if vb.strip() == va.strip():
                continue
            cells.append((ri, ci))
            if len(changes) < max_items:
                left = (vb[:24] + "…") if len(vb) > 24 else vb
                right = (va[:24] + "…") if len(va) > 24 else va
                changes.append(f"R{ri}C{ci}: 「{left}」→「{right}」")

    return {
        "changed_cell_count": len(cells),
        "changed_cells": cells[:200],
        "lines": changes,
        "row_count_before": n_b,
        "row_count_after": n_a,
        "identical": len(cells) == 0 and n_b == n_a and cols_b == cols_a,
    }


def hydrate_structure_task_from_main_chain(
    task: FormatTask,
    table: dict,
) -> FormatTask:
    """把主链 `_reconstruct` / `_llm_proposal` 填进格式纠错任务（只读消费）。"""
    task = deepcopy(task)
    if task.task_type != TaskType.STRUCTURE_AI_REPAIR:
        return task

    data = table.get("data") or []
    before = deepcopy(data) if isinstance(data, list) else []
    snap = table.get("_reconstruct") or {}
    store = table.get("_llm_proposal") or {}
    status = str(table.get("repair_status") or "")
    cl = table.get("_repair_checklist") or {}

    task.status = TaskStatus.PROPOSED
    task.proposal = dict(task.proposal or {})
    task.proposal["action"] = "ai_structure_repair"
    task.proposal["from_main_chain"] = True
    task.proposal["auto_apply"] = False
    task.proposal["before_rows"] = len(before)
    task.proposal["reconstruct_stage"] = snap.get("stage")
    task.proposal["error_ids"] = list(
        ((cl.get("typed_errors") or []) and [])
        or (store.get("problem_tags") or [])
    )
    # typed error ids if present on checklist
    typed = cl.get("typed_errors")
    if isinstance(typed, list) and typed:
        ids = []
        for e in typed:
            if isinstance(e, dict) and e.get("error_id"):
                ids.append(str(e["error_id"]))
        if ids:
            task.proposal["error_ids"] = ids

    task.evidence = dict(task.evidence or {})
    task.evidence["reconstruct"] = {
        "stage": snap.get("stage"),
        "table_kind": snap.get("table_kind") or (table.get("_table_kind") or {}).get("kind"),
        "checklist_failed_ids": list(snap.get("checklist_failed_ids") or []),
    }

    # 丢数 / human：展示拦截说明
    if status == "human_needed" or snap.get("stage") == "needs_human":
        err = (
            store.get("llm_error")
            or store.get("validation_errors")
            or "主链标为需人工（禁止自动补数或校验未过）"
        )
        if isinstance(err, list):
            err = "; ".join(str(x) for x in err[:6])
        task.proposal["awaiting_llm"] = False
        task.proposal["llm_error"] = str(err)
        task.proposal["blocked_reason"] = str(err)
        task.proposal["before_data"] = deepcopy(store.get("before_data") or before)
        repaired = store.get("repaired_table")
        if repaired:
            task.proposal["repaired_table"] = deepcopy(repaired)
            change = summarize_table_changes(
                task.proposal["before_data"], repaired,
            )
            task.proposal["change_summary"] = change
        task.conservation_ok = False
        task.conservation_detail = str(err)
        task.confidence = Confidence.LOW
        task.reason = f"主链需人工：{str(err)[:80]}"
        return task

    repaired = store.get("repaired_table")
    before_p = store.get("before_data") or before
    if repaired and store.get("success", True) and status in (
        "llm_proposed", "llm_applied", "llm_candidate",
    ):
        change = summarize_table_changes(before_p, repaired)
        task.proposal["awaiting_llm"] = False
        task.proposal["llm_error"] = ""
        task.proposal["repaired_table"] = deepcopy(repaired)
        task.proposal["before_data"] = deepcopy(before_p)
        task.proposal["change_summary"] = change
        task.proposal["reasoning_summary"] = store.get("reasoning_summary") or ""
        task.proposal["report_text"] = store.get("report_text") or ""
        task.proposal["actions"] = list(
            ((cl.get("actions") or [])[:20])
            if isinstance(cl.get("actions"), list)
            else []
        )
        task.evidence["reasoning_summary"] = task.proposal["reasoning_summary"]
        task.evidence["change_summary"] = change
        task.conservation_ok = True
        task.conservation_detail = "主链提案（已校验写入 _llm_proposal）"
        conf = float(store.get("confidence") or 0)
        if conf >= 0.75:
            task.confidence = Confidence.HIGH
        elif conf >= 0.5:
            task.confidence = Confidence.MEDIUM
        else:
            task.confidence = Confidence.MEDIUM
        n_chg = int(change.get("changed_cell_count") or 0)
        task.reason = (
            f"主链提案：改 {n_chg} 格"
            f"（stage={snap.get('stage') or status}）"
        )
        return task

    # 主链仍待 LLM：格式纠错只提示，不在此另起一套 typed_repair
    if status == "llm_candidate" or (
        snap.get("stage") in ("rules_done",) and not repaired
    ):
        task.proposal["awaiting_llm"] = True
        task.proposal["before_data"] = deepcopy(before)
        task.conservation_ok = False
        task.conservation_detail = "主链尚未生成 LLM 提案"
        task.confidence = Confidence.MEDIUM
        task.reason = (
            "主链为 llm_candidate：请勾选「启用 LLM」后重新扫描"
            "（由还原主链生成提案，本 Tab 只审阅）"
        )
        return task

    # 其它：展示当前状态
    task.proposal["awaiting_llm"] = False
    task.proposal["before_data"] = deepcopy(before)
    task.reason = f"主链状态 {status or 'none'} / stage={snap.get('stage')}"
    return task


def propose_structure_ai_repair(
    task: FormatTask,
    table: dict,
    *,
    use_llm: bool = False,
) -> FormatTask:
    """兼容旧名：改为只 hydrate 主链结果。

    use_llm 参数保留但忽略——LLM 是否运行由引擎在扫描前调用
    run_table_reconstruct(run_llm=...) 决定，避免 Tab 侧另起决策。
    """
    _ = use_llm
    return hydrate_structure_task_from_main_chain(task, table)


def apply_structure_ai_tasks(
    tables: List[dict],
    tasks: List[FormatTask],
    *,
    only_auto: bool = False,
    accepted_ids: Optional[Set[str]] = None,
) -> Tuple[List[dict], List[FormatTask], List[str]]:
    """应用已接受的结构 AI 提案。"""
    notes: List[str] = []
    accepted_ids = accepted_ids or set()
    out_tasks: List[FormatTask] = []

    for task in tasks:
        if task.task_type != TaskType.STRUCTURE_AI_REPAIR:
            out_tasks.append(task)
            continue
        prop = task.proposal or {}
        repaired = prop.get("repaired_table")
        should = False
        if only_auto and prop.get("auto_apply") and task.conservation_ok:
            should = True
        if (not only_auto) and task.task_id in accepted_ids:
            should = True
        if not should or not repaired:
            if task.task_id in accepted_ids and not repaired:
                notes.append(f"{task.task_id}: 无 repaired_table，跳过")
            out_tasks.append(task)
            continue

        idx = int(task.table_index)
        if idx < 0 or idx >= len(tables):
            notes.append(f"{task.task_id}: 表索引越界")
            out_tasks.append(task)
            continue

        try:
            from codes.table_repair.human_queue import apply_proposal_to_table

            apply_proposal_to_table(tables[idx], repaired, status="llm_applied")
            task.status = TaskStatus.APPLIED
            notes.append(f"{task.task_id}: 已写回 P{tables[idx].get('page')} 表#{idx}")
        except Exception as exc:
            notes.append(f"{task.task_id}: 写回失败 {exc}")
        out_tasks.append(task)

    return tables, out_tasks, notes
