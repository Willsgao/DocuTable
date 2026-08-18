# -*- coding: utf-8
"""列 profile + 行内角色约束：按整列 x 带与行结构落格，避免单 item 中心误判。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from codes.table_engine.geometry.column_anchors import (
    col_index_by_anchor,
    is_pd_range_cell_text,
    is_report_period_cell,
)
from codes.table_engine.geometry.data_column_assign import assign_data_value_column
from codes.table_engine.geometry.layout_rows import body_rows_for_layout
from codes.table_engine.geometry.numeric import is_numeric_data_cell, is_year_cell
from codes.table_engine.geometry.row_refiner import LayoutAnchors, estimate_layout_anchors

_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.I)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CODE_LETTERS = frozenset({"a", "b", "c", "d", "e"})
_PILLAR_LAYOUTS = frozenset({
    "pillar_cc1",
    "pillar_cc2",
    "pillar_ccrf",
    "pillar_sec1",
    "pillar_disclosure",
    "pillar_dsib",
    "pillar_gsib",
})
_VALUE_LAYOUT_ROLES = frozenset({"amount", "col_a", "col_b", "col_c", "col_d"})


def _normalize_layout_role(role: str) -> str:
    r = str(role or "").strip()
    if (
        r in _VALUE_LAYOUT_ROLES
        or re.match(r"^col_[a-z]$", r)
        or re.match(r"^period_\d+$", r)
    ):
        return "value"
    if r in ("row_no", "label", "code", "value", "header", "category", "pd_range", "level1", "indicator"):
        return r
    return "header"


@dataclass(frozen=True)
class ColumnProfile:
    col_index: int
    role: str
    x_lo: float
    x_hi: float


def uses_pillar_row_assignment(layout_id: str) -> bool:
    return layout_id in _PILLAR_LAYOUTS


def infer_column_profiles(
    rows: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
    layout_id: str,
    anchors: LayoutAnchors,
    *,
    layout_roles: Sequence[str] | None = None,
) -> List[ColumnProfile]:
    """从列界 + 表体投票推断每列角色（序号/标签/数值/代码）。"""
    if layout_roles and len(layout_roles) == len(col_ranges):
        return [
            ColumnProfile(i, _normalize_layout_role(role), lo, hi)
            for i, ((lo, hi), role) in enumerate(zip(col_ranges, layout_roles))
        ]
    n = len(col_ranges)
    serial_votes = [0] * n
    label_votes = [0] * n
    value_votes = [0] * n
    code_votes = [0] * n

    scan_rows = body_rows_for_layout(list(rows)) or list(rows)
    for row in scan_rows:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t:
                continue
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", 0))
            if _is_serial_item(it, anchors):
                ci = _serial_col_index(x0, col_ranges, anchors)
                serial_votes[ci] += 1
            elif _is_value_item(it, anchors):
                ci = col_index_by_anchor(x0, x1, t, col_ranges)
                if 0 <= ci < n:
                    value_votes[ci] += 1
            elif _is_code_item(
                it, anchors, layout_id, code_x0=col_ranges[-1][0],
            ):
                ci = col_index_by_anchor(x0, x1, t, col_ranges)
                if 0 <= ci < n:
                    code_votes[ci] += 1
            elif _is_label_item(it, anchors):
                ci = _label_col_index(x0, col_ranges, anchors)
                label_votes[ci] += 1

    row_no_col = _pick_peak_col(serial_votes, default=_serial_col_index(80.0, col_ranges, anchors))
    label_col = _pick_label_col(col_ranges, anchors, label_votes, fallback=row_no_col + 1 if n > 1 else 0)
    code_col = _pick_peak_col(code_votes, default=n - 1)
    if layout_id == "pillar_cc1" and n >= 4:
        code_col = min(3, n - 1)

    profiles: List[ColumnProfile] = []
    for i, (lo, hi) in enumerate(col_ranges):
        if i == row_no_col:
            role = "row_no"
        elif i == label_col:
            role = "label"
        elif i == code_col and layout_id in ("pillar_cc1", "pillar_cc2"):
            role = "code"
        elif value_votes[i] > 0 or lo >= anchors.value_x_min - 25:
            role = "value"
        else:
            role = "header"
        profiles.append(ColumnProfile(i, role, lo, hi))
    return profiles


def assign_pillar_row_to_columns(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
    anchors: LayoutAnchors,
    layout_id: str,
    profiles: Sequence[ColumnProfile],
) -> List[List[dict]]:
    """单行：先判角色，再按列 profile / 数值锚点落格。"""
    n = len(col_ranges)
    col_items: List[List[dict]] = [[] for _ in range(n)]
    if not row_items:
        return col_items

    row_no_col = next((p.col_index for p in profiles if p.role == "row_no"), 0)
    label_col = next((p.col_index for p in profiles if p.role == "label"), min(1, n - 1))
    category_col = next((p.col_index for p in profiles if p.role == "category"), None)
    pd_col = next((p.col_index for p in profiles if p.role == "pd_range"), None)
    code_col = next(
        (p.col_index for p in profiles if p.role == "code"),
        n - 1,
    )
    code_x0 = col_ranges[code_col][0]
    value_cols = [p.col_index for p in profiles if p.role == "value"]
    if not value_cols:
        value_cols = [
            i for i, (lo, _) in enumerate(col_ranges)
            if lo >= anchors.value_x_min - 25
        ]

    for it in sorted(row_items, key=lambda x: float(x.get("x0", 0))):
        t = str(it.get("text", "")).strip()
        if not t:
            continue
        x0 = float(it.get("x0", 0))
        x1 = float(it.get("x1", 0))

        if _is_serial_item(it, anchors):
            col_items[row_no_col].append(it)
            continue
        if pd_col is not None and is_pd_range_cell_text(t) and float(x0) < col_ranges[2][0]:
            col_items[pd_col].append(it)
            continue
        if category_col is not None and _is_exposure_category_item(it, col_ranges, category_col):
            col_items[category_col].append(it)
            continue
        if _is_code_item(it, anchors, layout_id, code_x0=code_x0):
            col_items[code_col].append(it)
            continue
        if _is_value_item(it, anchors):
            ci = _assign_value_col(it, col_ranges, value_cols)
            col_items[ci].append(it)
            continue
        if _is_label_item(it, anchors):
            col_items[label_col].append(it)
            continue
        if is_report_period_cell(t) or is_year_cell(t):
            ci = col_index_by_anchor(x0, x1, t, col_ranges)
        else:
            ci = _fallback_col(it, col_ranges, label_col)
        if 0 <= ci < n:
            col_items[ci].append(it)

    return col_items


def row_uses_pillar_assignment(
    row: dict,
    anchors: LayoutAnchors,
) -> bool:
    """仅对表体签名行做 profile 落列；表头/节标题仍走逐 item 规则。"""
    if row.get("row_phase") == "header":
        return False
    items = row.get("items") or []
    if not items:
        return False
    has_serial = any(_is_serial_item(it, anchors) for it in items)
    has_value = any(_is_value_item(it, anchors) for it in items)
    has_label = any(_is_label_item(it, anchors) for it in items)
    if has_serial and (has_value or has_label):
        return True
    if has_value and has_label:
        return True
    return False


def _serial_col_index(
    x0: float,
    col_ranges: List[Tuple[float, float]],
    anchors: LayoutAnchors,
) -> int:
    """序号按 x0 落在序号带左缘，不依赖 bbox 中心。"""
    if not col_ranges:
        return 0
    if x0 <= anchors.row_num_x_max + 8:
        return 0
    for i, (lo, hi) in enumerate(col_ranges):
        if lo - 4 <= x0 <= hi + 6:
            return i
    return 0


def _label_col_index(
    x0: float,
    col_ranges: List[Tuple[float, float]],
    anchors: LayoutAnchors,
) -> int:
    """标签列：优先按列界 col1，再回退 anchor 估计。"""
    if len(col_ranges) > 1:
        lo, hi = col_ranges[1]
        if lo - 4 <= x0 <= hi + 8:
            return 1
    if len(col_ranges) > 1 and anchors.label_x_min - 10 <= x0 <= col_ranges[1][1] + 20:
        return 1
    best_i = 1 if len(col_ranges) > 1 else 0
    best_overlap = -1.0
    for i, (lo, hi) in enumerate(col_ranges):
        if i == 0 and x0 > col_ranges[0][1] + 5:
            continue
        overlap = min(hi, anchors.label_x_max) - max(lo, anchors.label_x_min)
        if overlap > best_overlap and x0 <= hi + 10:
            best_overlap = overlap
            best_i = i
    return best_i


def _pick_peak_col(votes: List[int], *, default: int) -> int:
    if not votes:
        return default
    best = max(range(len(votes)), key=lambda i: votes[i])
    return best if votes[best] > 0 else default


def _pick_label_col(
    col_ranges: List[Tuple[float, float]],
    anchors: LayoutAnchors,
    label_votes: List[int],
    *,
    fallback: int,
) -> int:
    if any(v > 0 for v in label_votes):
        return _pick_peak_col(label_votes, default=fallback)
    best_i = fallback
    best_overlap = -1.0
    for i, (lo, hi) in enumerate(col_ranges):
        overlap = min(hi, anchors.label_x_max) - max(lo, anchors.label_x_min)
        if overlap > best_overlap:
            best_overlap = overlap
            best_i = i
    return best_i


def _assign_value_col(
    it: dict,
    col_ranges: List[Tuple[float, float]],
    value_cols: List[int],
) -> int:
    return assign_data_value_column(
        it, col_ranges, value_cols=value_cols or None,
    )


def _fallback_col(
    it: dict,
    col_ranges: List[Tuple[float, float]],
    label_col: int,
) -> int:
    x0 = float(it.get("x0", 0))
    x1 = float(it.get("x1", 0))
    t = str(it.get("text", "")).strip()
    if _CJK_RE.search(t) and x0 < col_ranges[label_col][1] + 20:
        return label_col
    return col_index_by_anchor(x0, x1, t, col_ranges)


def _is_exposure_category_item(
    it: dict,
    col_ranges: List[Tuple[float, float]],
    category_col: int,
) -> bool:
    t = str(it.get("text", "")).strip()
    x0 = float(it.get("x0", 0))
    if not t or is_pd_range_cell_text(t) or is_numeric_data_cell(t):
        return False
    if category_col >= len(col_ranges):
        return False
    lo, hi = col_ranges[category_col]
    return lo - 4 <= x0 <= hi + 6 and bool(_CJK_RE.search(t)) and len(t) <= 12


def _is_serial_item(it: dict, anchors: LayoutAnchors) -> bool:
    t = str(it.get("text", "")).strip()
    x0 = float(it.get("x0", 0))
    return bool(t) and _ROW_NUMBER_RE.match(t) and x0 <= anchors.row_num_x_max + 5


def _is_label_item(it: dict, anchors: LayoutAnchors) -> bool:
    t = str(it.get("text", "")).strip()
    if not t or _ROW_NUMBER_RE.match(t):
        return False
    x0 = float(it.get("x0", 0))
    if x0 > anchors.label_x_max + 30:
        return False
    if x0 < anchors.value_x_min - 15:
        from codes.table_engine.geometry.data_column_assign import (
            _RISK_WEIGHT_LABEL_RE,
            _SIMPLE_PCT_LABEL_RE,
        )
        if _RISK_WEIGHT_LABEL_RE.match(t) or _SIMPLE_PCT_LABEL_RE.match(t):
            return True
    if is_numeric_data_cell(t):
        return False
    if anchors.label_x_min - 10 <= x0 <= anchors.label_x_max + 40:
        return bool(_CJK_RE.search(t))
    return x0 <= anchors.row_num_x_max + 35 and bool(_CJK_RE.search(t))


def _is_value_item(it: dict, anchors: LayoutAnchors) -> bool:
    t = str(it.get("text", "")).strip()
    if not t:
        return False
    x0 = float(it.get("x0", 0))
    if x0 < anchors.value_x_min - 20:
        return False
    if t in ("-", "—", "–", "－"):
        return True
    if is_year_cell(t) or is_report_period_cell(t):
        return False
    return is_numeric_data_cell(t)


def _is_code_item(
    it: dict,
    anchors: LayoutAnchors,
    layout_id: str,
    *,
    code_x0: float,
) -> bool:
    if layout_id not in ("pillar_cc1", "pillar_cc2"):
        return False
    t = str(it.get("text", "")).strip()
    x0 = float(it.get("x0", 0))
    if layout_id == "pillar_cc1" and t in ("-", "—", "–", "－") and x0 >= code_x0 - 8:
        return True
    if len(t) > 3:
        return False
    if t.lower() not in _CODE_LETTERS and t not in ("e+g",):
        return False
    return x0 >= code_x0 - 12
