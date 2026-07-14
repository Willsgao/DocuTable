# -*- coding: utf-8
"""CC1 四列：行号 | 标签 | 数额 | 代码。"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from codes.table_engine.geometry.column_anchors import col_index_by_anchor, is_report_period_cell
from codes.table_engine.layout.base import LayoutContext, LayoutSelection
from codes.table_engine.layout.pillar_cc2 import CC2LayoutPlugin

_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.IGNORECASE)
_CC1_MARKERS = frozenset({"a", "b", "数额", "代码"})


class CC1LayoutPlugin:
    layout_id = "pillar_cc1"

    def score(self, ctx: LayoutContext) -> float:
        if CC2LayoutPlugin().infer(ctx):
            return 0.0
        markers = _collect_markers(ctx.items)
        if "数额" not in markers:
            return 0.0
        if "代码" not in markers and "b" not in markers:
            return 0.0
        return 0.92

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        if CC2LayoutPlugin().infer(ctx):
            return None
        markers = _collect_markers(ctx.items)
        if "数额" not in markers:
            return None
        if "代码" not in markers and "b" not in markers:
            return None
        amount_x = markers["数额"]
        code_x = markers.get("代码", markers.get("b", 498.0))
        split_ac = code_x - 22.0
        ranges = [
            (60.0, 95.0),
            (96.0, amount_x - 12.0),
            (amount_x - 12.0, split_ac),
            (split_ac, 535.0),
        ]
        return LayoutSelection(
            layout_id=self.layout_id,
            col_ranges=ranges,
            confidence=0.92,
            roles=["row_no", "label", "amount", "code"],
        )

    def col_index_for_item(
        self,
        x0: float,
        x1: float,
        text: str,
        col_ranges: List[Tuple[float, float]],
    ) -> int:
        if len(col_ranges) != 4:
            return _nearest_center(x0, x1, col_ranges)
        t = str(text or "").strip()
        row_num_x1 = col_ranges[0][1]
        code_x0 = col_ranges[3][0]
        amount_x0 = col_ranges[2][0]
        if t and _ROW_NUMBER_RE.match(t) and x0 <= row_num_x1 + 10:
            return 0
        if is_report_period_cell(t):
            return col_index_by_anchor(x0, x1, text, col_ranges)
        if x0 >= code_x0 - 6:
            return 3
        if x0 >= amount_x0 - 8:
            return 2
        if x0 <= row_num_x1 + 12:
            return 0
        return 1


def _collect_markers(items: List[dict]) -> Dict[str, float]:
    markers: Dict[str, float] = {}
    for it in items:
        t = str(it.get("text", "")).strip()
        if t in _CC1_MARKERS:
            markers[t] = float(it.get("x0", 0))
    return markers


def _nearest_center(x0: float, x1: float, col_ranges: List[Tuple[float, float]]) -> int:
    centers = [(a + b) / 2 for a, b in col_ranges]
    xc = (x0 + x1) / 2
    return min(range(len(centers)), key=lambda i: abs(xc - centers[i]))
