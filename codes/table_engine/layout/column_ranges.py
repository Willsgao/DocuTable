# -*- coding: utf-8
"""向后兼容薄封装 → layout registry。"""

from __future__ import annotations

from typing import List, Tuple

from codes.table_engine.layout.base import LayoutContext
from codes.table_engine.layout.registry import plugin_for_layout_id, select_layout


def select_column_ranges(
    items: List[dict],
    rows: List[dict],
    table_y0: float,
) -> Tuple[List[Tuple[float, float]], str]:
    ctx = LayoutContext(
        page=0,
        scope_y0=table_y0,
        region_y0=table_y0,
        region_y1=table_y0 + 9999,
        items=items,
        rows=rows,
    )
    sel, _ = select_layout(ctx)
    return sel.col_ranges, sel.layout_id


def col_index_for_item(
    x0: float,
    x1: float,
    text: str,
    col_ranges: List[Tuple[float, float]],
    layout_id: str,
) -> int:
    return plugin_for_layout_id(layout_id).col_index_for_item(
        x0, x1, text, col_ranges
    )
