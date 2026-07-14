# -*- coding: utf-8
"""披露表 Layout：KM1/OV1 等多期多列指标表。"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from codes.table_engine.geometry.column_anchors import (
    col_index_by_anchor,
    infer_numeric_data_column_splits,
)
from codes.table_engine.layout.base import LayoutContext, LayoutSelection
from codes.table_engine.scope.header_scope import is_rmb_unit_lead_row

_DISCLOSURE_MARKERS = frozenset({"a", "b", "c", "d"})
_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.I)


def _header_y_anchor(ctx: LayoutContext) -> float:
    """表头检索锚点：scope 上扩很远时用 region 顶，避免漏检表头带。"""
    if ctx.region_y0 > 0 and ctx.scope_y0 < ctx.region_y0 - 40:
        return ctx.region_y0
    return ctx.scope_y0


class DisclosureLayoutPlugin:
    layout_id = "pillar_disclosure"

    def score(self, ctx: LayoutContext) -> float:
        y0 = _header_y_anchor(ctx)
        markers = _find_abcd_markers(ctx.items, y0)
        if len(markers) >= 4:
            return 0.88
        if set(markers.keys()) == {"a", "b"}:
            return 0.84
        if _is_ov1_style_header(ctx.items, y0):
            return 0.91
        if _has_rmb_unit_and_periods(ctx.items, y0):
            return 0.86
        return 0.0

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        y0 = _header_y_anchor(ctx)
        markers = _find_abcd_markers(ctx.items, y0)
        if len(markers) >= 4:
            ax, bx, cx, dx = (
                markers["a"],
                markers["b"],
                markers["c"],
                markers["d"],
            )
            split_ab = (ax + bx) / 2.0
            split_bc = (bx + cx) / 2.0
            split_cd = (cx + dx) / 2.0
            label_hi = max(120.0, min(ax - 100.0, 210.0))
            ranges = [
                (60.0, 95.0),
                (96.0, label_hi),
                (label_hi, split_ab),
                (split_ab, split_bc),
                (split_bc, split_cd),
                (split_cd, 540.0),
            ]
            roles = ["row_no", "label", "col_a", "col_b", "col_c", "col_d"]
            return LayoutSelection(
                layout_id=self.layout_id,
                col_ranges=ranges,
                confidence=0.88,
                roles=roles,
            )

        ab_two = _infer_ab_two_col_ranges(markers)
        if ab_two:
            return LayoutSelection(
                layout_id=self.layout_id,
                col_ranges=ab_two,
                confidence=0.84,
                roles=["row_no", "label", "col_a", "col_b"],
            )

        ov1 = _infer_ov1_ranges(ctx.items, y0, ctx.rows)
        if ov1:
            return LayoutSelection(
                layout_id=self.layout_id,
                col_ranges=ov1,
                confidence=0.91,
                roles=["row_no", "label", "col_a", "col_b", "col_c"],
            )

        period = _infer_period_grid_ranges(ctx.items, y0)
        if period:
            return LayoutSelection(
                layout_id=self.layout_id,
                col_ranges=period,
                confidence=0.86,
                roles=["row_no", "label", "period_1", "period_2", "period_3", "period_4"],
            )
        return None

    def col_index_for_item(
        self,
        x0: float,
        x1: float,
        text: str,
        col_ranges: List[Tuple[float, float]],
    ) -> int:
        t = str(text or "").strip()
        if col_ranges and t and _ROW_NUMBER_RE.match(t) and x0 <= col_ranges[0][1] + 10:
            return 0
        return col_index_by_anchor(x0, x1, text, col_ranges)


def _find_abcd_markers(
    items: List[dict],
    table_y0: float,
    upward_pt: float = 120.0,
) -> Dict[str, float]:
    markers: Dict[str, float] = {}
    for it in items:
        cy = it.get("y_mid", 0)
        if table_y0 > 0 and cy > table_y0 + 25:
            continue
        if table_y0 > 0 and cy < table_y0 - upward_pt:
            continue
        t = str(it.get("text", "")).strip().lower()
        if t in _DISCLOSURE_MARKERS:
            markers[t] = float(it.get("x0", 0))
    return markers


def _infer_ab_two_col_ranges(markers: Dict[str, float]) -> List[Tuple[float, float]] | None:
    """LIQ/IRRBB 等：序号 | 标签 | a | b 四列。"""
    if set(markers.keys()) != {"a", "b"}:
        return None
    ax = markers["a"]
    bx = markers["b"]
    split_ab = (ax + bx) / 2.0
    label_hi = max(96.0, min(ax - 70.0, 260.0))
    return [
        (60.0, 95.0),
        (96.0, label_hi),
        (label_hi, split_ab),
        (split_ab, 540.0),
    ]


def _has_rmb_unit_and_periods(items: List[dict], table_y0: float) -> bool:
    header_items = [
        it for it in items
        if table_y0 <= 0 or table_y0 - 120 <= it.get("y_mid", 0) <= table_y0 + 40
    ]
    rows_by_y: Dict[float, List[dict]] = {}
    for it in header_items:
        y = round(float(it.get("y_mid", 0)), 1)
        rows_by_y.setdefault(y, []).append(it)
    for row_items in rows_by_y.values():
        cells = [
            str(it.get("text", "")).strip()
            for it in sorted(row_items, key=lambda x: x.get("x0", 0))
            if str(it.get("text", "")).strip()
        ]
        if is_rmb_unit_lead_row(cells):
            return True
    return False


def _is_ov1_style_header(items: List[dict], table_y0: float) -> bool:
    joined = " ".join(
        str(it.get("text", ""))
        for it in items
        if table_y0 <= 0 or table_y0 - 120 <= it.get("y_mid", 0) <= table_y0 + 50
    )
    return "风险加权资产" in joined and ("最低资本" in joined or "资本要求" in joined)


def _infer_ov1_ranges(
    items: List[dict],
    table_y0: float,
    rows: List[dict] | None = None,
) -> List[Tuple[float, float]] | None:
    if not _is_ov1_style_header(items, table_y0):
        return None

    splits = infer_numeric_data_column_splits(rows or [], min_clusters=3)
    if splits and len(splits) >= 2:
        s0, s1 = splits[0], splits[1]
        return [
            (60.0, 95.0),
            (96.0, 200.0),
            (200.0, s0),
            (s0, s1),
            (s1, 540.0),
        ]

    return [
        (60.0, 95.0),
        (96.0, 200.0),
        (200.0, 374.0),
        (374.0, 454.0),
        (454.0, 540.0),
    ]


def _infer_period_grid_ranges(
    items: List[dict],
    table_y0: float,
) -> List[Tuple[float, float]] | None:
    xs: List[float] = []
    for it in items:
        cy = it.get("y_mid", 0)
        if table_y0 > 0 and not (table_y0 - 120 <= cy <= table_y0 + 40):
            continue
        t = str(it.get("text", "")).strip()
        if re.search(r"(?:19|20)\d{2}", t) or "月" in t and "日" in t:
            xs.append(float(it.get("x0", 0)))
    if len(xs) < 3:
        return None
    xs = sorted(set(xs))
    if len(xs) >= 4:
        splits = [95.0] + [(xs[i] + xs[i + 1]) / 2 for i in range(len(xs) - 1)] + [540.0]
        ranges = [(splits[i], splits[i + 1]) for i in range(min(6, len(splits) - 1))]
        if len(ranges) >= 5:
            return ranges[:6]
    return [
        (60.0, 95.0),
        (96.0, 210.0),
        (210.0, 310.0),
        (310.0, 410.0),
        (410.0, 510.0),
        (510.0, 540.0),
    ]
