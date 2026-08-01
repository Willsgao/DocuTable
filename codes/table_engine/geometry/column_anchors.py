# -*- coding: utf-8 -*-
"""列锚点：标签带左对齐 (x0)，数值带右对齐 (x1)，跨列标题用中心。"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import List, Optional, Sequence, Tuple

from codes.table_engine.geometry.layout_rows import body_rows_for_layout
from codes.table_engine.geometry.numeric import (
    contains_numeric_data,
    is_month_day_cell,
    is_numeric_data_cell,
    is_quarter_column_header_text,
    is_report_date_cell,
    is_year_cell,
)

_X1_CLUSTER_TOL = 28.0
_NUMERIC_MIN_X0 = 150.0
_VALUE_BAND_X0_SLACK = 8.0
_REPORT_PERIOD_RE = re.compile(
    r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_STAGE_COL_RE = re.compile(r"^阶段[一二三1-3]$")
_ECL_SUBHEADER_LABELS = frozenset({"12个月", "整个存续期", "预期信用损失"})
_ENTITY_SCOPE_LABELS = frozenset({"本集团", "本行"})
_DASH_VALUES = frozenset(("-", "－", "—", "–"))
_VALUE_HEADER_MAX_LEN = 20
_LABEL_WRAP_X0_MARGIN = 100.0
_MID_LABEL_CLUSTER_TOL = 12.0
_MID_LABEL_MIN_SUPPORT = 3
_MID_LABEL_HEADER_MAX_LEN = 14
_MID_LABEL_MIN_WIDTH_SPAN = 15.0
_FOOTNOTE_MARKER_RE = re.compile(r"^\([ivxlc]+\)$", re.I)
_PD_RANGE_RE = re.compile(
    r"^\[(?:\d+\.\d+|\d+)[,\s]*(?:\d+\.\d+|\d+)?[)\]]",
)
_PD_DEFAULT_RE = re.compile(r"^100[（(]违约[）)]")
_PD_SUBTOTAL_RE = re.compile(r"^小计$")


def is_pd_range_cell_text(text: str) -> bool:
    """CR6 违约概率区间列：如 [0.00,0.15)、100（违约）、小计。"""
    t = str(text or "").strip()
    if not t:
        return False
    if _PD_SUBTOTAL_RE.match(t):
        return True
    if _PD_DEFAULT_RE.match(t):
        return True
    if _PD_RANGE_RE.match(t):
        return True
    if t.startswith("[") and ")" in t:
        return True
    return False


def _body_amount_column_x0_min(
    body: List[dict],
    *,
    value_x_min: float = _NUMERIC_MIN_X0,
) -> float:
    xs: List[float] = []
    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not is_numeric_data_cell(t):
                continue
            x0 = float(it.get("x0", 0))
            if x0 >= value_x_min + 80:
                xs.append(x0)
    return min(xs) if xs else value_x_min + 220.0


def infer_mid_label_column_x0_clusters(
    rows: List[dict],
    *,
    value_x_min: float = _NUMERIC_MIN_X0,
    cluster_tol: float = _MID_LABEL_CLUSTER_TOL,
    min_support: int = _MID_LABEL_MIN_SUPPORT,
) -> List[float]:
    """可变宽左对齐描述列（如所属行业）：多行共享 x0、文本宽度不一。"""
    body = body_rows_for_layout(rows) or rows
    if not body:
        return []

    amount_x0_min = _body_amount_column_x0_min(body, value_x_min=value_x_min)
    mid_hi = amount_x0_min - 30.0
    mid_lo = value_x_min - 20.0

    header_seeds: List[float] = []
    for row in rows[:10]:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            x0 = float(it.get("x0", 0))
            if not (4 <= len(t) <= _MID_LABEL_HEADER_MAX_LEN and _CJK_RE.search(t)):
                continue
            if is_numeric_data_cell(t) or is_report_period_cell(t):
                continue
            if mid_lo <= x0 < mid_hi:
                header_seeds.append(x0)

    widths_by_q: dict[int, List[float]] = defaultdict(list)
    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", 0))
            if not t or not _CJK_RE.search(t) or is_numeric_data_cell(t):
                continue
            if is_report_period_cell(t):
                continue
            if not (mid_lo <= x0 < mid_hi):
                continue
            q = int(round(x0 / cluster_tol))
            widths_by_q[q].append(max(0.0, x1 - x0))

    clusters: List[float] = []
    body_min_x0 = min(
        (float(it.get("x0", 0)) for row in body for it in row.get("items") or []
         if str(it.get("text", "")).strip() and _CJK_RE.search(str(it.get("text", "")))),
        default=0.0,
    )
    for q, widths in widths_by_q.items():
        if len(widths) < min_support:
            continue
        cluster_x = q * cluster_tol
        # 仅比顶格 x0 右移一点、且无列头种子 → 缩进子项，非独立描述列
        if cluster_x > body_min_x0 + 6 and abs(cluster_x - body_min_x0) <= 36:
            if not any(abs(cluster_x - hx) <= cluster_tol for hx in header_seeds):
                continue
        if max(widths) - min(widths) < _MID_LABEL_MIN_WIDTH_SPAN and len(widths) < 6:
            continue
        clusters.append(cluster_x)

    for hx in header_seeds:
        if not any(abs(hx - c) <= cluster_tol for c in clusters):
            clusters.append(hx)

    return sorted(set(clusters))


def is_mid_label_column_item(
    it: dict,
    mid_label_x0s: Optional[Sequence[float]] = None,
    *,
    value_x_min: float = _NUMERIC_MIN_X0,
    cluster_tol: float = _MID_LABEL_CLUSTER_TOL,
) -> bool:
    """左对齐描述列：x0 落在表体聚类锚点，长度可变，按 x0 分列。"""
    if not mid_label_x0s:
        return False
    t = str(it.get("text", "")).strip()
    x0 = float(it.get("x0", 0))
    if not t or not _CJK_RE.search(t) or is_numeric_data_cell(t) or t in _DASH_VALUES:
        return False
    if (
        is_stage_column_header_text(t)
        or is_entity_scope_label_text(t)
        or is_report_period_cell(t)
        or is_quarter_column_header_text(t)
        or _FOOTNOTE_MARKER_RE.match(t)
    ):
        return False
    return any(abs(x0 - cx) <= cluster_tol for cx in mid_label_x0s)


def is_label_band_item(
    it: dict,
    *,
    value_x_min: float = _NUMERIC_MIN_X0,
    mid_label_x0s: Optional[Sequence[float]] = None,
) -> bool:
    """行标签/折行科目名：左缘起始于标签列，优先 x0 锚点。"""
    t = str(it.get("text", "")).strip()
    x0 = float(it.get("x0", 0))
    if not t or is_numeric_data_cell(t) or t in _DASH_VALUES:
        return False
    if (
        is_stage_column_header_text(t)
        or is_entity_scope_label_text(t)
        or is_report_period_cell(t)
        or is_quarter_column_header_text(t)
        or _FOOTNOTE_MARKER_RE.match(t)
    ):
        return False
    if is_mid_label_column_item(
        it, mid_label_x0s, value_x_min=value_x_min,
    ):
        return True
    if x0 < value_x_min:
        return True
    if (
        _CJK_RE.search(t)
        and len(t) > _VALUE_HEADER_MAX_LEN
        and x0 < value_x_min + _LABEL_WRAP_X0_MARGIN
    ):
        return True
    return False


def default_value_x_min() -> float:
    return _NUMERIC_MIN_X0


def in_value_band(x0: float, *, value_x_min: float = _NUMERIC_MIN_X0) -> bool:
    """x0 落入数值带（标签列以左、金额列以右）。"""
    return float(x0) >= value_x_min - _VALUE_BAND_X0_SLACK


def is_stage_column_header_text(text: str) -> bool:
    return bool(_STAGE_COL_RE.match(str(text or "").strip()))


def is_entity_scope_label_text(text: str) -> bool:
    return str(text or "").strip() in _ENTITY_SCOPE_LABELS


_KNOWN_VALUE_COL_HEADERS = frozenset({
    "账面余额", "账面价值", "公允价值", "摊余成本",
    "占比", "比重", "比例", "金额", "数额", "代码", "期数",
    "增减幅度", "变化幅度", "变化原因", "主要原因",
    # 地区分布等：即使 x 偏左也必须当数值列，勿与「地区」并入标签列
    "营业收入", "营业利润", "营业支出", "营业外收入", "营业外支出",
    "比去年增减", "较上年增减", "同比增减", "增减",
})
# 亦可作行标签（季度指标表「项目」列）；仅在数值带才当列表头
_AMBIGUOUS_VALUE_OR_ROW_LABELS = frozenset({
    "营业收入", "营业利润", "营业支出", "营业外收入", "营业外支出",
})


def is_value_column_header_text(
    text: str,
    *,
    x0: Optional[float] = None,
    value_x_min: float = _NUMERIC_MIN_X0,
    mid_label_x0s: Optional[Sequence[float]] = None,
) -> bool:
    """数值区列表头：用于分列与「多表头同列」检测。"""
    t = str(text or "").strip()
    if not t:
        return False
    # 已知数值列表头：即使 x 落在项目列右缘，也不得当项目列标签
    if t in _KNOWN_VALUE_COL_HEADERS:
        # 季报指标表行标签「营业收入」在标签带，不得挤进数值列与金额粘连
        if (
            t in _AMBIGUOUS_VALUE_OR_ROW_LABELS
            and x0 is not None
            and float(x0) < value_x_min - _VALUE_BAND_X0_SLACK
        ):
            return False
        return True
    if is_stage_column_header_text(t) or is_entity_scope_label_text(t):
        return True
    if is_quarter_column_header_text(t):
        return True
    if t.endswith("的预期信用损失"):
        return True
    if x0 is not None and float(x0) >= value_x_min:
        if is_report_period_cell(t) or is_label_band_item(
            {"text": t, "x0": x0, "x1": 0},
            value_x_min=value_x_min,
            mid_label_x0s=mid_label_x0s,
        ):
            return False
        if _CJK_RE.search(t) and len(t) <= _VALUE_HEADER_MAX_LEN:
            return True
    return False


def is_report_period_cell(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if is_report_date_cell(t) or is_year_cell(t) or is_month_day_cell(t):
        return True
    if _REPORT_PERIOD_RE.search(t):
        return True
    return False


def item_column_anchor(
    it: dict,
    *,
    value_x_min: float = _NUMERIC_MIN_X0,
    mid_label_x0s: Optional[Sequence[float]] = None,
) -> float:
    """分列锚点。

    - 标签带文本：左缘 x0
    - 数值带金额/破折号/ECL 子表头：右缘 x1
    - 数值带一般中文表头：右缘 x1（与下方金额右对齐）
    - 跨列报告期、主体列标、阶段列标：中心
    """
    x0 = float(it.get("x0", 0))
    x1 = float(it.get("x1", 0))
    t = str(it.get("text", "")).strip()
    if not t:
        return x0

    if is_stage_column_header_text(t) or is_entity_scope_label_text(t):
        return (x0 + x1) / 2.0

    if is_label_band_item(
        it, value_x_min=value_x_min, mid_label_x0s=mid_label_x0s,
    ):
        return x0

    in_value = float(x0) >= value_x_min

    if is_month_day_cell(t):
        return x1
    if is_year_cell(t) and in_value:
        return x1
    if is_report_period_cell(t):
        return (x0 + x1) / 2.0
    if is_quarter_column_header_text(t) and in_value:
        return x1
    if is_quarter_column_header_text(t):
        return (x0 + x1) / 2.0

    if t in _DASH_VALUES and in_value:
        return x1
    if is_numeric_data_cell(t) and in_value:
        return x1
    if t in _ECL_SUBHEADER_LABELS:
        return x1
    if in_value and _CJK_RE.search(t) and len(t) <= _VALUE_HEADER_MAX_LEN:
        return x1

    return x0


def col_index_by_x0(
    x0: float,
    col_ranges: List[Tuple[float, float]],
) -> int:
    """按 x0 左缘落列（列缝处避免 anchor 容差误判）。"""
    for i, (lo, hi) in enumerate(col_ranges):
        if lo <= x0 <= hi:
            return i
    return min(
        range(len(col_ranges)),
        key=lambda i: min(abs(x0 - col_ranges[i][0]), abs(x0 - col_ranges[i][1])),
    )


def is_item_in_label_column_zone(
    it: dict,
    col_ranges: Sequence[Tuple[float, float]],
    *,
    margin: float = 10.0,
) -> bool:
    """文本列：x0 在首个数值列左缘之前（含缩进子项）。"""
    if not col_ranges:
        return False
    x0 = float(it.get("x0", 0))
    if len(col_ranges) >= 2:
        return x0 < float(col_ranges[1][0]) - margin
    return x0 <= col_ranges[0][1] + margin


def col_index_by_x1(
    x1: float,
    col_ranges: List[Tuple[float, float]],
) -> int:
    """数值右对齐落列：优先按右缘 x1 匹配列界。"""
    for i, (lo, hi) in enumerate(col_ranges):
        if lo - 4 <= x1 <= hi + 6:
            return i
    return min(range(len(col_ranges)), key=lambda i: abs(x1 - col_ranges[i][1]))


def col_index_by_anchor(
    x0: float,
    x1: float,
    text: str,
    col_ranges: List[Tuple[float, float]],
    *,
    value_x_min: float = _NUMERIC_MIN_X0,
    mid_label_x0s: Optional[Sequence[float]] = None,
) -> int:
    anchor = item_column_anchor(
        {"x0": x0, "x1": x1, "text": text},
        value_x_min=value_x_min,
        mid_label_x0s=mid_label_x0s,
    )
    matches: List[Tuple[float, int]] = []
    for i, (lo, hi) in enumerate(col_ranges):
        if lo - 6 <= anchor <= hi + 6:
            mid = (lo + hi) / 2.0
            matches.append((abs(anchor - mid), i))
    if matches:
        return min(matches)[1]
    mids = [(a + b) / 2 for a, b in col_ranges]
    return min(range(len(mids)), key=lambda i: abs(anchor - mids[i]))


def infer_numeric_data_column_splits(
    rows: List[dict],
    *,
    min_x0: float = _NUMERIC_MIN_X0,
    min_clusters: int = 2,
) -> Optional[List[float]]:
    """从 body 数值格的 x1 / x0 沟道推断数据列之间的竖线位置。"""
    body = body_rows_for_layout(rows)
    if not body:
        body = rows

    points: List[Tuple[float, float, float]] = []
    for row in body:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not contains_numeric_data(t):
                continue
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", 0))
            if x0 < min_x0:
                continue
            points.append((x0, x1, _row_weight(row)))

    if len(points) < min_clusters:
        return None

    clusters = _cluster_numeric_points(points)
    clusters = _merge_overlapping_numeric_clusters(clusters)
    if len(clusters) < min_clusters:
        return None

    clusters.sort(key=lambda c: c["x1_mean"])
    splits: List[float] = []
    for i in range(len(clusters) - 1):
        left = clusters[i]
        right = clusters[i + 1]
        gutter = (left["x1_max"] + right["x0_min"]) / 2.0
        splits.append(gutter)
    return splits


def _row_weight(row: dict) -> float:
    n = len(row.get("items") or [])
    return 1.0 + min(2.0, n * 0.25)


def _cluster_numeric_points(
    points: List[Tuple[float, float, float]],
) -> List[dict]:
    ordered = sorted(points, key=lambda p: p[1])
    clusters: List[dict] = []
    for x0, x1, wt in ordered:
        if not clusters or x1 - clusters[-1]["x1_mean"] > _X1_CLUSTER_TOL:
            clusters.append({
                "x0_min": x0,
                "x0_max": x0,
                "x1_min": x1,
                "x1_max": x1,
                "x1_mean": x1,
                "weight": wt,
            })
        else:
            c = clusters[-1]
            c["x0_min"] = min(c["x0_min"], x0)
            c["x0_max"] = max(c["x0_max"], x0)
            c["x1_min"] = min(c["x1_min"], x1)
            c["x1_max"] = max(c["x1_max"], x1)
            total = c["weight"] + wt
            c["x1_mean"] = (c["x1_mean"] * c["weight"] + x1 * wt) / total
            c["weight"] = total
    return clusters


def _clusters_share_numeric_column(left: dict, right: dict) -> bool:
    """右对齐同列：水平区间重叠，或列沟极窄且右缘仍接近。"""
    if right["x0_min"] <= left["x1_max"] + 4.0:
        return True
    gap = right["x0_min"] - left["x1_max"]
    if gap >= 22.0:
        return False
    return abs(right["x1_mean"] - left["x1_mean"]) <= 36.0


def _merge_overlapping_numeric_clusters(clusters: List[dict]) -> List[dict]:
    """位数不同导致 x0 参差、右缘仍同列时，勿拆成多条沟道。"""
    if len(clusters) < 2:
        return clusters
    merged: List[dict] = [clusters[0]]
    for c in clusters[1:]:
        prev = merged[-1]
        if not _clusters_share_numeric_column(prev, c):
            merged.append(c)
            continue
        total = prev["weight"] + c["weight"]
        prev["x0_min"] = min(prev["x0_min"], c["x0_min"])
        prev["x0_max"] = max(prev["x0_max"], c["x0_max"])
        prev["x1_min"] = min(prev["x1_min"], c["x1_min"])
        prev["x1_max"] = max(prev["x1_max"], c["x1_max"])
        prev["x1_mean"] = (prev["x1_mean"] * prev["weight"] + c["x1_mean"] * c["weight"]) / total
        prev["weight"] = total
    return merged
