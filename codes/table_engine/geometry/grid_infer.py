# -*- coding: utf-8 -*-
"""约束网格推断（CGR）：在 liteparse bbox 之间的「沟」里定列界，线不切字。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, TYPE_CHECKING

from codes.table_engine.geometry.layout_rows import body_rows_for_layout, is_poison_row_for_layout
from codes.table_engine.geometry.column_anchors import (
    col_index_by_anchor,
    col_index_by_x0,
    infer_numeric_data_column_splits,
    is_stage_column_header_text,
    is_value_column_header_text,
    item_column_anchor,
)
from codes.table_engine.geometry.cell_decomposition import (
    count_decomposition_violations,
    rows_look_like_change_reason_body,
)
from codes.table_engine.geometry.numeric import (
    is_merged_numeric_cell,
    is_numeric_data_cell,
    is_quarter_column_header_text,
    is_report_date_cell,
)
from codes.table_engine.conservation.item_conservation import apply_table_transform_guard

if TYPE_CHECKING:
    from codes.table_engine.models import StructuredTable

_X_CLUSTER_TOL = 18.0
_CENTER_MERGE_TOL = 38.0
_HEADER_X_DEDUP_TOL = 12.0
_MIN_HEADER_MARKERS = 8
_ABCDE_RE = re.compile(r"^[a-e]$", re.I)
_PERIOD_HEADER_RE = re.compile(
    r"^(?:变[动化][（(]?\s*%[）)]?|(?:19|20)\d{2}年(?:\d{1,2}月\d{1,2}日)?)$"
)
_ANCHOR_GRID_METHODS = frozenset({
    "header_anchor_grid",
    "period_header_anchor_grid",
    "metric_header_anchor_grid",
    "numeric_gutter_grid",
})
_BODY_FIRST_GRID_METHODS = frozenset({"numeric_gutter_grid", "constraint_grid"})
_DBCG_BODY_FIT_WEIGHT = 12.0
_DBCG_BODY_FIRST_BONUS = 4.0
_DBCG_TIEBREAK_SLACK = 4.0
_PILLAR_LAYOUT_MIN_BEAT = 12.0
_YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}年?$")
_METRIC_COL_MARKERS = ("平均余额", "支出", "成本率", "利息收入", "平均收益率")
_SUBCOLUMN_HEADER_LABELS = frozenset({"注释", "交易金额", "交易余额"})
_SUBCOLUMN_HEADER_FRAG_RE = re.compile(r"^的比例$|^占同类交易$")
_FOOTNOTE_MARKER_RE = re.compile(r"^\([ivxlc]+\)$", re.I)
_MIN_ROW_SUPPORT_RATIO = 0.12
_LINE_EPS = 0.6
_MIN_COLS = 2
_MAX_COLS = 16
_SERIAL_RE = re.compile(r"^\d+[a-z]?$", re.I)


@dataclass
class GridInference:
    """列网格推断结果。"""

    col_ranges: List[Tuple[float, float]]
    col_lines: List[float] = field(default_factory=list)
    confidence: float = 0.0
    method: str = "constraint_grid"
    column_centers: List[float] = field(default_factory=list)

    @property
    def col_count(self) -> int:
        return len(self.col_ranges)


def infer_constraint_grid(
    rows: List[dict],
    x_lo: float,
    x_hi: float,
    *,
    min_confidence: float = 0.55,
) -> Optional[GridInference]:
    """从 body 行 item 的 x 对齐 + 列沟推断列界。"""
    body = body_rows_for_layout(rows)
    if not body:
        body = [r for r in rows if not is_poison_row_for_layout(r)]
    if not body:
        return None

    all_layout_items: List[dict] = []
    for row in rows:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t:
                continue
            if (float(it.get("x1", 0)) - float(it.get("x0", 0))) > 160:
                continue
            all_layout_items.append(it)

    all_items: List[dict] = []
    weighted_x0: List[Tuple[float, float]] = []
    for row in body:
        w = _row_vote_weight(row)
        if w <= 0:
            continue
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t:
                continue
            if (float(it.get("x1", 0)) - float(it.get("x0", 0))) > 160:
                continue
            all_items.append(it)
            ax = _anchor_x(it)
            weighted_x0.append((ax, w))

    if not all_items or len(weighted_x0) < 2:
        return None

    items_for_lines = all_layout_items or all_items
    candidates: List[Tuple[str, List[Tuple[float, float]], List[float]]] = []
    seen_range_keys: set[Tuple[Tuple[float, float], ...]] = set()

    def _range_key(ranges: List[Tuple[float, float]]) -> Tuple[Tuple[float, float], ...]:
        return tuple((round(lo, 1), round(hi, 1)) for lo, hi in ranges)

    def _push_candidate(method: str, centers: List[float]) -> None:
        if len(centers) < _MIN_COLS:
            return
        cc = list(centers)
        if len(cc) > _MAX_COLS:
            cc = cc[:_MAX_COLS]
        lines = _centers_to_col_lines(cc, items_for_lines, x_lo, x_hi)
        if len(lines) < 2:
            return
        ranges = [(lines[i], lines[i + 1]) for i in range(len(lines) - 1)]
        if len(ranges) < _MIN_COLS:
            return
        key = _range_key(ranges)
        if key in seen_range_keys:
            return
        seen_range_keys.add(key)
        candidates.append((method, ranges, cc))

    body_centers = _cluster_weighted_x0(weighted_x0, _X_CLUSTER_TOL)
    body_centers = _merge_close_centers(body_centers, _CENTER_MERGE_TOL)
    body_centers = _prune_centers_by_support(body_centers, body, all_items)
    _push_candidate("constraint_grid", body_centers)

    header_xs = _header_band_column_xs(rows)
    if len(header_xs) >= _MIN_HEADER_MARKERS:
        lead = _infer_lead_columns(body, header_xs)
        _push_candidate("header_anchor_grid", sorted(set(lead + header_xs)))

    metric_xs = _header_band_metric_column_xs(rows)
    if len(metric_xs) >= 4:
        lead = _infer_label_lead_column(body, metric_xs)
        _push_candidate("metric_header_anchor_grid", sorted(set(lead + metric_xs)))

    period_xs = _header_band_period_column_xs(rows)
    if len(period_xs) >= 3:
        lead = _infer_label_lead_column(body, period_xs)
        _push_candidate("period_header_anchor_grid", sorted(set(lead + period_xs)))

    gutter_ranges = _ranges_from_numeric_gutters(rows, items_for_lines, x_lo, x_hi)
    if gutter_ranges and len(gutter_ranges) >= _MIN_COLS:
        gkey = _range_key(gutter_ranges)
        if gkey not in seen_range_keys:
            seen_range_keys.add(gkey)
            gutter_centers = [(lo + hi) / 2.0 for lo, hi in gutter_ranges]
            candidates.append(("numeric_gutter_grid", gutter_ranges, gutter_centers))

    lead_numeric_ranges = _ranges_from_lead_and_numeric(rows, x_lo, x_hi)
    if lead_numeric_ranges and len(lead_numeric_ranges) >= _MIN_COLS:
        lk = _range_key(lead_numeric_ranges)
        if lk not in seen_range_keys:
            seen_range_keys.add(lk)
            lead_centers = [(lo + hi) / 2.0 for lo, hi in lead_numeric_ranges]
            candidates.append(("lead_numeric_grid", lead_numeric_ranges, lead_centers))

    picked = _pick_dbcg_grid_candidate(candidates, rows, body, all_items)
    if picked is None:
        return None

    method, ranges, centers, _pick_score = picked
    conf = _score_grid(body, all_items, centers, ranges)
    if conf < min_confidence:
        return None

    inner_lines = [ranges[i][1] for i in range(len(ranges) - 1)]
    return GridInference(
        col_ranges=ranges,
        col_lines=inner_lines,
        confidence=conf,
        column_centers=centers,
        method=method,
    )


def _dedupe_sorted_xs(xs: List[float], tol: float) -> List[float]:
    if not xs:
        return []
    ordered = sorted(xs)
    out = [ordered[0]]
    for x in ordered[1:]:
        if x - out[-1] > tol:
            out.append(x)
    return out


def _header_band_column_xs(rows: List[dict], *, scan: int = 8) -> List[float]:
    """表头带 a–e 列标 → 列锚点 x（避免 c/d、6-12/≥1 被聚类合并）。"""
    xs: List[float] = []
    for row in rows[:scan]:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if _ABCDE_RE.match(t):
                xs.append(float(it.get("x0", 0)))
    return _dedupe_sorted_xs(xs, _HEADER_X_DEDUP_TOL)


def _is_subcolumn_header_text(text: str) -> bool:
    """数据区子列表头：注释 / 交易金额 / 占同类交易 / 的比例 等。"""
    t = str(text or "").strip()
    if not t or len(t) > 10:
        return False
    if t in _SUBCOLUMN_HEADER_LABELS:
        return True
    return bool(_SUBCOLUMN_HEADER_FRAG_RE.match(t))


def _collect_subcolumn_header_xs(rows: List[dict], *, scan: int = 14) -> List[float]:
    xs: List[float] = []
    for row in rows[:scan]:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not _is_subcolumn_header_text(t):
                continue
            x0 = float(it.get("x0", 0))
            if x0 < 150:
                continue
            xs.append(x0)
    return _dedupe_sorted_xs(xs, _HEADER_X_DEDUP_TOL)


def _collect_stage_column_header_xs(rows: List[dict], *, scan: int = 14) -> List[float]:
    """阶段一/二/三列表头中心 x。"""
    xs: List[float] = []
    for row in rows[:scan]:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not is_stage_column_header_text(t):
                continue
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", 0))
            if x0 < 150:
                continue
            xs.append((x0 + x1) / 2.0)
    return _dedupe_sorted_xs(xs, _HEADER_X_DEDUP_TOL)


def _stage_column_merge_violations(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> int:
    """多个阶段列表头落入同一列 → 列界过宽。"""
    violations = 0
    for row in rows[:12]:
        buckets: dict[int, List[str]] = {}
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not is_stage_column_header_text(t):
                continue
            ci = col_index_by_anchor(
                float(it.get("x0", 0)),
                float(it.get("x1", 0)),
                t,
                col_ranges,
            )
            buckets.setdefault(ci, []).append(t)
        for texts in buckets.values():
            if len(texts) >= 2:
                violations += len(texts) - 1
    return violations


def _collect_footnote_marker_xs(rows: List[dict]) -> List[float]:
    """表体脚注 (i)/(ii) 列锚点。"""
    body = body_rows_for_layout(rows) or rows
    xs: List[float] = []
    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not _FOOTNOTE_MARKER_RE.match(t):
                continue
            x0 = float(it.get("x0", 0))
            if 150 < x0 < 400:
                xs.append(x0)
    return _dedupe_sorted_xs(xs, 8.0)


def _is_metric_column_header_text(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t) > 12:
        return False
    if "／" in t or "/" in t:
        return False
    if t in ("平均余额", "支出", "利息收入", "利息支出"):
        return True
    if "成本率" in t:
        return True
    return False


def _is_period_like_header_text(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if is_quarter_column_header_text(t):
        return True
    if _PERIOD_HEADER_RE.match(t):
        return True
    if _is_metric_column_header_text(t):
        return True
    return bool(re.search(r"(?:19|20)\d{2}年", t) and len(t) <= 12)


def _all_layout_items(rows: List[dict]) -> List[dict]:
    items: List[dict] = []
    for row in rows:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t:
                continue
            if (float(it.get("x1", 0)) - float(it.get("x0", 0))) > 160:
                continue
            items.append(it)
    return items


def _collect_column_anchor_xs(rows: List[dict]) -> List[float]:
    """表头标记 + 数值列锚点 → 列中心候选。"""
    xs: List[float] = []
    xs.extend(_header_band_column_xs(rows))
    xs.extend(_collect_subcolumn_header_xs(rows))
    xs.extend(_collect_stage_column_header_xs(rows))
    xs.extend(_collect_footnote_marker_xs(rows))
    xs.extend(_header_band_period_column_xs(rows))
    xs.extend(_header_band_metric_column_xs(rows))
    for row in rows[:12]:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if (
                _is_period_like_header_text(t)
                or _is_subcolumn_header_text(t)
                or is_stage_column_header_text(t)
            ):
                xs.append(item_column_anchor(it))
    body = body_rows_for_layout(rows) or rows
    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if is_numeric_data_cell(t) and float(it.get("x0", 0)) > 140:
                xs.append(item_column_anchor(it))
    return _dedupe_sorted_xs(xs, 10.0)


def _ranges_merge_distinct_headers(
    col_ranges: List[Tuple[float, float]],
    rows: List[dict],
) -> bool:
    """两列以上表头标记落入同一列 → 列界过宽。"""
    for row in rows[:12]:
        buckets: dict[int, List[str]] = {}
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not (
                _is_period_like_header_text(t)
                or _is_subcolumn_header_text(t)
                or is_stage_column_header_text(t)
                or is_value_column_header_text(t, x0=float(it.get("x0", 0)))
            ):
                continue
            ci = col_index_by_anchor(
                float(it.get("x0", 0)),
                float(it.get("x1", 0)),
                t,
                col_ranges,
            )
            buckets.setdefault(ci, []).append(t)
        for texts in buckets.values():
            if len(set(texts)) >= 2:
                return True
    return False


def _value_column_header_merge_violations(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> int:
    """数值区多个不同表头落入同一列。"""
    violations = 0
    for row in rows[:12]:
        buckets: dict[int, List[str]] = {}
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            x0 = float(it.get("x0", 0))
            if not is_value_column_header_text(t, x0=x0):
                continue
            ci = col_index_by_anchor(
                x0,
                float(it.get("x1", 0)),
                t,
                col_ranges,
            )
            buckets.setdefault(ci, []).append(t)
        for texts in buckets.values():
            if len(set(texts)) >= 2:
                violations += len(set(texts)) - 1
    return violations


def _subdivide_ranges_by_numeric_gutters(
    col_ranges: List[Tuple[float, float]],
    rows: List[dict],
) -> List[Tuple[float, float]]:
    splits = infer_numeric_data_column_splits(rows) or []
    if not splits:
        return col_ranges
    bounds: List[float] = []
    for lo, hi in col_ranges:
        if not bounds:
            bounds.append(lo)
        for s in splits:
            if lo + 8 < s < hi - 8:
                bounds.append(s)
        bounds.append(hi)
    ordered = sorted(set(bounds))
    out = [(ordered[i], ordered[i + 1]) for i in range(len(ordered) - 1)]
    return [r for r in out if r[1] - r[0] >= 12.0] or col_ranges


def _ranges_from_anchor_centers(
    rows: List[dict],
    anchor_xs: List[float],
    all_items: List[dict],
    x_lo: float,
    x_hi: float,
) -> List[Tuple[float, float]]:
    data_xs = [x for x in anchor_xs if x > 150]
    body = body_rows_for_layout(rows) or rows
    lead = _infer_label_lead_column(body, data_xs or anchor_xs)
    centers = sorted(set(lead + anchor_xs))
    if len(centers) < 2:
        return []
    lines = _centers_to_col_lines(centers, all_items, x_lo, x_hi)
    if len(lines) < 2:
        return []
    return [(lines[i], lines[i + 1]) for i in range(len(lines) - 1)]


def _expected_value_col_count(rows: List[dict]) -> int:
    """body 数值沟道推断出的值列数（不含标签列）。"""
    splits = infer_numeric_data_column_splits(rows, min_clusters=2) or []
    if len(splits) >= 2:
        return len(splits) + 1
    return 0


def _infer_note_column_bounds(rows: List[dict]) -> Optional[Tuple[float, float]]:
    """独立「注释」列界：(列左, 列右)。表头含注释且与首列金额沟道可分时返回。"""
    note_lo: Optional[float] = None
    note_hi: Optional[float] = None
    for row in rows[:14]:
        for it in row.get("items") or []:
            if str(it.get("text", "")).strip() != "注释":
                continue
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", 0))
            note_lo = x0 if note_lo is None else min(note_lo, x0)
            note_hi = x1 if note_hi is None else max(note_hi, x1)
    if note_lo is None or note_hi is None:
        return None

    body = body_rows_for_layout(rows) or rows
    amt_x0: List[float] = []
    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not is_numeric_data_cell(t):
                continue
            x0 = float(it.get("x0", 0))
            if x0 > note_hi + 5:
                amt_x0.append(x0)
    if not amt_x0:
        return None

    first_amt = min(amt_x0)
    if first_amt <= note_hi + 8:
        return None
    col_hi = (note_hi + first_amt) / 2.0
    return (note_lo - 6.0, col_hi)


def _ranges_from_lead_and_numeric(
    rows: List[dict],
    x_lo: float,
    x_hi: float,
) -> List[Tuple[float, float]]:
    """序号 + 多左文本列 + 数值沟道 → 完整列界（分支机构表等）。"""
    body = body_rows_for_layout(rows) or rows
    splits = infer_numeric_data_column_splits(rows, min_clusters=2) or []
    if len(splits) < 2:
        return []
    lead = _infer_label_lead_column(body, splits)
    if len(lead) < 2:
        return []

    num_x0: List[float] = []
    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if is_numeric_data_cell(t) and float(it.get("x0", 0)) > 140:
                num_x0.append(float(it.get("x0", 0)))
    if not num_x0:
        return []

    label_hi = min(num_x0) - 8.0
    bounds = [x_lo]
    deduped = _dedupe_sorted_xs(lead, 14.0)
    deduped = _merge_indent_lead_xs(deduped)
    for i in range(len(deduped) - 1):
        if deduped[i + 1] - deduped[i] >= 40.0:
            bounds.append((deduped[i] + deduped[i + 1]) / 2.0)
    bounds.append(label_hi)
    bounds.extend(s for s in splits if label_hi < s < x_hi)
    bounds.append(x_hi)
    ordered = sorted(set(bounds))
    ranges = [(ordered[i], ordered[i + 1]) for i in range(len(ordered) - 1)]
    return [r for r in ranges if r[1] - r[0] >= 10.0]


def _ranges_from_numeric_gutters(
    rows: List[dict],
    all_items: List[dict],
    x_lo: float,
    x_hi: float,
) -> List[Tuple[float, float]]:
    """数值列 x1 沟道 + 标签列 → 完整列界（双期多指标表）。"""
    splits = infer_numeric_data_column_splits(rows, min_clusters=2) or []
    if len(splits) < 2:
        return []

    body = body_rows_for_layout(rows) or rows
    num_x0: List[float] = []
    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if is_numeric_data_cell(t) and float(it.get("x0", 0)) > 140:
                num_x0.append(float(it.get("x0", 0)))
    if not num_x0:
        return []

    label_hi = min(num_x0) - 8.0
    label_lo = x_lo
    # 列检测前拆分 liteparse 合并的「机构名+地址」复合 item
    col_body = _copy_body_with_name_addr_splits(body)
    lead_xs = _infer_label_lead_column(col_body, splits)
    if lead_xs:
        label_lo = min(label_lo, min(lead_xs) - 12.0)

    left_lead_bounds = [label_lo]
    deduped = _dedupe_sorted_xs(lead_xs, 14.0) if lead_xs else []
    deduped = _merge_indent_lead_xs(deduped)
    for i in range(len(deduped) - 1):
        if deduped[i + 1] - deduped[i] >= 40.0:
            left_lead_bounds.append((deduped[i] + deduped[i + 1]) / 2.0)

    note_bounds = _infer_note_column_bounds(rows)
    if note_bounds:
        note_col_lo, note_col_hi = note_bounds
        label_hi = min(label_hi, note_col_lo)
        bounds = (
            left_lead_bounds
            + [label_hi, note_col_hi]
            + [s for s in splits if note_col_hi < s < x_hi]
            + [x_hi]
        )
    else:
        bounds = (
            left_lead_bounds
            + [label_hi]
            + [s for s in splits if label_hi < s < x_hi]
            + [x_hi]
        )
    ordered = sorted(set(bounds))
    ranges = [(ordered[i], ordered[i + 1]) for i in range(len(ordered) - 1)]
    return [r for r in ranges if r[1] - r[0] >= 10.0]


def _merged_numeric_violations(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
    *,
    label_hi: float = 150.0,
) -> int:
    """模拟分列后，值列中疑似合并数值的格数。"""
    body = body_rows_for_layout(rows) or rows
    violations = 0
    for row in body:
        col_parts: dict[int, List[str]] = {}
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t:
                continue
            x0 = float(it.get("x0", 0))
            ci = col_index_by_anchor(
                x0,
                float(it.get("x1", 0)),
                t,
                col_ranges,
            )
            if ci < 0 or ci >= len(col_ranges):
                continue
            if col_ranges[ci][0] < label_hi and x0 < label_hi:
                continue
            col_parts.setdefault(ci, []).append(t)
        for ci, parts in col_parts.items():
            if col_ranges[ci][0] < label_hi:
                continue
            joined = " ".join(parts)
            if is_merged_numeric_cell(joined):
                violations += 1
    return violations


def _merged_footnote_label_violations(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> int:
    """脚注 (i) 与行标签或金额挤入同一列。"""
    if not _infer_note_column_bounds(rows):
        return 0
    body = body_rows_for_layout(rows) or rows
    violations = 0
    for row in body:
        buckets: dict[int, List[str]] = {}
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t:
                continue
            ci = col_index_by_anchor(
                float(it.get("x0", 0)),
                float(it.get("x1", 0)),
                t,
                col_ranges,
            )
            if 0 <= ci < len(col_ranges):
                buckets.setdefault(ci, []).append(t)
        for parts in buckets.values():
            has_note = any(_FOOTNOTE_MARKER_RE.match(p) for p in parts)
            has_label = any(
                re.search(r"[\u4e00-\u9fff]", p)
                and not _FOOTNOTE_MARKER_RE.match(p)
                and not is_numeric_data_cell(p)
                and len(p) > 2
                for p in parts
            )
            has_amount = any(is_numeric_data_cell(p) for p in parts)
            if has_note and has_label:
                violations += 1
            if has_note and has_amount:
                violations += 1
    return violations


def _note_column_layout_bonus(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> float:
    """存在「注释」列时，奖励独立窄列、惩罚与标签列合并。"""
    note_bounds = _infer_note_column_bounds(rows)
    if not note_bounds:
        return 0.0
    nlo, nhi = note_bounds
    for lo, hi in col_ranges:
        if lo <= nlo + 3 and hi >= nhi - 3 and (hi - lo) <= 55:
            return 18.0
    return -30.0


def _col_index_by_item_x0(
    x0: float,
    col_ranges: List[Tuple[float, float]],
) -> int:
    return col_index_by_x0(x0, col_ranges)


def _left_text_column_merge_violations(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> int:
    """同一行多个左对齐文本列（名称/地址）被合并到同一列。"""
    body = body_rows_for_layout(rows) or rows
    violations = 0
    for row in body:
        text_items: List[dict] = []
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t or is_numeric_data_cell(t):
                continue
            x0 = float(it.get("x0", 0))
            if _SERIAL_RE.match(t) and x0 < 100:
                text_items.append(it)
            elif re.search(r"[\u4e00-\u9fff]", t) and x0 < 300:
                text_items.append(it)
        if len(text_items) < 2:
            continue
        text_items.sort(key=lambda it: float(it.get("x0", 0)))
        for a, b in zip(text_items, text_items[1:]):
            ax = float(a.get("x0", 0))
            bx = float(b.get("x0", 0))
            if bx - ax < 20.0:
                continue
            aci = _col_index_by_item_x0(ax, col_ranges)
            bci = _col_index_by_item_x0(bx, col_ranges)
            if aci == bci:
                violations += 1
    return violations


def _serial_label_merge_violations(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> int:
    """序号与行标签落同一列（应分列）。"""
    body = body_rows_for_layout(rows) or rows
    violations = 0
    for row in body:
        serial_item: Optional[dict] = None
        label_item: Optional[dict] = None
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t:
                continue
            x0 = float(it.get("x0", 0))
            if _SERIAL_RE.match(t) and x0 < 100:
                serial_item = it
            elif re.search(r"[\u4e00-\u9fff]", t) and not is_numeric_data_cell(t):
                if label_item is None or x0 > float(label_item.get("x0", 0)):
                    label_item = it
        if not serial_item or not label_item:
            continue
        sx = float(serial_item.get("x0", 0))
        lx = float(label_item.get("x0", 0))
        if lx <= sx + 12.0:
            continue
        sci = _col_index_by_item_x0(sx, col_ranges)
        lci = _col_index_by_item_x0(lx, col_ranges)
        if sci == lci:
            violations += 1
    return violations


def _has_serial_label_lead_columns(rows: List[dict]) -> bool:
    body = body_rows_for_layout(rows) or rows
    splits = infer_numeric_data_column_splits(rows, min_clusters=4) or []
    if not splits:
        return False
    lead = _infer_label_lead_column(body, splits)
    return len(lead) >= 2 and lead[1] - lead[0] >= 18.0


def _has_change_reason_table_header(rows: List[dict]) -> bool:
    """变化原因表：项目 + 报告期 + 增减幅度 + 主要原因。"""
    for row in rows[:14]:
        parts = [
            str(it.get("text", "")).strip()
            for it in (row.get("items") or [])
            if str(it.get("text", "")).strip()
        ]
        if not parts:
            continue
        joined = " ".join(parts)
        if "项目" in joined and ("增减幅度" in joined or "变化幅度" in joined):
            return True
        if ("主要原因" in joined or "变化原因" in joined) and any(
            "2024" in p or "2023" in p or "增减" in p for p in parts
        ):
            return True
    return False


def _change_table_mixed_cell_violations(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> int:
    """变化原因表及同类：多字段粘连 item 数（委托统一分解检测）。"""
    return count_decomposition_violations(rows, col_ranges)


def _expected_total_col_count(rows: List[dict]) -> int:
    if _has_change_reason_table_header(rows) or rows_look_like_change_reason_body(rows):
        return 5
    expected_vals = _expected_value_col_count(rows)
    body = body_rows_for_layout(rows) or rows
    splits = infer_numeric_data_column_splits(rows, min_clusters=2) or []
    lead = _infer_label_lead_column(body, splits) if splits else []
    if len(lead) >= 3 and expected_vals >= 3:
        return len(lead) + expected_vals
    if expected_vals < 3:
        return 0
    total = expected_vals + 1
    if _has_serial_label_lead_columns(rows):
        total += 1
    if _infer_note_column_bounds(rows):
        total += 1
    return total


def _body_row_fit_ratio(
    body_rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> float:
    """表体行 item 全部落列的比例（0–1）。"""
    if not body_rows or not col_ranges:
        return 0.0
    matched = 0
    total = 0
    for row in body_rows:
        items = row.get("items") or []
        if not items:
            continue
        total += 1
        ok = 0
        for it in items:
            ci = col_index_by_anchor(
                float(it.get("x0", 0)),
                float(it.get("x1", 0)),
                str(it.get("text", "")),
                col_ranges,
            )
            if 0 <= ci < len(col_ranges):
                ok += 1
        if ok == len(items):
            matched += 1
    return matched / max(total, 1)


def _score_dbcg_candidate(
    method: str,
    col_ranges: List[Tuple[float, float]],
    rows: List[dict],
    body_rows: List[dict],
) -> float:
    """双带约束网格：列界惩罚 + 表体落列率；表体路径小幅加分。"""
    score = _score_col_ranges(col_ranges, rows, body_rows=body_rows, include_body_fit=False)
    if score < 0:
        return score
    score += _body_row_fit_ratio(body_rows, col_ranges) * _DBCG_BODY_FIT_WEIGHT
    if method in _BODY_FIRST_GRID_METHODS:
        score += _DBCG_BODY_FIRST_BONUS
    return score


def _pick_dbcg_grid_candidate(
    candidates: List[Tuple[str, List[Tuple[float, float]], List[float]]],
    rows: List[dict],
    body_rows: List[dict],
    all_items: List[dict],
) -> Optional[Tuple[str, List[Tuple[float, float]], List[float], float]]:
    """多假设并行，统一打分；近分时优先表体锚定路径。"""
    del all_items  # 保留签名供后续扩展
    if not candidates:
        return None
    best: Optional[Tuple[str, List[Tuple[float, float]], List[float], float]] = None
    best_score = -1e9
    for method, ranges, centers in candidates:
        sc = _score_dbcg_candidate(method, ranges, rows, body_rows)
        if sc < 0:
            continue
        if sc > best_score + _DBCG_TIEBREAK_SLACK:
            best = (method, ranges, centers, sc)
            best_score = sc
            continue
        if abs(sc - best_score) <= _DBCG_TIEBREAK_SLACK and best is not None:
            cur_body = method in _BODY_FIRST_GRID_METHODS
            best_body = best[0] in _BODY_FIRST_GRID_METHODS
            if cur_body and not best_body:
                best = (method, ranges, centers, sc)
                best_score = sc
            elif cur_body and best_body and method == "numeric_gutter_grid":
                best = (method, ranges, centers, sc)
                best_score = sc
    return best


def _score_col_ranges(
    col_ranges: List[Tuple[float, float]],
    rows: List[dict],
    *,
    body_rows: Optional[List[dict]] = None,
    include_body_fit: bool = True,
) -> float:
    if not col_ranges or min(hi - lo for lo, hi in col_ranges) < 10.0:
        return -1.0
    merged = _ranges_merge_distinct_headers(col_ranges, rows)
    score = float(len(col_ranges) * 10 - (80 if merged else 0))
    target = _expected_total_col_count(rows)
    if target >= 5:
        score -= abs(len(col_ranges) - target) * 15
    violations = _merged_numeric_violations(rows, col_ranges)
    violations += _merged_footnote_label_violations(rows, col_ranges)
    violations += _stage_column_merge_violations(rows, col_ranges)
    violations += _value_column_header_merge_violations(rows, col_ranges)
    violations += _serial_label_merge_violations(rows, col_ranges)
    violations += _left_text_column_merge_violations(rows, col_ranges)
    violations += _change_table_mixed_cell_violations(rows, col_ranges)
    score -= violations * 25
    score += _note_column_layout_bonus(rows, col_ranges)
    if include_body_fit:
        fit_rows = body_rows if body_rows is not None else (body_rows_for_layout(rows) or rows)
        score += _body_row_fit_ratio(fit_rows, col_ranges) * _DBCG_BODY_FIT_WEIGHT
    return score


def refine_col_ranges_by_coordinates(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
    x_lo: float,
    x_hi: float,
) -> List[Tuple[float, float]]:
    """用表头/数值锚点坐标精炼列界，纠正相邻列被粗分栏合并。"""
    if len(col_ranges) < 2:
        return col_ranges

    expected = _expected_value_col_count(rows)
    violations = _merged_numeric_violations(rows, col_ranges)
    needs_refine = (
        _ranges_merge_distinct_headers(col_ranges, rows)
        or _stage_column_merge_violations(rows, col_ranges) > 0
        or _value_column_header_merge_violations(rows, col_ranges) > 0
        or (expected >= 4 and expected + 1 > len(col_ranges))
        or _change_table_mixed_cell_violations(rows, col_ranges) > 0
        or violations > 0
    )
    if not needs_refine:
        return col_ranges

    all_items = _all_layout_items(rows)
    anchor_xs = _collect_column_anchor_xs(rows)
    candidates: List[List[Tuple[float, float]]] = [col_ranges]
    candidates.append(_subdivide_ranges_by_numeric_gutters(col_ranges, rows))
    gutter_ranges = _ranges_from_numeric_gutters(rows, all_items, x_lo, x_hi)
    if gutter_ranges:
        candidates.append(gutter_ranges)
    if len(anchor_xs) >= 2:
        anchor_ranges = _ranges_from_anchor_centers(rows, anchor_xs, all_items, x_lo, x_hi)
        if anchor_ranges:
            candidates.append(anchor_ranges)

    best = col_ranges
    best_score = _score_col_ranges(best, rows)
    for cand in candidates:
        if cand == col_ranges:
            continue
        if len(cand) > len(col_ranges) and min(hi - lo for lo, hi in cand) < 25.0:
            continue
        sc = _score_col_ranges(cand, rows)
        if sc > best_score:
            best, best_score = cand, sc
    return best


def structured_table_to_row_dicts(table: "StructuredTable") -> List[dict]:
    """从已建表 cell 还原分列用 row dict（结构拆分后按本表坐标重推断列界）。"""
    row_dicts: List[dict] = []
    for ri, row in enumerate(table.rows):
        items: List[dict] = []
        for cell in row:
            if cell is None:
                continue
            text = str(cell.text or "").strip()
            if not text:
                continue
            src = list(cell.source_items or [])
            items.append({
                "text": text,
                "x0": float(cell.bbox.x0),
                "x1": float(cell.bbox.x1),
                "y0": float(cell.bbox.y0),
                "y1": float(cell.bbox.y1),
                "item_index": src[0] if src else f"r{ri}c{cell.col}",
            })
        if items:
            row_dicts.append({
                "items": items,
                "cluster_row_id": ri,
                "row_phase": "",
            })
    return row_dicts


def _joined_equals_header_parts(joined: str, parts: List[str]) -> bool:
    """OCR 将相邻**不同**列表头合成一格：「A B」≈ 模板 [A, B]。

    多列相同表头（如三列均为「预期信用损失」）不算 OCR 合并。
    """
    if len(parts) < 2:
        return False
    j = str(joined or "").strip()
    if not j:
        return False
    if len(set(parts)) < len(parts):
        return False
    j_compact = j.replace(" ", "")
    parts_compact = "".join(parts)
    if len(j_compact) <= max(len(p) for p in parts):
        return False
    if j_compact == parts_compact:
        return True
    if " ".join(parts) in j:
        return True
    return all(p in j for p in parts) and len(j_compact) <= len(parts_compact) + len(parts)


def _find_separate_header_template_row(
    rows: List[List[str]],
    *,
    scan: int = 12,
) -> Optional[Tuple[int, List[str]]]:
    """上半表：多列独立表头行（优先较长短语，如信用减值分类）。"""
    best: Optional[Tuple[int, List[str]]] = None
    best_key = (-1, -1)
    for i in range(min(scan, len(rows))):
        texts = [
            c.strip()
            for c in rows[i]
            if c.strip()
            and len(c.strip()) >= 6
            and not is_numeric_data_cell(c)
            and not is_report_date_cell(c)
        ]
        if len(texts) < 2:
            continue
        if len(set(texts)) != len(texts):
            continue
        key = (min(len(t) for t in texts), sum(len(t) for t in texts))
        if key > best_key:
            best_key = key
            best = (i, texts)
    return best


def _header_cell_x_templates(
    table: "StructuredTable",
    row_idx: int,
    header_texts: List[str],
) -> List[dict]:
    """从模板表指定行取各列表头的坐标。"""
    if row_idx < 0 or row_idx >= len(table.rows):
        return []
    templates: List[dict] = []
    for ht in header_texts:
        for cell in table.rows[row_idx]:
            if cell is None:
                continue
            if str(cell.text or "").strip() != ht:
                continue
            templates.append({
                "text": ht,
                "x0": float(cell.bbox.x0),
                "x1": float(cell.bbox.x1),
            })
            break
    return templates


def _expand_compound_item_with_templates(
    it: dict,
    templates: List[dict],
) -> List[dict]:
    text = str(it.get("text", "")).strip()
    parts = [t["text"] for t in templates]
    if not _joined_equals_header_parts(text, parts):
        return [it]
    y0 = float(it.get("y0", 0))
    y1 = float(it.get("y1", 0))
    item_index = it.get("item_index")
    out: List[dict] = []
    for tpl in templates:
        out.append({
            "text": tpl["text"],
            "x0": float(tpl["x0"]),
            "x1": float(tpl["x1"]),
            "y0": y0,
            "y1": y1,
            "item_index": item_index,
        })
    return out


def repair_compound_header_items_from_template(
    table: "StructuredTable",
    template: "StructuredTable",
) -> "StructuredTable":
    """结构拆分后：用上半表表头坐标拆开下半表中被 OCR 合并的列表头 item。"""
    from codes.table_engine.geometry.cell_builder import build_structured_table
    from codes.table_engine.table_access import dense_rows

    ref = _find_separate_header_template_row(dense_rows(template))
    if not ref:
        return table
    ref_i, ref_texts = ref
    x_templates = _header_cell_x_templates(template, ref_i, ref_texts)
    if len(x_templates) < 2:
        return table

    target_rows = dense_rows(table)
    has_merged = any(
        _joined_equals_header_parts(c, ref_texts)
        for row in target_rows[:12]
        for c in row
    )
    if not has_merged:
        return table

    row_dicts = structured_table_to_row_dicts(table)
    modified = False
    for rd in row_dicts:
        new_items: List[dict] = []
        for it in rd.get("items") or []:
            expanded = _expand_compound_item_with_templates(it, x_templates)
            if len(expanded) > 1:
                modified = True
            new_items.extend(expanded)
        rd["items"] = new_items

    if not modified:
        return table

    x_lo = float(table.x0) - 5.0
    x_hi = float(table.x1) + 5.0
    old_ranges = [(r.x0, r.x1) for r in table.grid.ranges]
    new_ranges = pick_best_col_ranges_for_rows(
        row_dicts, x_lo, x_hi, seed_ranges=old_ranges,
    )
    layout_id = str(table.layout_id or "constraint_grid")
    out = build_structured_table(table.page, row_dicts, new_ranges, layout_id)
    out.metadata = copy.copy(table.metadata)
    out.description_text = table.description_text
    out.layout_id = layout_id
    out.grid.layout_id = layout_id
    out.metadata["compound_header_repaired"] = True
    return apply_table_transform_guard(table, out, require_source_ids=True)


def pick_best_col_ranges_for_rows(
    rows: List[dict],
    x_lo: float,
    x_hi: float,
    *,
    seed_ranges: Optional[List[Tuple[float, float]]] = None,
) -> List[Tuple[float, float]]:
    """在种子列界、数值沟道、约束网格等候选中选最高分列界。"""
    candidates: List[List[Tuple[float, float]]] = []
    if seed_ranges and len(seed_ranges) >= 2:
        candidates.append(list(seed_ranges))
        candidates.append(
            refine_col_ranges_by_coordinates(rows, list(seed_ranges), x_lo, x_hi),
        )
    all_items = _all_layout_items(rows)
    gutter = _ranges_from_numeric_gutters(rows, all_items, x_lo, x_hi)
    if gutter and len(gutter) >= 2:
        candidates.append(gutter)
    grid = infer_constraint_grid(rows, x_lo, x_hi)
    if grid and len(grid.col_ranges) >= 2:
        candidates.append(grid.col_ranges)
    if not candidates:
        return list(seed_ranges or [])
    return max(candidates, key=lambda c: _score_col_ranges(c, rows))


def _is_cr6_category_pd_layout(table: "StructuredTable") -> bool:
    ranges = table.grid.ranges
    if len(ranges) < 2:
        return False
    return ranges[0].role == "category" and ranges[1].role == "pd_range"


def reinfer_table_column_grid(table: "StructuredTable") -> "StructuredTable":
    """结构拆分后按子表自身坐标重推断列界并重分列（避免沿用父表宽网格）。"""
    from codes.table_engine.geometry.cell_builder import build_structured_table

    if _is_cr6_category_pd_layout(table):
        return table

    row_dicts = structured_table_to_row_dicts(table)
    if len(row_dicts) < 2:
        return table

    x_lo = float(table.x0) - 5.0
    x_hi = float(table.x1) + 5.0
    old_ranges = [(r.x0, r.x1) for r in table.grid.ranges]
    old_score = _score_col_ranges(old_ranges, row_dicts)
    new_ranges = pick_best_col_ranges_for_rows(
        row_dicts, x_lo, x_hi, seed_ranges=old_ranges,
    )
    new_score = _score_col_ranges(new_ranges, row_dicts)
    if new_score <= old_score + 0.5:
        return table
    if len(new_ranges) < 2:
        return table

    layout_id = str(table.layout_id or "constraint_grid")
    out = build_structured_table(
        table.page,
        row_dicts,
        new_ranges,
        layout_id,
    )
    out.metadata = copy.copy(table.metadata)
    out.description_text = table.description_text
    out.layout_id = layout_id
    out.grid.layout_id = layout_id
    out.metadata["grid_reinferred"] = True
    out.metadata["grid_inference"] = {
        "method": "post_split_reinfer",
        "col_count": len(new_ranges),
        "score_delta": round(new_score - old_score, 2),
    }
    return apply_table_transform_guard(table, out, require_source_ids=False)


def _header_band_metric_column_xs(rows: List[dict], *, scan: int = 12) -> List[float]:
    """表头子列「平均余额 / 支出 / 成本率(%)」→ 按 x0 定列锚点。"""
    xs: List[float] = []
    for row in rows[:scan]:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if _is_metric_column_header_text(t):
                xs.append(float(it.get("x0", 0)))
    return _dedupe_sorted_xs(xs, _HEADER_X_DEDUP_TOL)


def _header_band_period_column_xs(rows: List[dict], *, scan: int = 10) -> List[float]:
    """表头「2024年」「变化(%)」等 → 数据列锚点。"""
    xs: List[float] = []
    for row in rows[:scan]:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if _PERIOD_HEADER_RE.match(t) or is_quarter_column_header_text(t) or (
                re.search(r"(?:19|20)\d{2}年", t) and len(t) <= 12
            ):
                xs.append(float(it.get("x0", 0)))
    return _dedupe_sorted_xs(xs, _HEADER_X_DEDUP_TOL)


# liteparse 合并"机构名+地址"时的拆分模式
_NAME_SUFFIX_RE = re.compile(
    r"(?:支行|分行|营业部|分理处|储蓄所|信用社"
    r"|办事处|受理点|服务部|经营部|管理部|代表处|分部)"
)
_ADDR_START_STRONG_RE = re.compile(
    r"^[\u4e00-\u9fff]{2,}(?:省|市|县|区|自治[州县区旗])"
)


def _copy_body_with_name_addr_splits(body: List[dict]) -> List[dict]:
    """对 body 做深拷贝，并将 liteparse 合并的「机构名+地址」复合 item 拆分为两个 item。

    拆分坐标参考同列 item 的实际对齐方式：
    - 名称列（文本，左对齐）→ x0 不变，x1 取自同列名称 item 的右缘
    - 地址列（文本，左对齐）→ x0 取自其他行独立地址 item 的左缘中位数
    只在副本上操作，不影响原始数据。
    """
    if not body:
        return body

    # ── 第一遍：收集参考坐标 ──
    # 纯名称 item 的 x1（文本列，左对齐 → 右缘代表列边界）
    name_x1s: List[float] = []
    # 独立地址 item 的 x0（文本列，左对齐 → 左缘代表列起点）
    addr_x0s: List[float] = []

    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", 0))
            if not t or not re.search(r"[\u4e00-\u9fff]", t):
                continue
            if _SERIAL_RE.match(t) or is_numeric_data_cell(t):
                continue

            # 判断此 item 是否会触发拆分规则（即是否被误合并）
            split = _try_detect_name_addr_merge(t)
            if split:
                # 此 item 将被拆分 → 不用它做坐标参考
                continue

            # 独立 item：根据模式分入名称列或地址列参考
            if _NAME_SUFFIX_RE.search(t):
                # 短名称 item（如"华兴支行"），记录其 x1
                # 文本列左对齐，x1 是名称区域自然终点
                name_x1s.append(x1)
            elif re.search(r"(?:[路街号市区镇层栋幢厦])", t):
                # 独立地址 item（如"成都市一环路南四段 30 号"），记录其 x0
                # 文本列左对齐，x0 是地址列自然起点
                addr_x0s.append(x0)

    # 参考分割点：地址列左缘中位数 > 名称列右缘最大值 > 保守回退
    ref_split_x: Optional[float] = None
    if addr_x0s:
        addr_x0s.sort()
        ref_split_x = addr_x0s[len(addr_x0s) // 2]

    ref_name_x1: Optional[float] = None
    if name_x1s:
        name_x1s.sort()
        ref_name_x1 = name_x1s[len(name_x1s) // 2]

    # ── 第二遍：拆分 + 坐标赋值 ──
    modified = False
    rows_copy: List[dict] = []
    for row in body:
        items = row.get("items") or []
        new_items: List[dict] = []
        any_split = False
        for it in items:
            t = str(it.get("text", "")).strip()
            split = _try_split_org_name_address(t)
            if len(split) != 2:
                new_items.append(it)
                continue

            any_split = True
            name_part, addr_part = split
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", 0))

            # 坐标分割策略（优先参考同列 item 的实际坐标）：
            # 名称 item（文本，左对齐）：x0 保持原值
            #    x1 = 名称列实际右缘 (ref_name_x1)
            #    fallback: x0 + 估算宽度
            # 地址 item（文本，左对齐）：x0 = 地址列实际左缘 (ref_split_x)
            #    fallback: 名称 x1 + 间隔
            #    x1 保持原值

            # 名称 x1 估算
            if ref_name_x1 and ref_name_x1 > x0 + 10:
                name_x1 = min(ref_name_x1, x1 - 20)
            else:
                # 保守估算：名称文本约 4-8 个中文字符，每字 ~12px
                est_name_w = max(4, len(name_part)) * 11.0
                name_x1 = x0 + est_name_w

            # 地址 x0 估算
            if ref_split_x and ref_split_x > name_x1 + 4:
                addr_x0 = ref_split_x
            else:
                addr_x0 = name_x1 + 6.0

            # 安全检查：分裂点必须在原始区间内
            split_boundary = max(name_x1 + 2, addr_x0)
            if split_boundary >= x1 - 10:
                # 分割点太靠右，无法安全拆分 → 保守使用 text 比例
                ratio = len(name_part) / max(len(name_part) + len(addr_part), 1)
                split_boundary = x0 + max(0, (x1 - x0) * ratio)

            it_name = dict(it)
            it_name["text"] = name_part
            it_name["x1"] = split_boundary - 1

            it_addr = dict(it)
            it_addr["text"] = addr_part
            it_addr["x0"] = split_boundary
            it_addr["x1"] = x1

            # 保留原始溯源信息
            for item in (it_name, it_addr):
                item["_source_item_ids"] = list(item.get("_source_item_ids") or [])

            new_items.append(it_name)
            new_items.append(it_addr)

        if any_split:
            modified = True
            new_row = dict(row)
            new_row["items"] = new_items
            rows_copy.append(new_row)
        else:
            rows_copy.append(row)
    return rows_copy if modified else body


def _try_detect_name_addr_merge(text: str) -> Optional[Tuple[str, str]]:
    """检测文本是否包含「机构名 + 地址」合并模式。

    返回 (name_part, addr_part) 或 None。
    """
    t = str(text or "").strip()
    if len(t) < 10:
        return None

    m = _NAME_SUFFIX_RE.search(t)
    if not m:
        return None

    split_pos = m.end()
    name_part = t[:split_pos].strip()
    addr_part = t[split_pos:].strip()

    if len(name_part) < 4 or len(addr_part) < 4:
        return None
    if not re.search(r"[\u4e00-\u9fff]", addr_part):
        return None
    if not re.search(r"(?:[路街号市区镇层栋幢厦])", addr_part):
        return None
    if not (_ADDR_START_STRONG_RE.match(addr_part) or re.match(r"^\d", addr_part)):
        return None

    return (name_part, addr_part)


def _try_split_org_name_address(text: str) -> List[str]:
    """_try_detect_name_addr_merge 的 List-wrapping 便利接口。"""
    result = _try_detect_name_addr_merge(text)
    return list(result) if result else [text]


def _infer_label_lead_column(body: List[dict], data_header_xs: List[float]) -> List[float]:
    """序号列 + 一个或多个左对齐文本列（机构名称/机构地址等）。"""
    if not data_header_xs:
        return []
    first_data_x = min(data_header_xs)
    all_left_x0: List[float] = []
    for row in body:
        for it in row.get("items") or []:
            x0 = float(it.get("x0", 0))
            if x0 >= first_data_x - 20:
                continue
            t = str(it.get("text", "")).strip()
            if not t:
                continue
            if _SERIAL_RE.match(t) and x0 < 100:
                all_left_x0.append(x0)
            elif re.search(r"[\u4e00-\u9fff]", t) and not is_numeric_data_cell(t):
                all_left_x0.append(x0)
    if not all_left_x0:
        return []

    clusters = _dedupe_sorted_xs(all_left_x0, 14.0)
    hits: dict[float, int] = {c: 0 for c in clusters}
    for x in all_left_x0:
        for c in clusters:
            if abs(x - c) <= 14.0:
                hits[c] += 1
                break
    kept = [
        c for c in clusters
        if hits[c] >= 3 or (c == min(clusters) and hits[c] >= 1)
    ]
    kept = _merge_indent_lead_xs(kept)
    return sorted(kept)


def _merge_indent_lead_xs(lead_xs: List[float]) -> List[float]:
    """缩进子项 x0 与顶格标签 x0 间距较小时，合并为同一标签列（勿拆两列）。"""
    if len(lead_xs) < 2:
        return lead_xs
    ordered = sorted(lead_xs)
    if ordered[-1] - ordered[0] <= 40.0:
        return [ordered[0]]
    return ordered


def _infer_lead_columns(body: List[dict], header_xs: List[float]) -> List[float]:
    """序号列 + 一个或多个左对齐文本列。"""
    return _infer_label_lead_column(body, header_xs)


def col_index_for_item(
    x0: float,
    x1: float,
    col_ranges: List[Tuple[float, float]],
) -> int:
    return col_index_by_anchor(x0, x1, "", col_ranges)


def _layout_ranges_need_override(
    col_ranges: List[Tuple[float, float]],
    rows: List[dict],
) -> bool:
    """专表 layout 列界无表体/表头违反时才允许几何网格覆盖。"""
    if not col_ranges or len(col_ranges) < 2:
        return True
    if _merged_numeric_violations(rows, col_ranges) > 0:
        return True
    if _ranges_merge_distinct_headers(col_ranges, rows):
        return True
    if _stage_column_merge_violations(rows, col_ranges) > 0:
        return True
    if _value_column_header_merge_violations(rows, col_ranges) > 0:
        return True
    if _serial_label_merge_violations(rows, col_ranges) > 0:
        return True
    if _left_text_column_merge_violations(rows, col_ranges) > 0:
        return True
    return False


def merge_grid_with_layout(
    grid: Optional[GridInference],
    layout_id: str,
    layout_ranges: List[Tuple[float, float]],
    layout_confidence: float,
    *,
    rows: Optional[List[dict]] = None,
    x_lo: float = 0.0,
    x_hi: float = 600.0,
) -> Tuple[List[Tuple[float, float]], str, dict]:
    """几何网格与 layout 插件结果合并：高置信专表保留插件，其余优先网格。"""
    meta: dict = {}
    pillar_keep = layout_confidence >= 0.85 and layout_id in (
        "pillar_cc1",
        "pillar_cc2",
        "pillar_ccrf",
        "pillar_sec1",
        "pillar_dsib",
        "pillar_disclosure",
        "pillar_gsib",
    )

    def _emit_grid(g: GridInference) -> Tuple[List[Tuple[float, float]], str, dict]:
        meta["grid_inference"] = {
            "confidence": g.confidence,
            "method": g.method,
            "col_lines": g.col_lines,
            "column_centers": g.column_centers,
        }
        out_id = (
            "constraint_grid"
            if layout_id in ("pillar_disclosure", "generic")
            else layout_id
        )
        return g.col_ranges, out_id, meta

    def _candidate_score(
        ranges: List[Tuple[float, float]],
        *,
        is_layout: bool,
        candidate_id: str = "",
    ) -> float:
        if not rows or len(ranges) < 2:
            return -1.0
        body = body_rows_for_layout(rows) or rows
        score = _score_col_ranges(ranges, rows, body_rows=body, include_body_fit=False)
        if candidate_id == "numeric_gutter":
            score += _DBCG_BODY_FIRST_BONUS
        if is_layout:
            bonus = {
                "pillar_cc1": 15.0,
                "pillar_cc2": 15.0,
                "pillar_ccrf": 14.0,
                "pillar_sec1": 12.0,
                "pillar_dsib": 12.0,
                "pillar_gsib": 12.0,
            }.get(layout_id, 0.0)
            score += bonus
        return score

    def _pick_best_candidate(
        candidates: List[Tuple[List[Tuple[float, float]], str, bool]],
    ) -> Optional[Tuple[List[Tuple[float, float]], str]]:
        """网格优先：在全部候选中选最高分；专表 layout 享有小幅加分。"""
        if not rows or not candidates:
            return None
        layout_score = -1.0
        for ranges, _cid, is_layout in candidates:
            if is_layout:
                layout_score = _candidate_score(ranges, is_layout=True, candidate_id="layout")
                break
        pillar_guard = (
            layout_confidence >= 0.85
            and layout_id in pillar_layout_ids
        )
        best: Optional[Tuple[List[Tuple[float, float]], str]] = None
        best_score = -1.0
        for ranges, cid, is_layout in candidates:
            sc = _candidate_score(ranges, is_layout=is_layout, candidate_id=cid)
            if (
                pillar_guard
                and not is_layout
                and layout_score >= 0
                and sc < layout_score + _PILLAR_LAYOUT_MIN_BEAT
            ):
                continue
            if sc > best_score:
                best_score = sc
                best = (ranges, cid)
        return best

    pillar_layout_ids = (
        "pillar_cc1",
        "pillar_cc2",
        "pillar_ccrf",
        "pillar_sec1",
        "pillar_dsib",
        "pillar_disclosure",
        "pillar_gsib",
    )
    if (
        rows
        and layout_confidence >= 0.80
        and layout_id in pillar_layout_ids
        and not _layout_ranges_need_override(layout_ranges, rows)
    ):
        return layout_ranges, layout_id, meta

    grid_candidates: List[Tuple[List[Tuple[float, float]], str, bool]] = []
    if grid and grid.confidence >= 0.55:
        grid_candidates.append((grid.col_ranges, "grid", False))
    if rows:
        gutter = _ranges_from_numeric_gutters(rows, _all_layout_items(rows), x_lo, x_hi)
        if gutter and len(gutter) >= 2:
            grid_candidates.append((gutter, "numeric_gutter", False))
        refined = refine_col_ranges_by_coordinates(rows, list(layout_ranges), x_lo, x_hi)
        if refined:
            grid_candidates.append((refined, "refined_layout", False))
        if grid and grid.col_ranges:
            grid_refined = refine_col_ranges_by_coordinates(
                rows, list(grid.col_ranges), x_lo, x_hi,
            )
            if grid_refined and grid_refined != grid.col_ranges:
                grid_candidates.append((grid_refined, "refined_grid", False))
    grid_candidates.append((layout_ranges, "layout", True))

    picked = _pick_best_candidate(grid_candidates)
    if picked and rows:
        picked_ranges, picked_id = picked
        if picked_id == "grid" and grid:
            return _emit_grid(grid)
        if picked_id != "layout":
            meta["grid_inference"] = {"method": picked_id, "col_count": len(picked_ranges)}
            out_id = (
                "constraint_grid"
                if layout_id in ("pillar_disclosure", "generic")
                else layout_id
            )
            return picked_ranges, out_id, meta

    if pillar_keep:
        if grid and grid.confidence >= 0.55:
            if grid.method in _ANCHOR_GRID_METHODS:
                return _emit_grid(grid)
            if grid.col_count > len(layout_ranges):
                return _emit_grid(grid)
        return layout_ranges, layout_id, meta

    if grid and grid.confidence >= 0.55:
        if grid.method in _ANCHOR_GRID_METHODS:
            return _emit_grid(grid)
        prefer_grid = (
            layout_id == "generic" and grid.col_count > len(layout_ranges)
        )
        if prefer_grid:
            return _emit_grid(grid)

    return layout_ranges, layout_id, meta


def _anchor_x(it: dict) -> float:
    return item_column_anchor(it)


def _merge_close_centers(centers: List[float], tol: float) -> List[float]:
    if not centers:
        return []
    ordered = sorted(centers)
    merged = [ordered[0]]
    for c in ordered[1:]:
        if c - merged[-1] <= tol:
            merged[-1] = (merged[-1] + c) / 2.0
        else:
            merged.append(c)
    return merged


def _prune_centers_by_support(
    centers: List[float],
    body_rows: List[dict],
    items: List[dict],
    *,
    min_ratio: float = _MIN_ROW_SUPPORT_RATIO,
) -> List[float]:
    if len(centers) <= _MIN_COLS:
        return centers
    n_rows = max(len(body_rows), 1)
    kept: List[float] = []
    for c in centers:
        support = sum(
            1
            for row in body_rows
            if any(
                abs(_anchor_x(it) - c) <= _X_CLUSTER_TOL + 8
                for it in row.get("items") or []
            )
        )
        if support / n_rows >= min_ratio:
            kept.append(c)
        elif not kept:
            kept.append(c)
        else:
            nearest = min(kept, key=lambda k: abs(k - c))
            kept = [
                (k + c) / 2.0 if k == nearest else k
                for k in kept
            ]
    return kept if len(kept) >= _MIN_COLS else centers


def _row_vote_weight(row: dict) -> float:
    items = row.get("items") or []
    if not items:
        return 0.0
    w = 1.0
    if len(items) >= 2:
        w += 0.5
    if len(items) >= 3:
        w += 1.0
    has_num = any(
        is_numeric_data_cell(str(it.get("text", "")).strip())
        and float(it.get("x0", 0)) > 180
        for it in items
    )
    has_serial = any(
        _SERIAL_RE.match(str(it.get("text", "")).strip())
        and float(it.get("x0", 0)) < 120
        for it in items
    )
    if has_num:
        w += 1.0
    if has_serial and has_num:
        w += 1.5
    return w


def _cluster_weighted_x0(points: List[Tuple[float, float]], tol: float) -> List[float]:
    ordered = sorted(points, key=lambda p: p[0])
    clusters: List[List[Tuple[float, float]]] = [[ordered[0]]]
    for x0, wt in ordered[1:]:
        if x0 - clusters[-1][-1][0] <= tol:
            clusters[-1].append((x0, wt))
        else:
            clusters.append([(x0, wt)])

    centers: List[float] = []
    for cluster in clusters:
        tw = sum(w for _, w in cluster)
        if tw <= 0:
            continue
        centers.append(sum(x * w for x, w in cluster) / tw)
    return centers


def _centers_to_col_lines(
    centers: List[float],
    items: List[dict],
    x_lo: float,
    x_hi: float,
) -> List[float]:
    xs = sorted(centers)
    bounds = [x_lo]
    for i in range(len(xs) - 1):
        b = _gutter_x(xs[i], xs[i + 1], items)
        b = _snap_valid_line(b, items)
        bounds.append(b)
    bounds.append(x_hi)
    return bounds


def _gutter_x(c_left: float, c_right: float, items: List[dict]) -> float:
    """两列锚点之间的沟：用区间内全部 item 的 x1/x0，避免表头子列被错并。"""
    mid = (c_left + c_right) / 2.0
    left_items = [
        it for it in items
        if float(it.get("x0", 0)) < mid and abs(_anchor_x(it) - c_left) <= _X_CLUSTER_TOL + 20
    ]
    right_items = [
        it for it in items
        if float(it.get("x0", 0)) >= mid and abs(_anchor_x(it) - c_right) <= _X_CLUSTER_TOL + 20
    ]
    if not left_items or not right_items:
        left_items = [it for it in items if abs(float(it.get("x0", 0)) - c_left) <= _X_CLUSTER_TOL + 8]
        right_items = [it for it in items if abs(float(it.get("x0", 0)) - c_right) <= _X_CLUSTER_TOL + 8]
    gap_lo = max((float(it.get("x1", 0)) for it in left_items), default=c_left)
    gap_hi = min((float(it.get("x0", 0)) for it in right_items), default=c_right)
    if gap_hi > gap_lo + 1.0:
        return (gap_lo + gap_hi) / 2.0
    return (c_left + c_right) / 2.0


def _line_cuts_item(x: float, it: dict) -> bool:
    return float(it.get("x0", 0)) + _LINE_EPS < x < float(it.get("x1", 0)) - _LINE_EPS


def _snap_valid_line(x: float, items: List[dict]) -> float:
    if not any(_line_cuts_item(x, it) for it in items):
        return x
    for delta in [0.5, 1, 2, 3, 5, 8, 12, 16, 20, 30]:
        for sign in (-1, 1):
            cand = x + sign * delta
            if not any(_line_cuts_item(cand, it) for it in items):
                return cand
    return x


def _score_grid(
    body_rows: List[dict],
    items: List[dict],
    centers: List[float],
    ranges: List[Tuple[float, float]],
) -> float:
    if not centers or not ranges:
        return 0.0
    matched_rows = 0
    for row in body_rows:
        row_items = row.get("items") or []
        if not row_items:
            continue
        ok = 0
        for it in row_items:
            ci = col_index_by_anchor(
                float(it.get("x0", 0)),
                float(it.get("x1", 0)),
                str(it.get("text", "")),
                ranges,
            )
            if 0 <= ci < len(ranges):
                ok += 1
        if row_items and ok == len(row_items):
            matched_rows += 1

    row_ratio = matched_rows / max(len(body_rows), 1)
    col_bonus = min(0.25, 0.06 * len(centers))
    cut_penalty = 0.0
    inner_lines = [ranges[i][1] for i in range(len(ranges) - 1)]
    for line in inner_lines:
        if any(_line_cuts_item(line, it) for it in items):
            cut_penalty += 0.15
    return max(0.0, min(0.95, 0.35 + 0.45 * row_ratio + col_bonus - cut_penalty))
