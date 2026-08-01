# -*- coding: utf-8 -*-
"""空行/空列与粘连格：对照 liteparse 提案拆分（不做 OCR 纠字）。"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import List, Optional, Tuple

from .conservation import cell_key, nonempty_multiset, normalize_cell
from .liteparse_bridge import region_text_for_table
from .models import Confidence, FormatTask, TaskStatus, TaskType

# 常见粘连：数值% + 后续说明 / 双日期 / 多数值空格分隔
_GLUE_PCT_DESC = re.compile(
    r"^([+\-]?\d[\d,]*(?:\.\d+)?%?)\s*(.+)$"
)
_MULTI_NUM = re.compile(
    r"^([+\-]?\d[\d,]*(?:\.\d+)?%?)\s+([+\-]?\d[\d,]*(?:\.\d+)?%?)(.*)$"
)


def propose_empty_split(task: FormatTask, table: dict, liteparse_data=None) -> FormatTask:
    """为 EMPTY_SPLIT 任务生成拆分/裁空提案。"""
    if task.task_type != TaskType.EMPTY_SPLIT:
        return task

    data = table.get("data") or []
    patches = []

    # 1) 全空列：仅当 liteparse 也不支持该「空列位置有独立字段」时，提案删除空列
    #    删除空列不丢非空内容 → 守恒 OK
    empty_cols = list(task.evidence.get("empty_cols") or [])
    if empty_cols:
        patches.append(
            {
                "action": "drop_empty_columns",
                "cols": empty_cols,
                "note": "删除整列为空的列（不含任何非空单元格）",
            }
        )

    # 2) 连续空行：同样可安全删除（全空）
    empty_ranges = list(task.evidence.get("empty_row_ranges") or [])
    if empty_ranges:
        patches.append(
            {
                "action": "drop_empty_row_ranges",
                "ranges": empty_ranges,
                "note": "删除连续空行区间（行内无非空内容）",
            }
        )

    # 3) 规则粘连拆分：扫描非空格，尝试拆成多段（拼接须还原）
    glue_patches = _scan_glue_cells(data)
    patches.extend(glue_patches)

    if not patches:
        task.status = TaskStatus.CANDIDATE
        task.reason = (task.reason or "") + "（未生成可自动补丁，保留人工对照）"
        return task

    # 干跑守恒
    trial, ok, detail = apply_patches(data, patches)
    task.conservation_ok = ok
    task.conservation_detail = detail
    task.proposal = {
        "action": "patch",
        "patches": patches,
        "trial_rows": len(trial) if trial is not None else 0,
        "auto_apply": ok and task.confidence == Confidence.HIGH and all(
            p["action"] in ("drop_empty_columns", "drop_empty_row_ranges") for p in patches
        ),
    }
    task.status = TaskStatus.PROPOSED if ok else TaskStatus.BLOCKED
    if liteparse_data is not None:
        task.evidence["liteparse_preview"] = (region_text_for_table(liteparse_data, table) or "")[:400]
    return task


def _scan_glue_cells(data: List[List]) -> List[dict]:
    patches = []
    for ri, row in enumerate(data or []):
        for ci, cell in enumerate(row or []):
            s = normalize_cell(cell)
            if not s or len(s) < 4:
                continue
            parts = _try_split_glued(s)
            if not parts or len(parts) < 2:
                continue
            # 守恒：拼接去空白 == 原文去空白
            if cell_key("".join(parts)) != cell_key(s):
                continue
            patches.append(
                {
                    "action": "split_cell_horizontal",
                    "row": ri,
                    "col": ci,
                    "parts": parts,
                    "original": s,
                    "note": "规则拆分粘连单元格（不改 OCR 字）",
                }
            )
    return patches


def _try_split_glued(s: str) -> Optional[List[str]]:
    # 百分比 + 中文说明
    m = _GLUE_PCT_DESC.match(s)
    if m and re.search(r"[\u4e00-\u9fff]", m.group(2)):
        left, right = m.group(1), m.group(2).strip()
        if right:
            return [left, right]
    # 两数值空格分隔
    m2 = _MULTI_NUM.match(s)
    if m2:
        parts = [m2.group(1), m2.group(2)]
        rest = (m2.group(3) or "").strip()
        if rest:
            parts.append(rest)
        return parts
    return None


def apply_patches(
    data: List[List],
    patches: List[dict],
) -> Tuple[Optional[List[List]], bool, str]:
    """应用补丁并做守恒校验。

    粘连拆分：原文整格会被 parts 替换，校验时把「原文 → parts」视为等价替换，
    不要求 after 多重集合仍含粘连整串。
    """
    from collections import Counter

    out = deepcopy(data)
    # 期望多重集合从 before 出发，随拆分替换更新
    expected = nonempty_multiset(data)

    for p in patches:
        action = p.get("action")
        if action == "drop_empty_columns":
            cols = sorted(set(p.get("cols") or []), reverse=True)
            for c in cols:
                for row in out:
                    if c < len(row) and normalize_cell(row[c]):
                        return None, False, f"拒绝删列{c}：存在非空单元格"
                for row in out:
                    if c < len(row):
                        del row[c]
        elif action == "drop_empty_row_ranges":
            to_del = set()
            for start, end in p.get("ranges") or []:
                for ri in range(int(start), int(end) + 1):
                    to_del.add(ri)
            for ri in sorted(to_del, reverse=True):
                if ri < 0 or ri >= len(out):
                    continue
                if any(normalize_cell(c) for c in out[ri]):
                    return None, False, f"拒绝删行{ri}：存在非空单元格"
                del out[ri]
        elif action == "split_cell_horizontal":
            ri, ci = int(p["row"]), int(p["col"])
            parts = list(p.get("parts") or [])
            if ri >= len(out) or ci >= len(out[ri]):
                return None, False, "split 坐标越界"
            original = normalize_cell(out[ri][ci])
            if cell_key(original) != cell_key(p.get("original") or original):
                return None, False, "单元格原文已变，跳过拆分"
            if cell_key("".join(parts)) != cell_key(original):
                return None, False, "拆分结果无法还原原文"
            # 期望集：去掉原文，加入 parts
            ok_key = cell_key(original)
            if expected.get(ok_key, 0) <= 0:
                return None, False, "拆分原文不在期望集合中"
            expected[ok_key] -= 1
            if expected[ok_key] == 0:
                del expected[ok_key]
            for part in parts:
                pk = cell_key(part)
                if pk:
                    expected[pk] += 1

            out[ri][ci] = parts[0]
            insert_at = ci + 1
            for part in parts[1:]:
                for row in out:
                    while len(row) < insert_at:
                        row.append("")
                for rj, row in enumerate(out):
                    row.insert(insert_at, part if rj == ri else "")
                insert_at += 1
        else:
            return None, False, f"未知 action: {action}"

    actual = nonempty_multiset(out)
    lost = {k: v for k, v in (Counter(expected) - Counter(actual)).items() if v > 0}
    if lost:
        return None, False, f"内容丢失: {list(lost.items())[:8]}"
    return out, True, "ok"


def apply_empty_split_tasks(
    tables: List[dict],
    tasks: List[FormatTask],
    *,
    only_auto: bool = False,
    accepted_ids: Optional[set] = None,
    liteparse_data=None,
) -> Tuple[List[dict], List[FormatTask], List[str]]:
    new_tables = deepcopy(tables)
    notes = []
    updated_map = {}
    for task in tasks:
        if task.task_type != TaskType.EMPTY_SPLIT:
            continue
        if only_auto and not (task.proposal or {}).get("auto_apply"):
            continue
        if accepted_ids is not None and task.task_id not in accepted_ids:
            continue
        idx = task.table_index
        if idx < 0 or idx >= len(new_tables):
            continue
        if new_tables[idx].get("_format_hidden"):
            continue
        task = propose_empty_split(task, new_tables[idx], liteparse_data)
        if task.status == TaskStatus.BLOCKED or not task.proposal.get("patches"):
            updated_map[task.task_id] = task
            continue
        if only_auto and not task.proposal.get("auto_apply"):
            updated_map[task.task_id] = task
            continue
        trial, ok, detail = apply_patches(
            new_tables[idx].get("data") or [],
            task.proposal["patches"],
        )
        if not ok or trial is None:
            task.status = TaskStatus.BLOCKED
            task.conservation_ok = False
            task.conservation_detail = detail
            updated_map[task.task_id] = task
            notes.append(f"{task.task_id}: 失败 {detail}")
            continue
        new_tables[idx]["data"] = trial
        new_tables[idx]["rows"] = len(trial)
        new_tables[idx]["cols"] = max((len(r) for r in trial), default=0)
        new_tables[idx]["_format_corrector_split"] = True
        task.status = TaskStatus.APPLIED
        task.conservation_ok = True
        updated_map[task.task_id] = task
        notes.append(f"{task.task_id}: 已应用 {len(task.proposal['patches'])} 个补丁")

    updated = [updated_map.get(t.task_id, t) for t in tasks]
    return new_tables, updated, notes
