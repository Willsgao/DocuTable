# -*- coding: utf-8
"""SEC1 十二列 a–l + 行号 + 标签；CR6 等表为暴露类别 + 违约概率区间双左列。"""

from __future__ import annotations

import re
from typing import List, Tuple

from codes.table_engine.geometry.column_anchors import (
    col_index_by_anchor,
    col_index_by_x0,
    is_pd_range_cell_text,
)
from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.layout.base import LayoutContext, LayoutSelection

_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.I)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CR6_CATEGORY_X_MAX = 72.0
_CR6_PD_X_MIN = 72.0


def _detect_cr6_dual_left(items: List[dict], scope_y0: float) -> bool:
    """表头含「暴露类别」+「违约概率区间」→ 双左列（非 序号+标签）。"""
    has_exposure = False
    has_pd_header = False
    for it in items:
        y = float(it.get("y_mid", 0))
        if scope_y0 > 0 and y > scope_y0 + 110:
            break
        t = str(it.get("text", "")).strip()
        x0 = float(it.get("x0", 0))
        if x0 < _CR6_CATEGORY_X_MAX and any(k in t for k in ("暴露", "类别", "风险")):
            has_exposure = True
        if x0 >= _CR6_PD_X_MIN - 8 and any(k in t for k in ("违约概率", "间（%）", "间(%)")):
            has_pd_header = True
    return has_exposure and has_pd_header


def _is_cr6_dual_left_ranges(col_ranges: List[Tuple[float, float]]) -> bool:
    if len(col_ranges) < 3:
        return False
    _, hi0 = col_ranges[0]
    lo1, hi1 = col_ranges[1]
    lo2, _ = col_ranges[2]
    return hi0 <= 85 and lo1 <= 78 and hi1 >= 120 and hi1 < lo2 - 8


def _is_exposure_category_text(text: str, x0: float) -> bool:
    t = str(text or "").strip()
    if not t or is_pd_range_cell_text(t) or is_numeric_data_cell(t):
        return False
    if float(x0) >= _CR6_CATEGORY_X_MAX:
        return False
    return bool(_CJK_RE.search(t)) and len(t) <= 12


class SEC1LayoutPlugin:
    layout_id = "pillar_sec1"

    def score(self, ctx: LayoutContext) -> float:
        sel = self.infer(ctx)
        if not sel or len(sel.col_ranges) < 12:
            return 0.0
        return 0.88

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        letters = [chr(ord("a") + i) for i in range(12)]
        row_letters: List[tuple] = []
        for it in ctx.items:
            t = str(it.get("text", "")).strip().lower()
            if t not in letters:
                continue
            if ctx.scope_y0 > 0 and it.get("y_mid", 0) > ctx.scope_y0 + 80:
                continue
            row_letters.append((float(it.get("x0", 0)), float(it.get("x1", 0)), t))
        if len({t for _, _, t in row_letters}) < 8:
            return None
        row_letters.sort(key=lambda x: x[0])
        data_ranges: List[Tuple[float, float]] = []
        for i, (x0, x1, _) in enumerate(row_letters):
            if i + 1 < len(row_letters):
                x1 = (x1 + row_letters[i + 1][0]) / 2.0
            else:
                x1 = max(x1, x0 + 30)
            if i > 0:
                x0 = data_ranges[-1][1]
            data_ranges.append((x0 - 2, x1 + 2))
        if len(data_ranges) < 10:
            return None

        cr6 = _detect_cr6_dual_left(ctx.items, ctx.scope_y0)
        if cr6:
            col_a_x0 = data_ranges[0][0]
            pd_hi = min(col_a_x0 - 4.0, 158.0)
            left_ranges = [(45.0, _CR6_CATEGORY_X_MAX), (_CR6_PD_X_MIN, pd_hi)]
            left_roles = ["category", "pd_range"]
        else:
            left_ranges = [(60.0, 95.0), (96.0, data_ranges[0][0])]
            left_roles = ["row_no", "label"]

        ranges = left_ranges + data_ranges
        roles = left_roles + [f"col_{c}" for c in letters[: len(data_ranges)]]
        return LayoutSelection(
            layout_id=self.layout_id,
            col_ranges=ranges,
            confidence=0.88,
            roles=roles,
        )

    def col_index_for_item(
        self,
        x0: float,
        x1: float,
        text: str,
        col_ranges: List[Tuple[float, float]],
    ) -> int:
        if not col_ranges:
            return 0
        t = str(text or "").strip()
        cr6 = _is_cr6_dual_left_ranges(col_ranges)

        if cr6:
            if is_pd_range_cell_text(t) and float(x0) < col_ranges[2][0]:
                return 1
            if _is_exposure_category_text(t, x0):
                return 0
            value_start = col_ranges[2][0] - 15
            if is_numeric_data_cell(t) and float(x0) >= value_start:
                return col_index_by_x0(x0, col_ranges)
            return col_index_by_anchor(x0, x1, t, col_ranges)

        if t and _ROW_NUMBER_RE.match(t) and x0 <= col_ranges[0][1] + 10:
            return 0
        if is_numeric_data_cell(t) and x0 >= col_ranges[1][0] - 15:
            return col_index_by_x0(x0, col_ranges)
        return col_index_by_anchor(x0, x1, t, col_ranges)
