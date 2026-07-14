# -*- coding: utf-8
"""约束网格布局：列界由 grid_infer 提供，item 落列用几何中心。"""

from __future__ import annotations

from typing import List, Tuple

from codes.table_engine.geometry.column_anchors import col_index_by_anchor
from codes.table_engine.layout.base import LayoutContext, LayoutSelection
from codes.table_engine.layout.generic import _detect_col_ranges


class ConstraintGridLayoutPlugin:
    layout_id = "constraint_grid"

    def score(self, ctx: LayoutContext) -> float:
        return 0.2

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        ranges = _detect_col_ranges(ctx.rows)
        return LayoutSelection(
            layout_id=self.layout_id,
            col_ranges=ranges,
            confidence=0.5,
        )

    def col_index_for_item(
        self,
        x0: float,
        x1: float,
        text: str,
        col_ranges: List[Tuple[float, float]],
    ) -> int:
        return col_index_by_anchor(x0, x1, text, col_ranges)
