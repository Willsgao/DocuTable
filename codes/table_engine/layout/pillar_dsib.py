# -*- coding: utf-8
"""DSIB1 等：一级指标 | （空） | 二级指标 | 指标值（四列，一级类别纵续）。"""

from __future__ import annotations

from typing import List, Tuple

from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.layout.base import LayoutContext, LayoutSelection
from codes.table_engine.layout.generic import GenericLayoutPlugin

# 一级指标 ~x73 | 间隔 | 二级指标 ~x166 | 指标值 ~x467
_LEVEL1_X_MAX = 130.0
_SPACER_X_MAX = 160.0
_VALUE_X_MIN = 400.0
_COL_RANGES = [
    (60.0, _LEVEL1_X_MAX),
    (_LEVEL1_X_MAX, _SPACER_X_MAX),
    (_SPACER_X_MAX, _VALUE_X_MIN),
    (_VALUE_X_MIN, 540.0),
]
_ROLES = ["level1", "spacer", "indicator", "value"]


class DSIBLayoutPlugin:
    layout_id = "pillar_dsib"

    def score(self, ctx: LayoutContext) -> float:
        if _is_dsib_indicator_table(ctx.rows):
            return 0.88
        return 0.0

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        if not _is_dsib_indicator_table(ctx.rows):
            return None
        return LayoutSelection(
            layout_id=self.layout_id,
            col_ranges=list(_COL_RANGES),
            confidence=0.88,
            roles=list(_ROLES),
        )

    def col_index_for_item(
        self,
        x0: float,
        x1: float,
        text: str,
        col_ranges: List[Tuple[float, float]],
    ) -> int:
        t = str(text or "").strip()
        x = float(x0)
        if is_numeric_data_cell(t) or x >= _VALUE_X_MIN - 8:
            return 3
        if x < _LEVEL1_X_MAX:
            return 0
        if x < _SPACER_X_MAX:
            return 1
        if x < _VALUE_X_MIN:
            return 2
        return GenericLayoutPlugin().col_index_for_item(x0, x1, text, col_ranges)


def _is_dsib_indicator_table(rows: List[dict]) -> bool:
    header_joined = ""
    for row in rows[:4]:
        header_joined += "".join(
            str(it.get("text", "")) for it in row.get("items") or []
        )
    if "一级指标" not in header_joined or "二级指标" not in header_joined:
        return False
    if "指标值" not in header_joined:
        return False
    if "序号" in header_joined and "指标类别" in header_joined:
        return False

    indicator_rows = 0
    for row in rows[2:18]:
        items = row.get("items") or []
        if not items:
            continue
        has_level1 = any(
            float(it.get("x0", 0)) < _LEVEL1_X_MAX
            and str(it.get("text", "")).strip()
            and not is_numeric_data_cell(str(it.get("text", "")).strip())
            for it in items
        )
        has_indicator = any(
            _SPACER_X_MAX <= float(it.get("x0", 0)) < _VALUE_X_MIN
            and str(it.get("text", "")).strip()
            for it in items
        )
        has_value = any(
            is_numeric_data_cell(str(it.get("text", "")).strip())
            and float(it.get("x0", 0)) >= _VALUE_X_MIN - 8
            for it in items
        )
        if has_value and (has_indicator or has_level1):
            indicator_rows += 1
    return indicator_rows >= 3
