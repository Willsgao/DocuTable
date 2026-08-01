# -*- coding: utf-8 -*-
"""文表边界：标记误入表内的叙述行（默认不删除，确认后可移到旁路字段）。"""

from __future__ import annotations

from copy import deepcopy
from typing import List, Optional, Tuple

from .conservation import assert_no_content_loss, normalize_cell
from .models import FormatTask, TaskStatus, TaskType


def apply_text_flags(
    tables: List[dict],
    tasks: List[FormatTask],
    *,
    accepted_ids: Optional[set] = None,
    remove_from_table: bool = False,
) -> Tuple[List[dict], List[FormatTask], List[str]]:
    """对 TEXT_TABLE_SPLIT 任务：默认只写标记；若 remove_from_table 且用户接受，则移到旁路。"""
    new_tables = deepcopy(tables)
    notes = []
    updated_map = {}

    for task in tasks:
        if task.task_type != TaskType.TEXT_TABLE_SPLIT:
            continue
        if accepted_ids is not None and task.task_id not in accepted_ids:
            continue
        idx = task.table_index
        if idx < 0 or idx >= len(new_tables):
            continue
        rows = list((task.proposal or {}).get("rows") or [])
        table = new_tables[idx]
        data = table.get("data") or []
        extracted = list(table.get("_format_extracted_text") or [])

        if not remove_from_table:
            table["_format_text_row_flags"] = rows
            task.status = TaskStatus.APPLIED
            task.proposal["flagged_only"] = True
            updated_map[task.task_id] = task
            notes.append(f"{task.task_id}: 已标记 {len(rows)} 行疑似正文（未删）")
            continue

        # 移出：内容进旁路，表内行替换为空行占位以保行序；再可选压缩空行由用户决定
        before = deepcopy(data)
        for ri in rows:
            if ri < 0 or ri >= len(data):
                continue
            row = data[ri]
            text = " ".join(normalize_cell(c) for c in row if normalize_cell(c))
            if text:
                extracted.append({"row": ri, "text": text})
            data[ri] = [""] * len(row)
        ok, detail = assert_no_content_loss(
            before,
            list(data) + [[x["text"]] for x in extracted],
        )
        # 上面把 extracted 拼进 after 不太对；改为：before 内容应等于 data非空 ∪ extracted
        from collections import Counter
        from .conservation import nonempty_multiset, cell_key

        b = nonempty_multiset(before)
        a = nonempty_multiset(data)
        for x in extracted:
            k = cell_key(x["text"])
            if k:
                a[k] += 1
        lost = {k: b[k] - a.get(k, 0) for k in b if a.get(k, 0) < b[k]}
        if lost:
            task.status = TaskStatus.BLOCKED
            task.conservation_ok = False
            task.conservation_detail = f"移出正文丢失: {list(lost.items())[:5]}"
            updated_map[task.task_id] = task
            notes.append(f"{task.task_id}: {task.conservation_detail}")
            continue

        table["data"] = data
        table["_format_extracted_text"] = extracted
        task.status = TaskStatus.APPLIED
        task.conservation_ok = True
        updated_map[task.task_id] = task
        notes.append(f"{task.task_id}: 已将 {len(rows)} 行正文移至旁路字段（表内留空行占位）")

    updated = [updated_map.get(t.task_id, t) for t in tasks]
    return new_tables, updated, notes
