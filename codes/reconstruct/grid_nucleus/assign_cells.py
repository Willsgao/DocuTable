# -*- coding: utf-8 -*-
"""字块落入格子，生成 data 网格。"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

# 同格「75 .21」→「75.21」；「1 .78%」→「1.78%」
_SPLIT_DECIMAL_SPACE_RE = re.compile(r"(\d)\s+(\.\d)")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_AMOUNT_RE = re.compile(r"[\d,]{3,}|\d+\.\d+%?|\(\s*[\d,]+\s*\)")


def _col_from_lines(x: float, col_lines: List[float]) -> int:
    if len(col_lines) < 2:
        return 0
    for c in range(len(col_lines) - 1):
        if col_lines[c] <= x < col_lines[c + 1]:
            return c
    if x < col_lines[0]:
        return 0
    return len(col_lines) - 2


def _should_glue_texts(
    left: str,
    right: str,
    *,
    left_n: Optional[Nucleus] = None,
    right_n: Optional[Nucleus] = None,
) -> bool:
    """整数与小数后缀、连续中文折行、或已以小数点结尾时，中间不加空格。

    粘连拆核（glued_split）须保留空格，供双指标表头再拆列。
    """
    if left_n is not None and right_n is not None:
        if "glued_split" in (left_n.flags or set()) or "glued_split" in (right_n.flags or set()):
            return False
    l = (left or "").rstrip()
    r = (right or "").lstrip()
    if not l or not r:
        return True
    if r.startswith(".") and l[-1].isdigit():
        return True
    if l.endswith(".") and r[:1].isdigit():
        return True
    # 连续中文同核/同格：不因空格或右缘拆开（折行碎片）
    if _CJK_RE.search(l[-1:]) and _CJK_RE.search(r[:1]):
        # 上下折行（y 分离）优先粘；同行且间隙大则仍空格（少见）
        if left_n is not None and right_n is not None:
            same_line = abs(float(left_n.cy) - float(right_n.cy)) <= max(
                float(left_n.height), float(right_n.height), 8.0
            ) * 0.65
            gap = float(right_n.x0) - float(left_n.x1)
            if same_line and gap > 6.0:
                return False
        return True
    return False


def join_cell_nuclei_text(items: List[Nucleus]) -> str:
    """同格多字框拼接：小数拆段与连续中文折行无空格；粘连拆核保留空格。"""
    nuclei = [n for n in items if n.text]
    if not nuclei:
        return ""
    out = str(nuclei[0].text)
    for i in range(1, len(nuclei)):
        prev, cur = nuclei[i - 1], nuclei[i]
        p = str(cur.text)
        if _should_glue_texts(out, p, left_n=prev, right_n=cur):
            out = out.rstrip() + p.lstrip()
        else:
            out = out.rstrip() + " " + p.lstrip()
    return _SPLIT_DECIMAL_SPACE_RE.sub(r"\1\2", out).strip()


def _infer_label_col_from_grid(
    grid: Sequence[Sequence[Sequence[Nucleus]]],
    n_cols: int,
) -> int:
    best_i, best_score = 0, -1e9
    for c in range(n_cols):
        cjk = amt = non_empty = 0
        for row_cells in grid:
            if c >= len(row_cells):
                continue
            for n in row_cells[c]:
                t = str(n.text or "").strip()
                if not t:
                    continue
                non_empty += 1
                if _CJK_RE.search(t):
                    cjk += 1
                if _AMOUNT_RE.search(t):
                    amt += 1
        score = cjk * 3 - amt * 5 + (1 if non_empty else 0)
        if score > best_score:
            best_score = score
            best_i = c
    return best_i


def _indent_prefix_for_items(
    items: Sequence[Nucleus],
    *,
    baseline_x0: float,
    indent_step_pt: float = 12.0,
    indent_threshold_pt: float = 5.0,
    spaces_per_level: int = 2,
    max_level: int = 4,
) -> str:
    if not items:
        return ""
    x0 = min(float(n.x0) for n in items)
    delta = max(0.0, x0 - baseline_x0)
    if delta < indent_threshold_pt:
        return ""
    level = min(max_level, max(1, int(round(delta / indent_step_pt))))
    return " " * (spaces_per_level * level)


def assign_to_grid(
    rows: List[RowCluster],
    *,
    n_cols: int,
    col_lines: List[float],
    preserve_label_indent: bool = True,
) -> List[List[str]]:
    n_rows = len(rows)
    if n_rows <= 0 or n_cols <= 0:
        return []
    grid: List[List[List[Nucleus]]] = [
        [[] for _ in range(n_cols)] for _ in range(n_rows)
    ]
    for r in rows:
        ri = r.row_id
        if ri < 0 or ri >= n_rows:
            continue
        for n in r.nuclei:
            c = n.col_id if 0 <= n.col_id < n_cols else _col_from_lines(n.cx, col_lines)
            c = max(0, min(n_cols - 1, c))
            grid[ri][c].append(n)

    label_col = _infer_label_col_from_grid(grid, n_cols) if preserve_label_indent else -1
    baseline: Optional[float] = None
    if label_col >= 0:
        xs = [
            float(n.x0)
            for row_cells in grid
            for n in (row_cells[label_col] if label_col < len(row_cells) else [])
            if str(n.text or "").strip() and _CJK_RE.search(str(n.text or ""))
        ]
        if xs:
            baseline = min(xs)

    data: List[List[str]] = []
    for ri in range(n_rows):
        row_out: List[str] = []
        for c in range(n_cols):
            items = sorted(grid[ri][c], key=lambda n: (float(n.y0), float(n.x0)))
            text = join_cell_nuclei_text(items)
            if (
                preserve_label_indent
                and c == label_col
                and text
                and baseline is not None
                and items
            ):
                prefix = _indent_prefix_for_items(items, baseline_x0=baseline)
                if prefix:
                    text = prefix + text
            row_out.append(text)
        data.append(row_out)
    return data


def label_x0_per_row(
    rows: List[RowCluster],
    *,
    n_cols: int,
    col_lines: List[float],
) -> List[Optional[float]]:
    """每行科目列最小 x0，供逻辑行缩进/诊断。"""
    n_rows = len(rows)
    if n_rows <= 0 or n_cols <= 0:
        return []
    grid: List[List[List[Nucleus]]] = [
        [[] for _ in range(n_cols)] for _ in range(n_rows)
    ]
    for r in rows:
        ri = r.row_id
        if ri < 0 or ri >= n_rows:
            continue
        for n in r.nuclei:
            c = n.col_id if 0 <= n.col_id < n_cols else _col_from_lines(n.cx, col_lines)
            c = max(0, min(n_cols - 1, c))
            grid[ri][c].append(n)
    lc = _infer_label_col_from_grid(grid, n_cols)
    out: List[Optional[float]] = []
    for ri in range(n_rows):
        items = grid[ri][lc] if lc < n_cols else []
        cjk = [
            n for n in items
            if str(n.text or "").strip() and _CJK_RE.search(str(n.text or ""))
        ]
        out.append(min(float(n.x0) for n in cjk) if cjk else None)
    return out
