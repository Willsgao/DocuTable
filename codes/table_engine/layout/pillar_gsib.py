# -*- coding: utf-8
"""GSIB1 等：序号 | 指标类别 | 指标 | 指标值（四列，类别纵续）。"""

from __future__ import annotations

import re
from typing import List, Tuple

from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.layout.base import LayoutContext, LayoutSelection
from codes.table_engine.layout.generic import GenericLayoutPlugin

_SERIAL_RE = re.compile(r"^\d+[a-z]?$", re.I)

# 序号 | 指标类别 | 指标 | 指标值 — X 分界（来自 GSIB/DSIB 披露表坐标）
_COL_BREAKS = (100.0, 180.0, 400.0)
_COL_RANGES = [
    (60.0, 100.0),
    (100.0, 180.0),
    (180.0, 400.0),
    (400.0, 540.0),
]
_ROLES = ["serial", "category", "indicator", "value"]


class GSIBLayoutPlugin:
    layout_id = "pillar_gsib"

    def score(self, ctx: LayoutContext) -> float:
        if _is_gsib_indicator_table(ctx.rows):
            return 0.87
        return 0.0

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        if not _is_gsib_indicator_table(ctx.rows):
            return None
        return LayoutSelection(
            layout_id=self.layout_id,
            col_ranges=list(_COL_RANGES),
            confidence=0.87,
            roles=list(_ROLES),
        )

    def col_index_for_item(
        self,
        x0: float,
        x1: float,
        text: str,
        col_ranges: List[Tuple[float, float]],
    ) -> int:
        x_left = float(x0)
        for i, (lo, hi) in enumerate(col_ranges):
            if lo <= x_left <= hi:
                return i
        return GenericLayoutPlugin().col_index_for_item(x0, x1, text, col_ranges)


def _is_gsib_indicator_table(rows: List[dict]) -> bool:
    header_joined = ""
    for row in rows[:4]:
        header_joined += "".join(
            str(it.get("text", "")) for it in row.get("items") or []
        )
    if "序号" not in header_joined or "指标类别" not in header_joined:
        return False
    if "指标值" not in header_joined and "指标值1" not in header_joined:
        return False

    serial_rows = 0
    for row in rows[2:16]:
        items = row.get("items") or []
        if not items:
            continue
        has_serial = any(
            _SERIAL_RE.match(str(it.get("text", "")).strip())
            and float(it.get("x0", 0)) < _COL_BREAKS[0]
            for it in items
        )
        has_value = any(
            is_numeric_data_cell(str(it.get("text", "")).strip())
            and float(it.get("x0", 0)) >= _COL_BREAKS[2]
            for it in items
        )
        if has_serial and has_value:
            serial_rows += 1
    return serial_rows >= 3


def gsib_col_index(x0: float) -> int:
    x = float(x0)
    if x < _COL_BREAKS[0]:
        return 0
    if x < _COL_BREAKS[1]:
        return 1
    if x < _COL_BREAKS[2]:
        return 2
    return 3
