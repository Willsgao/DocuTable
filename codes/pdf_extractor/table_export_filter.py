# -*- coding: utf-8 -*-
"""导出用「是否算表」判定（与质检 is_real_table / rejected 脱钩）。

标准（产品口径）：
- 二维网格：至少 2 行且至少 2 列
- 至少有一个数值单元格（即至少一行或一列上出现数值）
- 文本段落字符串、页眉页脚文案不导出
"""
from __future__ import annotations

import re
from typing import Any, List, Sequence

_NUM_RE = re.compile(
    r"^[\(（]?\s*-?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*[\)）]?\s*%?$"
)


def cell_looks_numeric(val: Any) -> bool:
    if val is None:
        return False
    s = str(val).strip().replace(" ", "").replace(",", "")
    if not s or s in ("-", "–", "—", "－"):
        return False
    s = s.rstrip("%").strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    if s.startswith("（") and s.endswith("）"):
        s = s[1:-1]
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return bool(_NUM_RE.match(str(val).strip().replace(" ", "")))


def as_grid(data: Any) -> List[List[Any]]:
    """规范为 list[list]；字符串整体不是表。"""
    if data is None or isinstance(data, str):
        return []
    if not isinstance(data, list) or not data:
        return []
    grid: List[List[Any]] = []
    for row in data:
        if isinstance(row, (list, tuple)):
            grid.append(list(row))
        elif isinstance(row, str):
            # 单行字符串视为一格；堆成「一字一行」时列数=1，后面会被挡掉
            grid.append([row])
        else:
            grid.append([row])
    return grid


def grid_has_numeric(grid: Sequence[Sequence[Any]]) -> bool:
    for row in grid:
        for cell in row:
            if cell_looks_numeric(cell):
                return True
    return False


def is_exportable_table(t: dict) -> bool:
    """可导出：多行多列二维表，且至少有一处数值。不看 is_real_table/rejected。"""
    if not t:
        return False
    if t.get("type") in ("text", "paragraph"):
        return False
    if t.get("text_role") in ("page_header", "page_footer"):
        return False
    cat = str(t.get("table_category") or "")
    if cat in ("文本段落", "页眉", "页脚"):
        return False

    data = t.get("data")
    if isinstance(data, str):
        return False

    grid = as_grid(data)
    if not grid:
        return False
    n_rows = len(grid)
    n_cols = max((len(r) for r in grid), default=0)
    if n_rows < 2 or n_cols < 2:
        return False
    return grid_has_numeric(grid)
