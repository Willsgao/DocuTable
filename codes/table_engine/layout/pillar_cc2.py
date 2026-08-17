# -*- coding: utf-8
"""CC2 五列：行号 | 标签 | a | b | 代码。"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from codes.table_engine.geometry.column_anchors import col_index_by_anchor, col_index_by_x0
from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.layout.base import LayoutContext, LayoutSelection

_CC2_MARKERS = frozenset({"a", "b", "c"})
_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.I)
# 表内分组标题：几何落在序号带，须进 row_no 列而非项目列
_SECTION_LEAD_LABELS = frozenset({"资产", "负债", "股东权益"})


class CC2LayoutPlugin:
    layout_id = "pillar_cc2"

    def score(self, ctx: LayoutContext) -> float:
        markers = _find_abc_markers(ctx.items, ctx.scope_y0)
        if not {"a", "b", "c"}.issubset(markers.keys()):
            return 0.0
        extra = _header_letters(ctx.items, ctx.scope_y0)
        if extra - {"a", "b", "c"}:
            return 0.0
        return 0.9

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        markers = _find_abc_markers(ctx.items, ctx.scope_y0)
        if not {"a", "b", "c"}.issubset(markers.keys()):
            return None
        ax, bx, cx = markers["a"], markers["b"], markers["c"]
        split_ab = (ax + bx) / 2.0
        label_hi = max(120.0, min(ax - 100.0, 210.0))
        ranges = [
            (60.0, 95.0),
            (96.0, label_hi),
            (label_hi, split_ab),
            (split_ab, cx - 12.0),
            (cx - 12.0, 540.0),
        ]
        return LayoutSelection(
            layout_id=self.layout_id,
            col_ranges=ranges,
            confidence=0.9,
            roles=["row_no", "label", "col_a", "col_b", "code"],
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
        code_x0 = col_ranges[-1][0]
        label_hi = col_ranges[1][1] if len(col_ranges) > 1 else row_num_x1 + 120

        if t and _ROW_NUMBER_RE.match(t) and x0 <= row_num_x1 + 10:
            return 0
        if t in ("代码",) and x0 >= code_x0 - 30:
            return len(col_ranges) - 1
        if is_numeric_data_cell(t) and x0 >= label_hi - 15:
            if len(t) <= 2 and t.isalpha() and x0 >= code_x0 - 20:
                return len(col_ranges) - 1
            # 金额列用 x0 落列，避免 x1 右缘跨 split_ab 误落 b 列
            return col_index_by_x0(x0, col_ranges)
        if len(t) <= 2 and t in ("a", "b", "c") and x0 >= code_x0 - 20:
            return len(col_ranges) - 1
        # 节标题「资产/负债」左缘在序号带 → 序号列（勿进项目列）
        if t in _SECTION_LEAD_LABELS and x0 <= row_num_x1 + 2.0:
            return 0
        # 按 x0 落列。序号列右缘必须收紧：旧 hi+6 把 x0≈100 的科目吞进 col0，
        # 再经空列左移后变成「项目|序号」颠倒。
        for ci, (lo, hi) in enumerate(col_ranges):
            if ci == 0:
                hi_slack = hi + 1.0
            elif ci < len(col_ranges) - 1:
                hi_slack = hi + 6.0
            else:
                hi_slack = hi + 2.0
            if lo - 4 <= x0 <= hi_slack:
                return ci
        return _nearest_center(x0, x1, col_ranges)


def _find_abc_markers(
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
        t = str(it.get("text", "")).strip()
        if t in _CC2_MARKERS:
            markers[t] = float(it.get("x0", 0))
    return markers


def _header_letters(items: List[dict], table_y0: float) -> set:
    letters = set()
    for it in items:
        cy = it.get("y_mid", 0)
        if table_y0 > 0 and cy > table_y0 + 25:
            continue
        if table_y0 > 0 and cy < table_y0 - 120:
            continue
        t = str(it.get("text", "")).strip().lower()
        if len(t) == 1 and t.isalpha():
            letters.add(t)
    return letters


def _nearest_center(x0: float, x1: float, col_ranges: List[Tuple[float, float]]) -> int:
    centers = [(a + b) / 2 for a, b in col_ranges]
    xc = (x0 + x1) / 2
    return min(range(len(centers)), key=lambda i: abs(xc - centers[i]))
