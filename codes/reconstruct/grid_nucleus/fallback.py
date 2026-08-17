# -*- coding: utf-8 -*-
"""退化方案：表头法 / 中点法 / 投影法。"""

from __future__ import annotations

from statistics import median
from typing import List, Optional, Tuple

from codes.reconstruct.grid_nucleus.assign_cells import assign_to_grid
from codes.reconstruct.grid_nucleus.column_infer import (
    assign_nuclei_to_slots,
    compact_unused_column_ids,
    compute_column_bands,
    infer_column_slots,
    mark_abnormal_rows,
)
from codes.reconstruct.grid_nucleus.preprocess import is_amount_nucleus
from codes.reconstruct.grid_nucleus.split_lines import build_col_lines, build_row_lines
from codes.reconstruct.grid_nucleus.types import ColumnBand, Nucleus, RowCluster
from codes.reconstruct.grid_nucleus.validate import validate_grid


def _projection_lines(coords: List[float], *, bins: int = 40) -> List[float]:
    if len(coords) < 2:
        return []
    lo, hi = min(coords), max(coords)
    if hi - lo < 1e-6:
        return [lo, hi + 1.0]
    width = (hi - lo) / bins
    hist = [0] * bins
    for v in coords:
        i = min(bins - 1, max(0, int((v - lo) / width)))
        hist[i] += 1
    # 谷：低于邻居
    valleys = []
    for i in range(1, bins - 1):
        if hist[i] <= hist[i - 1] and hist[i] <= hist[i + 1] and hist[i] < max(hist) * 0.3:
            valleys.append(lo + (i + 0.5) * width)
    lines = [lo] + valleys + [hi]
    # 去重近似
    out = [lines[0]]
    for x in lines[1:]:
        if x - out[-1] > width * 0.8:
            out.append(x)
    return out


def fallback_header(
    rows: List[RowCluster],
    *,
    col_gap_factor: float,
    max_cols: int,
) -> Tuple[List[ColumnBand], List[float], List[float], List[List[str]], str]:
    # 取块数最多的行当边界样本
    sample = sorted(rows, key=lambda r: len(r.nuclei), reverse=True)[:2]
    n_cols, centers = infer_column_slots(sample or rows, col_gap_factor=col_gap_factor, max_cols=max_cols)
    assign_nuclei_to_slots(rows, centers)
    n_cols = compact_unused_column_ids(rows, n_cols)
    for r in rows:
        r.is_abnormal = False
        r.role = "body"
    bands = compute_column_bands(rows, n_cols)
    col_lines = build_col_lines(bands)
    row_lines = build_row_lines(rows)
    data = assign_to_grid(rows, n_cols=n_cols, col_lines=col_lines)
    return bands, row_lines, col_lines, data, "header"


def fallback_midpoint(
    rows: List[RowCluster],
) -> Tuple[List[ColumnBand], List[float], List[float], List[List[str]], str]:
    # 用块数最多行的核中心中点
    best = max(rows, key=lambda r: len(r.nuclei)) if rows else None
    if not best or len(best.nuclei) < 2:
        return [], [], [], [], "midpoint"
    xs = sorted(n.cx for n in best.nuclei)
    col_lines = [best.nuclei[0].x0]
    for i in range(len(xs) - 1):
        col_lines.append((xs[i] + xs[i + 1]) / 2.0)
    col_lines.append(max(n.x1 for n in best.nuclei))
    n_cols = len(col_lines) - 1
    # 分配
    for r in rows:
        for n in r.nuclei:
            n.col_id = 0
            for c in range(n_cols):
                if col_lines[c] <= n.cx < col_lines[c + 1]:
                    n.col_id = c
                    break
            else:
                n.col_id = n_cols - 1
        r.is_abnormal = False
        r.role = "body"
    bands = [
        ColumnBand(col_id=c, left=col_lines[c], right=col_lines[c + 1])
        for c in range(n_cols)
    ]
    row_lines = build_row_lines(rows)
    data = assign_to_grid(rows, n_cols=n_cols, col_lines=col_lines)
    return bands, row_lines, col_lines, data, "midpoint"


def fallback_projection(
    nuclei: List[Nucleus],
    rows: List[RowCluster],
) -> Tuple[List[ColumnBand], List[float], List[float], List[List[str]], str]:
    xs = [n.cx for n in nuclei]
    ys = [n.cy for n in nuclei]
    col_lines = _projection_lines(xs)
    row_lines = _projection_lines(ys)
    if len(col_lines) < 2 or len(row_lines) < 2:
        return [], [], [], [], "projection"
    n_cols = len(col_lines) - 1
    # 重建 rows 的 col_id
    for r in rows:
        for n in r.nuclei:
            n.col_id = 0
            for c in range(n_cols):
                if col_lines[c] <= n.cx < col_lines[c + 1]:
                    n.col_id = c
                    break
            else:
                n.col_id = n_cols - 1
        r.is_abnormal = False
        r.role = "body"
    bands = [
        ColumnBand(col_id=c, left=col_lines[c], right=col_lines[c + 1])
        for c in range(n_cols)
    ]
    # 若投影行线与聚类行不一致，仍用聚类行线更稳
    row_lines2 = build_row_lines(rows)
    data = assign_to_grid(rows, n_cols=n_cols, col_lines=col_lines)
    return bands, row_lines2, col_lines, data, "projection"


def try_fallbacks(
    nuclei: List[Nucleus],
    rows: List[RowCluster],
    *,
    col_gap_factor: float,
    max_cols: int,
    cover_thresh: float,
    reasons: List[str],
) -> Tuple[Optional[object], List[str]]:
    """依次尝试退化；返回 (类似中间结果的 tuple 或 None, chain)。"""
    chain = list(reasons)
    attempts = []

    def _pack(bands, row_lines, col_lines, data, method):
        ok, errs, metrics = validate_grid(
            rows, data, row_lines, col_lines, cover_thresh=cover_thresh * 0.9,
            max_cols=max_cols,
        )
        return ok, bands, row_lines, col_lines, data, method, errs, metrics

    # body 少 → header
    body_n = sum(1 for r in rows if r.role == "body" and not r.is_abnormal)
    if body_n < 3:
        chain.append("header")
        attempts.append(fallback_header(rows, col_gap_factor=col_gap_factor, max_cols=max_cols))

    chain.append("midpoint")
    attempts.append(fallback_midpoint(rows))

    chain.append("projection")
    attempts.append(fallback_projection(nuclei, rows))

    for bands, row_lines, col_lines, data, method in attempts:
        if not data or not col_lines:
            continue
        ok, bands, row_lines, col_lines, data, method, errs, metrics = _pack(
            bands, row_lines, col_lines, data, method
        )
        if ok:
            return (ok, bands, row_lines, col_lines, data, method, errs, metrics, chain), chain
    return None, chain
