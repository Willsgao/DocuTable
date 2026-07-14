# -*- coding: utf-8
"""CCRF 六列 a–f + 序号 + 标签（CCR1 等）。"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from codes.table_engine.geometry.column_anchors import col_index_by_x0
from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.layout.base import LayoutContext, LayoutSelection

_CCRF_LETTERS = tuple(chr(ord("a") + i) for i in range(6))
_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.I)
_SERIAL_LABEL_SPLIT = 77.0


class CCRFLayoutPlugin:
    layout_id = "pillar_ccrf"

    def score(self, ctx: LayoutContext) -> float:
        sel = self.infer(ctx)
        if not sel or len(sel.col_ranges) < 8:
            return 0.0
        return 0.91

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        markers = _find_letter_markers(ctx.items, ctx.scope_y0, _CCRF_LETTERS)
        if not all(c in markers for c in _CCRF_LETTERS):
            return None
        extra = _header_letters(ctx.items, ctx.scope_y0)
        if extra - set(_CCRF_LETTERS):
            return None

        row_letters: List[tuple] = []
        for c in _CCRF_LETTERS:
            x0 = markers[c]
            row_letters.append((x0, x0 + 8.0, c))
        row_letters.sort(key=lambda x: x[0])

        ax = markers["a"]
        label_hi = max(130.0, ax - 22.0)
        data_ranges: List[Tuple[float, float]] = []
        for i, (mx, _, _) in enumerate(row_letters):
            if i + 1 < len(row_letters):
                hi = (mx + row_letters[i + 1][0]) / 2.0
            else:
                hi = mx + 35.0
            lo = label_hi if i == 0 else data_ranges[-1][1]
            data_ranges.append((lo, hi + 2.0))

        ranges = [
            (60.0, _SERIAL_LABEL_SPLIT),
            (_SERIAL_LABEL_SPLIT, label_hi),
        ] + data_ranges
        roles = ["row_no", "label"] + [f"col_{c}" for c in _CCRF_LETTERS]
        return LayoutSelection(
            layout_id=self.layout_id,
            col_ranges=ranges,
            confidence=0.91,
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
        row_num_x1 = col_ranges[0][1]
        label_hi = col_ranges[1][1] if len(col_ranges) > 1 else row_num_x1 + 80
        data_start = 2

        if t and _ROW_NUMBER_RE.match(t) and x0 <= row_num_x1 + 8:
            return 0
        if is_numeric_data_cell(t) and x0 >= label_hi - 15:
            return col_index_by_x0(x0, col_ranges)
        for ci, (lo, hi) in enumerate(col_ranges[:data_start]):
            slack = 6.0 if ci == 0 else 0.0
            if lo - 4 <= x0 <= hi + slack:
                return ci
        if x0 < col_ranges[data_start][0] - 4:
            return 1 if len(col_ranges) > 1 else 0
        return _nearest_data_col(x0, col_ranges, data_start)


def _nearest_data_col(
    x0: float,
    col_ranges: List[Tuple[float, float]],
    data_start: int,
) -> int:
    best_i = data_start
    best_dist = float("inf")
    for ci in range(data_start, len(col_ranges)):
        lo, hi = col_ranges[ci]
        mid = (lo + hi) / 2.0
        dist = abs(x0 - mid)
        if dist < best_dist:
            best_dist = dist
            best_i = ci
    return best_i


def _find_letter_markers(
    items: List[dict],
    table_y0: float,
    letters: Tuple[str, ...],
    upward_pt: float = 120.0,
) -> Dict[str, float]:
    markers: Dict[str, float] = {}
    letter_set = set(letters)
    for it in items:
        cy = float(it.get("y_mid", 0))
        if table_y0 > 0 and cy > table_y0 + 80:
            continue
        if table_y0 > 0 and cy < table_y0 - upward_pt:
            continue
        t = str(it.get("text", "")).strip().lower()
        if t in letter_set:
            markers[t] = float(it.get("x0", 0))
    return markers


def _header_letters(items: List[dict], table_y0: float) -> set:
    letters: set = set()
    for it in items:
        cy = float(it.get("y_mid", 0))
        if table_y0 > 0 and cy > table_y0 + 80:
            continue
        if table_y0 > 0 and cy < table_y0 - 120:
            continue
        t = str(it.get("text", "")).strip().lower()
        if len(t) == 1 and t.isalpha():
            letters.add(t)
    return letters
