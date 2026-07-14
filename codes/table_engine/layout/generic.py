# -*- coding: utf-8
"""Generic 列布局（列界优先由数据行 X 聚类反推）。"""

from __future__ import annotations

from typing import List, Tuple

from codes.table_engine.geometry.layout_rows import body_rows_for_layout
from codes.table_engine.layout.base import LayoutContext, LayoutSelection

_COL_MERGE_GAP = 25.0
_X_ASSIGN_TOL = 12.0
_MAX_ITEM_WIDTH_FOR_COL_CLUSTER = 160.0


class GenericLayoutPlugin:
    layout_id = "generic"

    def score(self, ctx: LayoutContext) -> float:
        return 0.1

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
        x_left = float(x0)
        for i, (a, b) in enumerate(col_ranges):
            if a <= x_left <= b:
                return i
        for i, (a, b) in enumerate(col_ranges):
            if a - _X_ASSIGN_TOL <= x_left <= b + _X_ASSIGN_TOL:
                return i
        centers = [(a + b) / 2 for a, b in col_ranges]
        xc = (x0 + x1) / 2
        return min(range(len(centers)), key=lambda i: abs(xc - centers[i]))


def _detect_col_ranges(rows: List[dict]) -> List[Tuple[float, float]]:
    from codes.table_engine.geometry.layout_rows import is_poison_row_for_layout

    bands = _collect_x_bands(body_rows_for_layout(rows))
    if len(bands) < 2:
        bands = _collect_x_bands(
            [r for r in rows if not is_poison_row_for_layout(r)]
        )
    clusters = _cluster_x_bands(bands)
    if len(clusters) >= 2:
        return [(c[0] - 5.0, c[1] + 5.0) for c in clusters]
    return _detect_from_widest_row(rows)


def _collect_x_bands(rows: List[dict]) -> List[Tuple[float, float]]:
    bands: List[Tuple[float, float]] = []
    for row in rows:
        for it in row.get("items", []):
            x0, x1 = float(it.get("x0", 0)), float(it.get("x1", 0))
            if x1 <= x0:
                continue
            if (x1 - x0) > _MAX_ITEM_WIDTH_FOR_COL_CLUSTER:
                continue
            if str(it.get("text", "")).strip():
                bands.append((x0, x1))
    return bands


def _cluster_x_bands(bands: List[Tuple[float, float]]) -> List[List[float]]:
    if not bands:
        return []
    ordered = sorted(bands, key=lambda b: b[0])
    clusters: List[List[float]] = [[ordered[0][0], ordered[0][1]]]
    for x0, x1 in ordered[1:]:
        if x0 > clusters[-1][1] + _COL_MERGE_GAP:
            clusters.append([x0, x1])
        else:
            clusters[-1][0] = min(clusters[-1][0], x0)
            clusters[-1][1] = max(clusters[-1][1], x1)
    return clusters


def _detect_from_widest_row(rows: List[dict]) -> List[Tuple[float, float]]:
    if not rows:
        return [(60.0, 95.0), (96.0, 540.0)]
    best = max(rows, key=lambda r: len(r.get("items", [])))
    ranges: List[Tuple[float, float]] = []
    for it in best.get("items", []):
        x0, x1 = float(it.get("x0", 0)), float(it.get("x1", 0))
        if x1 > x0:
            ranges.append((x0, x1))
    return ranges if len(ranges) >= 2 else [(60.0, 95.0), (96.0, 540.0)]
