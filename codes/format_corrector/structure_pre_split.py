# -*- coding: utf-8 -*-
"""在格式纠错扫描前，用 Table Engine 同一套结构拆分逻辑切开粘连表。

格式纠错本身不重建 PDF，但应对当前 data[][] 复用
`find_structure_break_row`，避免「前表」里仍夹着（五）/重复表头。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple

from codes.table_engine.split.structure_split import find_structure_break_row


def _slice_data(data: List[List], start: int, end: int = None) -> List[List]:
    chunk = data[start:end] if end is not None else data[start:]
    return [list(row) for row in chunk]


def split_table_data_by_structure(data: List[List]) -> List[List[List]]:
    """递归按结构切点拆分一张表的 data，返回 1..N 段。"""
    if not data or len(data) < 4:
        return [data]

    parts: List[List[List]] = []
    remaining = [list(r) for r in data]
    guard = 0
    while remaining and guard < 32:
        guard += 1
        br = find_structure_break_row(remaining)
        if br < 2 or br >= len(remaining) - 1:
            parts.append(remaining)
            break
        left = _slice_data(remaining, 0, br)
        right = _slice_data(remaining, br)
        if not left or not right:
            parts.append(remaining)
            break
        parts.append(left)
        remaining = right
    else:
        if remaining:
            parts.append(remaining)
    return parts if parts else [data]


def expand_tables_with_structure_split(
    tables: List[dict],
) -> Tuple[List[dict], List[str]]:
    """对每张真表做结构拆分，插入为多张表（保持文档顺序）。

    Returns:
        (new_tables, notes)
    """
    out: List[dict] = []
    notes: List[str] = []
    for idx, table in enumerate(tables or []):
        data = table.get("data")
        if not isinstance(data, list) or not data:
            out.append(deepcopy(table))
            continue
        if table.get("type") in ("text", "paragraph"):
            out.append(deepcopy(table))
            continue

        parts = split_table_data_by_structure(data)
        if len(parts) <= 1:
            out.append(deepcopy(table))
            continue

        page = table.get("page", 0)
        notes.append(
            f"表#{idx} P{page}: 结构拆分为 {len(parts)} 段 "
            f"（小节/重复表头等，复用 TE find_structure_break_row）"
        )
        for pi, part in enumerate(parts):
            t = deepcopy(table)
            t["data"] = part
            t["rows"] = len(part)
            t["cols"] = max((len(r) for r in part), default=0)
            t["_format_structure_split"] = True
            t["_format_structure_split_from"] = idx
            t["_format_structure_split_part"] = pi
            # 仅第一段保留缺表头等「前表」语义；后段若以表头开头则清掉 header_missing
            if pi > 0:
                anomaly = dict(t.get("_anomaly") or {})
                if anomaly.get("header_missing"):
                    # 后段若首行像表头，不再标缺表头
                    from codes.table_engine.scope.header_scope import (
                        is_annual_report_column_header_row,
                    )
                    from codes.format_corrector.conservation import looks_like_header_row

                    head = part[0] if part else []
                    cells = [str(c).strip() for c in head if str(c).strip()]
                    if (
                        looks_like_header_row(head)
                        or is_annual_report_column_header_row(cells)
                        or (cells and str(cells[0]).startswith(("（", "(")))
                    ):
                        anomaly["header_missing"] = False
                        t["_anomaly"] = anomaly
                        if t.get("table_category") == "数据表(缺表头)":
                            t["table_category"] = "财务数据表"
            out.append(t)
    return out, notes
