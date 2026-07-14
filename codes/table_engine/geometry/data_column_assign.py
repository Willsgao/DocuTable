# -*- coding: utf-8 -*-
"""数值/破折号落列唯一入口：右对齐数值看 x1，破折号看 x0 并入邻列。"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from codes.table_engine.geometry.column_anchors import col_index_by_anchor, col_index_by_x1
from codes.table_engine.geometry.numeric import is_numeric_data_cell

_DASH_VALUES = frozenset(("-", "－", "—", "–"))
_RISK_WEIGHT_LABEL_RE = re.compile(
    r"^(?:低于\s*)?[\d,]+(?:[-–][\d,]+)?%$",
    re.I,
)
_SIMPLE_PCT_LABEL_RE = re.compile(r"^\d+%$")


def is_row_label_zone_item(
    it: dict,
    col_ranges: List[Tuple[float, float]],
    *,
    value_cols: Optional[List[int]] = None,
) -> bool:
    """左侧标签带内的风险权重行标签（含 1250%、75%），非数据列数值。"""
    t = str(it.get("text", "")).strip()
    if not t:
        return False
    x0 = float(it.get("x0", 0))
    if len(col_ranges) < 2:
        return False
    value_lo = (
        col_ranges[value_cols[0]][0]
        if value_cols
        else (col_ranges[2][0] if len(col_ranges) > 2 else col_ranges[-1][0])
    )
    if x0 >= value_lo - 15:
        return False
    if _RISK_WEIGHT_LABEL_RE.match(t):
        return True
    if _SIMPLE_PCT_LABEL_RE.match(t):
        return True
    return False


def is_data_value_item(text: str) -> bool:
    t = str(text or "").strip()
    return bool(t) and (is_numeric_data_cell(t) or t in _DASH_VALUES)


def assign_data_value_column(
    it: dict,
    col_ranges: List[Tuple[float, float]],
    *,
    layout_id: str = "",
    value_cols: Optional[List[int]] = None,
) -> int:
    """数值与占位破折号的权威落列（pillar / 普通路径均须与此一致）。"""
    x0 = float(it.get("x0", 0))
    x1 = float(it.get("x1", 0))
    text = str(it.get("text", "")).strip()
    n = len(col_ranges)
    if n == 0:
        return 0

    if text in _DASH_VALUES and value_cols:
        for ci in value_cols:
            lo, hi = col_ranges[ci]
            if lo - 4 <= x0 <= hi + 6:
                return ci
        return value_cols[-1]

    if is_data_value_item(text) and not is_row_label_zone_item(
        it, col_ranges, value_cols=value_cols,
    ):
        ci = col_index_by_x1(x1, col_ranges)
        if value_cols and ci not in value_cols:
            ci = min(
                value_cols,
                key=lambda i: abs(
                    x1 - col_ranges[i][1]
                ),
            )
        return ci

    if value_cols:
        ci = col_index_by_anchor(x0, x1, text, col_ranges)
        if ci in value_cols:
            return ci
        sub = [col_ranges[i] for i in value_cols]
        return value_cols[col_index_by_anchor(x0, x1, text, sub)]

    return col_index_by_anchor(x0, x1, text, col_ranges)


def reconcile_col_items_by_anchor(
    col_items: List[List[dict]],
    col_ranges: List[Tuple[float, float]],
    *,
    layout_id: str = "",
    value_cols: Optional[List[int]] = None,
) -> List[List[dict]]:
    """落列后校验：所有数值项强制按 anchor 归位，标签项保持原列。"""
    n = len(col_ranges)
    out: List[List[dict]] = [[] for _ in range(n)]

    for ci, items in enumerate(col_items):
        for it in items:
            text = str(it.get("text", "")).strip()
            if is_data_value_item(text) and not is_row_label_zone_item(
                it, col_ranges, value_cols=value_cols,
            ):
                target = assign_data_value_column(
                    it, col_ranges, layout_id=layout_id, value_cols=value_cols,
                )
            else:
                target = ci
            if 0 <= target < n:
                out[target].append(it)

    return out


def row_value_anchor_conflicts(
    col_items: List[List[dict]],
    col_ranges: List[Tuple[float, float]],
    *,
    layout_id: str = "",
    value_cols: Optional[List[int]] = None,
) -> bool:
    """当前落列与 anchor 不一致，或单格内多数值跨沟道。"""
    for ci, items in enumerate(col_items):
        value_items = [
            it for it in items if is_data_value_item(str(it.get("text", "")).strip())
        ]
        if not value_items:
            continue
        anchor_cols = {
            assign_data_value_column(
                it, col_ranges, layout_id=layout_id, value_cols=value_cols,
            )
            for it in value_items
        }
        if len(anchor_cols) > 1 and len(value_items) > 1 and ci in {
            ci2 for ci2, its in enumerate(col_items)
            if len([x for x in its if is_data_value_item(str(x.get("text", "")).strip())]) > 1
        }:
            return True
        for it in value_items:
            anchor_ci = assign_data_value_column(
                it, col_ranges, layout_id=layout_id, value_cols=value_cols,
            )
            if anchor_ci != ci:
                return True
    return False
