# -*- coding: utf-8 -*-
"""列边界与落列：标签左对齐(x0)、数值右对齐(x1)、表头可居中。

供 V2 Step1 与 processor 共用，避免仅用 x0 把右对齐数值列拆成多列。
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from codes.table_engine.geometry.numeric import is_numeric_data_cell

_DASH_VALUES = frozenset(("-", "－", "—", "–"))
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_REPORT_PERIOD_RE = re.compile(
    r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月\s*\d{1,2}\s*日)?"
)
_VALUE_X1_CLUSTER_TOL = 28.0
_DEFAULT_VALUE_X_MIN = 150.0
_LABEL_OVERLAP_SLACK = 6.0
_LABEL_INDENT_MERGE_GAP = 28.0


def is_value_word(text: str) -> bool:
    t = str(text or "").strip()
    return bool(t) and (is_numeric_data_cell(t) or t in _DASH_VALUES)


def is_center_header_word(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t) > 24:
        return False
    if _REPORT_PERIOD_RE.search(t):
        return True
    if re.match(r"^阶段[一二三1-3]$", t):
        return True
    if t in ("本集团", "本行", "本银行"):
        return True
    return False


def is_label_word(
    word: Dict[str, Any],
    *,
    value_x_min: float = _DEFAULT_VALUE_X_MIN,
) -> bool:
    """表体/表头中文标签（含缩进折行），非数值格。"""
    text = str(word.get("text", "")).strip()
    if not text:
        return False
    x0 = float(word.get("x0", 0))
    if is_value_word(text) and x0 >= value_x_min - 8:
        return False
    if is_center_header_word(text):
        return False
    return bool(_CJK_RE.search(text)) or (not is_value_word(text) and x0 < value_x_min + 40)


def word_alignment_anchor(
    word: Dict[str, Any],
    *,
    value_x_min: float = _DEFAULT_VALUE_X_MIN,
) -> Tuple[float, str]:
    """返回 (锚点, 模式)：left / right / center。"""
    text = str(word.get("text", "")).strip()
    x0 = float(word.get("x0", 0))
    x1 = float(word.get("x1", x0))
    if is_value_word(text) and x0 >= value_x_min - 8:
        return x1, "right"
    if is_center_header_word(text):
        return (x0 + x1) / 2.0, "center"
    return x0, "left"


def estimate_value_x_min(
    words: Sequence[Dict[str, Any]],
    *,
    default: float = _DEFAULT_VALUE_X_MIN,
) -> float:
    xs = [
        float(w["x0"])
        for w in words
        if is_value_word(str(w.get("text", "")).strip())
        and float(w.get("x0", 0)) >= default + 50
    ]
    if len(xs) >= 3:
        return min(xs) - 30.0
    if xs:
        return min(xs) - 40.0
    x0s = sorted(float(w["x0"]) for w in words if str(w.get("text", "")).strip())
    if len(x0s) >= 6:
        gaps = [x0s[i + 1] - x0s[i] for i in range(len(x0s) - 1)]
        if gaps:
            big = max(gaps)
            idx = gaps.index(big)
            if big >= 40 and idx < len(x0s) - 1:
                return x0s[idx] + big * 0.35
    return default + 70.0


def cluster_1d(
    values: Sequence[float],
    tolerance: float,
    *,
    min_samples: int = 3,
) -> List[float]:
    if not values:
        return []
    sorted_vals = sorted(values)
    clusters: List[List[float]] = []
    current = [sorted_vals[0]]
    for v in sorted_vals[1:]:
        if v - current[-1] <= tolerance:
            current.append(v)
        else:
            if len(current) >= min_samples:
                clusters.append(current)
            current = [v]
    if len(current) >= min_samples:
        clusters.append(current)
    return [sum(c) / len(c) for c in clusters]


def _intervals_overlap(
    a: Tuple[float, float],
    b: Tuple[float, float],
    *,
    slack: float = _LABEL_OVERLAP_SLACK,
) -> bool:
    return a[0] <= b[1] + slack and b[0] <= a[1] + slack


def _cluster_label_by_overlap(
    points: Sequence[Tuple[float, float]],
    *,
    min_samples: int = 1,
) -> List[Dict[str, float]]:
    """标签列：横向区间有交叉则同一列（缩进子项不拆列）。"""
    if not points:
        return []
    merged: List[Dict[str, float]] = []
    for x0, x1 in sorted(points, key=lambda p: p[0]):
        placed = False
        for g in merged:
            if _intervals_overlap((x0, x1), (g["x0_min"], g["x1_max"])):
                g["x0_min"] = min(g["x0_min"], x0)
                g["x1_max"] = max(g["x1_max"], x1)
                n = g.get("_n", 1)
                g["x0_mean"] = (g["x0_mean"] * n + x0) / (n + 1)
                g["_n"] = n + 1
                placed = True
                break
        if not placed:
            merged.append({
                "x0_min": x0,
                "x1_max": x1,
                "x0_mean": x0,
                "_n": 1,
            })

    # 缩进仅 x0 右移、区间仍相连 → 合并
    changed = True
    while changed and len(merged) > 1:
        changed = False
        merged.sort(key=lambda g: g["x0_min"])
        out: List[Dict[str, float]] = [merged[0]]
        for g in merged[1:]:
            prev = out[-1]
            gap = g["x0_min"] - prev["x1_max"]
            if gap <= _LABEL_INDENT_MERGE_GAP or _intervals_overlap(
                (prev["x0_min"], prev["x1_max"]),
                (g["x0_min"], g["x1_max"]),
            ):
                prev["x0_min"] = min(prev["x0_min"], g["x0_min"])
                prev["x1_max"] = max(prev["x1_max"], g["x1_max"])
                n = prev.get("_n", 1) + g.get("_n", 1)
                prev["x0_mean"] = (prev["x0_mean"] * prev.get("_n", 1) + g["x0_mean"] * g.get("_n", 1)) / n
                prev["_n"] = n
                changed = True
            else:
                out.append(g)
        merged = out

    result = [g for g in merged if g.get("_n", 1) >= min_samples]
    for g in result:
        g.pop("_n", None)
    return result


def _cluster_label_x0(
    points: Sequence[Tuple[float, float]],
    tolerance: float,
    *,
    min_samples: int = 2,
) -> List[Dict[str, float]]:
    return _cluster_label_by_overlap(points, min_samples=min_samples)


def _cluster_value_x1(
    points: Sequence[Tuple[float, float]],
    tolerance: float,
    *,
    min_samples: int = 2,
) -> List[Dict[str, float]]:
    if not points:
        return []
    ordered = sorted(points, key=lambda p: p[1])
    clusters: List[Dict[str, float]] = []
    bucket: List[Tuple[float, float]] = [ordered[0]]
    for pt in ordered[1:]:
        if pt[1] - bucket[-1][1] <= tolerance:
            bucket.append(pt)
        else:
            if len(bucket) >= min_samples:
                clusters.append(_value_cluster_stats(bucket))
            bucket = [pt]
    if len(bucket) >= min_samples:
        clusters.append(_value_cluster_stats(bucket))
    return _merge_overlapping_value_clusters(clusters)


def _value_cluster_stats(bucket: Sequence[Tuple[float, float]]) -> Dict[str, float]:
    x0s = [p[0] for p in bucket]
    x1s = [p[1] for p in bucket]
    return {
        "x0_min": min(x0s),
        "x0_max": max(x0s),
        "x1_mean": sum(x1s) / len(x1s),
        "x1_min": min(x1s),
        "x1_max": max(x1s),
    }


def _clusters_share_value_column(left: Dict[str, float], right: Dict[str, float]) -> bool:
    if right["x0_min"] <= left["x1_max"] + 4.0:
        return True
    gap = right["x0_min"] - left["x1_max"]
    if gap >= 22.0:
        return False
    return abs(right["x1_mean"] - left["x1_mean"]) <= 36.0


def _merge_overlapping_value_clusters(
    clusters: List[Dict[str, float]],
) -> List[Dict[str, float]]:
    if len(clusters) < 2:
        return clusters
    merged: List[Dict[str, float]] = [clusters[0]]
    for c in clusters[1:]:
        prev = merged[-1]
        if not _clusters_share_value_column(prev, c):
            merged.append(c)
            continue
        prev["x0_min"] = min(prev["x0_min"], c["x0_min"])
        prev["x0_max"] = max(prev["x0_max"], c["x0_max"])
        prev["x1_min"] = min(prev["x1_min"], c["x1_min"])
        prev["x1_max"] = max(prev["x1_max"], c["x1_max"])
        n_prev = max(prev.get("_n", 1), 1)
        n_c = max(c.get("_n", 1), 1)
        prev["x1_mean"] = (prev["x1_mean"] * n_prev + c["x1_mean"] * n_c) / (n_prev + n_c)
        prev["_n"] = n_prev + n_c
    for c in merged:
        c.pop("_n", None)
    return merged


def detect_bilateral_column_boundaries(
    words: Sequence[Dict[str, Any]],
    page_width: float,
    config: Dict[str, Any],
    *,
    anchor_lines: Optional[Sequence[float]] = None,
    fuse_fn=None,
) -> Optional[List[float]]:
    """双边对齐列界：标签区 x0，数值区 x1（合并同列不同位数）。"""
    tol = float(config.get("align_tolerance", 8.0))
    value_x_min = estimate_value_x_min(words)

    label_pts: List[Tuple[float, float]] = []
    value_pts: List[Tuple[float, float]] = []
    center_xs: List[float] = []

    for w in words:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        x0 = float(w.get("x0", 0))
        x1 = float(w.get("x1", x0))
        if is_center_header_word(text):
            center_xs.append((x0 + x1) / 2.0)
            continue
        if is_value_word(text) and x0 >= value_x_min - 8:
            value_pts.append((x0, x1))
        elif x0 >= value_x_min - 15:
            # 数值区上方的短表头（如「增减幅度」）按居中，勿当第二标签列
            center_xs.append((x0 + x1) / 2.0)
        else:
            label_pts.append((x0, x1))

    label_clusters = _cluster_label_by_overlap(label_pts, min_samples=1)
    value_clusters = _cluster_value_x1(value_pts, tol, min_samples=2)

    splits: List[float] = []

    for i in range(len(label_clusters) - 1):
        a, b = label_clusters[i], label_clusters[i + 1]
        splits.append((a["x0_mean"] + b["x0_mean"]) / 2.0)

    if label_clusters and value_clusters:
        left = label_clusters[-1]
        right = value_clusters[0]
        splits.append((left["x1_max"] + right["x0_min"]) / 2.0)

    for i in range(len(value_clusters) - 1):
        left, right = value_clusters[i], value_clusters[i + 1]
        splits.append((left["x1_max"] + right["x0_min"]) / 2.0)

    if not label_clusters and not value_clusters:
        x0_list = [float(w["x0"]) for w in words if str(w.get("text", "")).strip()]
        left_aligns = cluster_1d(x0_list, tol)
        if len(left_aligns) >= 2:
            boundaries = [0.0]
            for i in range(len(left_aligns) - 1):
                boundaries.append((left_aligns[i] + left_aligns[i + 1]) / 2.0)
            boundaries.append(page_width)
            return _finalize_boundaries(boundaries, page_width, anchor_lines, tol, fuse_fn)

    if not splits:
        return None

    if center_xs and value_clusters:
        center_aligns = cluster_1d(center_xs, tol, min_samples=1)
        for cx in center_aligns:
            for i, vc in enumerate(value_clusters):
                if abs(cx - vc["x1_mean"]) <= tol * 2:
                    break
            else:
                for i in range(len(value_clusters) - 1):
                    if value_clusters[i]["x1_mean"] < cx < value_clusters[i + 1]["x1_mean"]:
                        gutter = (
                            value_clusters[i]["x1_max"] + value_clusters[i + 1]["x0_min"]
                        ) / 2.0
                        if gutter not in splits:
                            splits.append(gutter)
                        break

    boundaries = [0.0] + sorted(splits) + [page_width]
    return _finalize_boundaries(boundaries, page_width, anchor_lines, tol, fuse_fn)


def _finalize_boundaries(
    boundaries: List[float],
    page_width: float,
    anchor_lines: Optional[Sequence[float]],
    tolerance: float,
    fuse_fn,
) -> List[float]:
    out = sorted(set(boundaries))
    if out[0] > 0:
        out.insert(0, 0.0)
    if out[-1] < page_width:
        out.append(page_width)
    if anchor_lines and fuse_fn:
        out = fuse_fn(out, list(anchor_lines), tolerance)
    if len(out) >= 3:
        return out
    return [0.0, page_width]


def _label_zone_col_limit(
    col_bounds: Sequence[float],
    value_x_min: float,
) -> int:
    """数值区起始列索引；其左侧均为标签区。"""
    for c in range(len(col_bounds) - 1):
        if float(col_bounds[c]) >= value_x_min - 12:
            return max(c, 1)
    return len(col_bounds) - 1


def _horizontal_overlap(x0: float, x1: float, lo: float, hi: float) -> float:
    return max(0.0, min(x1, hi) - max(x0, lo))


def col_index_for_word(
    word: Dict[str, Any],
    col_bounds: Sequence[float],
    *,
    value_x_min: Optional[float] = None,
) -> int:
    if value_x_min is None:
        value_x_min = _DEFAULT_VALUE_X_MIN
    n_cols = len(col_bounds) - 1
    if n_cols <= 0:
        return 0

    x0 = float(word.get("x0", 0))
    x1 = float(word.get("x1", x0))
    anchor, mode = word_alignment_anchor(word, value_x_min=value_x_min)

    if is_label_word(word, value_x_min=value_x_min):
        label_limit = _label_zone_col_limit(col_bounds, value_x_min)
        best_c, best_ov = 0, -1.0
        for c in range(label_limit):
            lo = float(col_bounds[c])
            hi = float(col_bounds[c + 1])
            ov = _horizontal_overlap(x0, x1, lo, hi)
            if ov > best_ov:
                best_ov, best_c = ov, c
        if best_ov > 0:
            return best_c
        return 0

    matches: List[Tuple[float, int]] = []
    for c in range(n_cols):
        lo = float(col_bounds[c])
        hi = float(col_bounds[c + 1])
        if mode == "right":
            if lo - 4 <= anchor <= hi + 6:
                matches.append((abs(anchor - hi), c))
        elif mode == "left":
            if lo - 2 <= anchor <= hi + 2:
                matches.append((abs(anchor - lo), c))
        else:
            mid = (lo + hi) / 2.0
            if lo - 6 <= anchor <= hi + 6:
                matches.append((abs(anchor - mid), c))

    if matches:
        return min(matches)[1]

    mids = [(col_bounds[c] + col_bounds[c + 1]) / 2.0 for c in range(n_cols)]
    return min(range(n_cols), key=lambda i: abs(anchor - mids[i]))


def _row_index_for_word(
    center_y: float,
    row_bounds: Sequence[Tuple[float, float]],
    margin_factor: float,
) -> Optional[int]:
    """选与词心最近的行，避免邻行 margin 抢词。"""
    best_r: Optional[int] = None
    best_dist = float("inf")
    for r, (y_top, y_bot) in enumerate(row_bounds):
        row_h = max(y_bot - y_top, 1.0)
        margin = row_h * margin_factor
        if (y_top - margin) <= center_y <= (y_bot + margin):
            mid = (y_top + y_bot) / 2.0
            dist = abs(center_y - mid)
            if dist < best_dist:
                best_dist = dist
                best_r = r
    return best_r


def _label_baseline_x0(
    words: Sequence[Dict[str, Any]],
    col_bounds: Sequence[float],
    value_x_min: float,
    *,
    label_col: int = 0,
) -> float:
    """标签列左对齐基线（最小 x0）。"""
    xs: List[float] = []
    for w in words:
        if not is_label_word(w, value_x_min=value_x_min):
            continue
        if col_index_for_word(w, col_bounds, value_x_min=value_x_min) == label_col:
            xs.append(float(w.get("x0", 0)))
    return min(xs) if xs else 0.0


def indent_level_from_x0(
    x0: float,
    baseline_x0: float,
    config: Dict[str, Any],
) -> int:
    """按 x0 相对基线量化缩进层级。"""
    step = float(config.get("indent_step_pt", 12.0))
    threshold = float(config.get("indent_threshold_pt", 5.0))
    max_level = int(config.get("indent_max_level", 4))
    delta = max(0.0, float(x0) - baseline_x0)
    if delta < threshold:
        return 0
    return min(max_level, max(1, int(round(delta / step))))


def format_label_with_indent(text: str, level: int, config: Dict[str, Any]) -> str:
    if level <= 0:
        return text
    n = int(config.get("indent_spaces_per_level", 2)) * level
    return (" " * n) + text


def assign_words_to_grid(
    words: Sequence[Dict[str, Any]],
    row_bounds: Sequence[Tuple[float, float]],
    col_bounds: Sequence[float],
    config: Dict[str, Any],
) -> Union[List[List[str]], Tuple[List[List[str]], Dict[str, Dict[str, Any]]]]:
    """锚点落列 + 行内按 x0 排序；可选标签列缩进空格 + cell_meta。"""
    data, meta = _assign_words_to_grid_impl(words, row_bounds, col_bounds, config)
    if config.get("preserve_label_indent", True):
        return data, meta
    return data


def _assign_words_to_grid_impl(
    words: Sequence[Dict[str, Any]],
    row_bounds: Sequence[Tuple[float, float]],
    col_bounds: Sequence[float],
    config: Dict[str, Any],
) -> Tuple[List[List[str]], Dict[str, Dict[str, Any]]]:
    n_rows = len(row_bounds)
    n_cols = len(col_bounds) - 1
    if n_rows == 0 or n_cols == 0:
        return []

    value_x_min = estimate_value_x_min(words)
    label_col = 0
    baseline_x0 = _label_baseline_x0(
        words, col_bounds, value_x_min, label_col=label_col,
    )
    preserve_indent = bool(config.get("preserve_label_indent", True))
    label_col = 0

    grid: List[List[List[Tuple[float, str]]]] = [
        [[] for _ in range(n_cols)] for _ in range(n_rows)
    ]

    margin_factor = config.get("row_margin_factor", 0.2)

    for w in words:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        wx0 = float(w["x0"])
        wy0 = float(w["y0"])
        wy1 = float(w["y1"])
        center_y = (wy0 + wy1) / 2.0

        row_idx = _row_index_for_word(center_y, row_bounds, margin_factor)
        if row_idx is None:
            continue

        col_idx = col_index_for_word(w, col_bounds, value_x_min=value_x_min)
        grid[row_idx][col_idx].append((wx0, text))

    cell_meta: Dict[str, Dict[str, Any]] = {}
    result: List[List[str]] = []
    for r in range(n_rows):
        row_data: List[str] = []
        for c in range(n_cols):
            cell_parts = grid[r][c]
            if cell_parts:
                cell_parts.sort(key=lambda p: p[0])
                joined = " ".join(t for _, t in cell_parts)
                if preserve_indent and c == label_col:
                    level = indent_level_from_x0(
                        cell_parts[0][0], baseline_x0, config,
                    )
                    if level > 0:
                        joined = format_label_with_indent(joined, level, config)
                        cell_meta[f"{r},{c}"] = {"indent_level": level}
                row_data.append(joined)
            else:
                row_data.append("")
        result.append(row_data)
    return result, cell_meta


def gap_fallback_boundaries(
    words: Sequence[Dict[str, Any]],
    page_width: float,
    config: Dict[str, Any],
    *,
    anchor_lines: Optional[Sequence[float]] = None,
    fuse_fn=None,
) -> List[float]:
    """gap 兜底：标签用 x0、数值用 x1，避免混用产生伪边界。"""
    value_x_min = estimate_value_x_min(words)
    coords: List[float] = []
    for w in words:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        if is_value_word(text) and float(w["x0"]) >= value_x_min - 8:
            coords.append(float(w["x1"]))
        elif is_center_header_word(text) or float(w["x0"]) >= value_x_min - 15:
            coords.append((float(w["x0"]) + float(w["x1"])) / 2.0)
        else:
            coords.append(float(w["x0"]))

    all_x = sorted(set(coords))
    if len(all_x) < 3:
        return [0.0, page_width]

    gaps = [all_x[i + 1] - all_x[i] for i in range(len(all_x) - 1)]
    median_gap = statistics.median(gaps)
    stdev_gap = statistics.stdev(gaps) if len(gaps) >= 2 else median_gap * 0.5
    gap_threshold = max(
        median_gap + stdev_gap * config.get("gap_factor", 0.3),
        config.get("gap_min", 10.0),
    )

    boundaries = [0.0]
    for i, gap in enumerate(gaps):
        if gap > gap_threshold:
            boundaries.append((all_x[i] + all_x[i + 1]) / 2.0)

    if anchor_lines and fuse_fn:
        boundaries = fuse_fn(boundaries, list(anchor_lines), config.get("gap_min", 10.0))
    else:
        boundaries.append(page_width)

    return sorted(set(boundaries))
