# -*- coding: utf-8 -*-
"""跨页/相邻表合并（顺序守恒 + 内容守恒）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Optional, Tuple

from .conservation import assert_no_content_loss, merge_tables_preserve, nonempty_multiset
from .models import Confidence, FormatTask, TaskStatus, TaskType


def propose_merge(task: FormatTask, tables: List[dict]) -> FormatTask:
    """为合并任务生成 proposal（含合并后 data 预览元数据，不全量塞进 evidence）。"""
    if task.task_type != TaskType.CROSS_PAGE_MERGE:
        return task
    keep = int(task.proposal.get("keep_index", task.table_index))
    absorb = int(task.proposal.get("absorb_index", (task.related_indices or [-1])[0]))
    if absorb < 0 or keep >= len(tables) or absorb >= len(tables):
        task.status = TaskStatus.BLOCKED
        task.conservation_ok = False
        task.conservation_detail = "索引无效"
        return task

    prev = tables[keep].get("data") or []
    nxt = tables[absorb].get("data") or []
    merged, allowed, skip, note = merge_tables_preserve(prev, nxt)

    # 守恒：before = prev+next 全部内容；after = merged；允许去掉重复表头
    before = list(prev) + list(nxt)
    ok, detail = assert_no_content_loss(before, merged, allowed_remove=allowed)
    task.conservation_ok = ok
    task.conservation_detail = detail if not ok else note
    if not ok:
        task.status = TaskStatus.BLOCKED
        task.confidence = Confidence.UNCERTAIN
        return task

    task.proposal.update(
        {
            "action": "merge",
            "keep_index": keep,
            "absorb_index": absorb,
            "skipped_header_rows": skip,
            "merged_rows": len(merged),
            "merged_cols": max((len(r) for r in merged), default=0),
            "note": note,
            # 实际 data 在 apply 时重算，避免报告过大；这里存校验通过标记
            "conservation_passed": True,
        }
    )
    task.status = TaskStatus.PROPOSED
    return task


def apply_merges(
    tables: List[dict],
    tasks: List[FormatTask],
    *,
    only_auto: bool = False,
    accepted_ids: Optional[set] = None,
) -> Tuple[List[dict], List[FormatTask], List[str]]:
    """应用合并提案，返回新 tables（深拷贝）与更新后的 tasks。

    不改变未参与合并的表的相对顺序；被吸收表标记 `_format_merged_into` 并保留占位
    （data 清空为 [] 会丢内容——改为保留 data 副本在 `_format_merged_snapshot`，
    主数据只留在 keep 表，占位表 data 置空但 snapshot 保全，便于回滚）。
    """
    new_tables = deepcopy(tables)
    notes: List[str] = []
    # 按 absorb 从大到小，避免索引错乱；实际我们保留占位不删元素，索引稳定
    merge_tasks = [
        t for t in tasks
        if t.task_type == TaskType.CROSS_PAGE_MERGE
        and t.status in (TaskStatus.PROPOSED, TaskStatus.CANDIDATE)
        and t.conservation_ok is not False
    ]

    for task in merge_tasks:
        if only_auto and not task.proposal.get("auto_apply"):
            continue
        if accepted_ids is not None and task.task_id not in accepted_ids:
            continue
        if task.confidence == Confidence.LOW and accepted_ids is None and only_auto:
            continue

        # 重新 propose 确保有校验
        task = propose_merge(task, new_tables)
        if task.status == TaskStatus.BLOCKED or not task.conservation_ok:
            notes.append(f"{task.task_id}: 跳过（{task.conservation_detail}）")
            continue

        keep = int(task.proposal["keep_index"])
        absorb = int(task.proposal["absorb_index"])
        if new_tables[absorb].get("_format_merged_into") is not None:
            notes.append(f"{task.task_id}: 后表已被合并，跳过")
            continue
        if new_tables[keep].get("_format_merged_into") is not None:
            notes.append(f"{task.task_id}: 前表已被吸收，跳过")
            continue

        prev = new_tables[keep].get("data") or []
        nxt = new_tables[absorb].get("data") or []
        merged, allowed, skip, note = merge_tables_preserve(prev, nxt)
        ok, detail = assert_no_content_loss(list(prev) + list(nxt), merged, allowed_remove=allowed)
        if not ok:
            task.status = TaskStatus.BLOCKED
            task.conservation_ok = False
            task.conservation_detail = detail
            notes.append(f"{task.task_id}: 守恒失败 {detail}")
            continue

        # 写回 keep
        new_tables[keep]["data"] = merged
        new_tables[keep]["rows"] = len(merged)
        new_tables[keep]["cols"] = max((len(r) for r in merged), default=0)
        pages = sorted({
            int(new_tables[keep].get("page") or 0),
            int(new_tables[absorb].get("page") or 0),
        })
        new_tables[keep]["_cross_page_merged"] = True
        new_tables[keep]["_merged_from_pages"] = pages
        new_tables[keep]["_format_corrector_merged"] = True
        src = list(new_tables[keep].get("merge_source_indices") or [keep])
        if absorb not in src:
            src.append(absorb)
        new_tables[keep]["merge_source_indices"] = src

        # 占位：保全快照，清空显示 data（内容已在 keep；快照防丢）
        new_tables[absorb]["_format_merged_snapshot"] = deepcopy(nxt)
        new_tables[absorb]["_format_merged_into"] = keep
        new_tables[absorb]["data"] = []
        new_tables[absorb]["rows"] = 0
        new_tables[absorb]["_format_hidden"] = True

        task.status = TaskStatus.APPLIED
        task.conservation_ok = True
        task.conservation_detail = note
        notes.append(f"{task.task_id}: 合并表#{absorb}→#{keep}，{note}")

    # 同步 tasks 列表中的同 id 状态
    by_id = {t.task_id: t for t in merge_tasks}
    updated = []
    for t in tasks:
        updated.append(by_id.get(t.task_id, t))
    return new_tables, updated, notes


def compact_hidden_tables(tables: List[dict]) -> List[dict]:
    """可选：去掉 `_format_hidden` 占位表，保持其余相对顺序。

    注意：会改变表索引。默认 engine 不自动调用；仅在用户明确选择「清理占位」时使用。
    被删表的内容必须已在 merge 目标中（由 snapshot + 守恒保证）。
    """
    return [t for t in tables if not t.get("_format_hidden")]
