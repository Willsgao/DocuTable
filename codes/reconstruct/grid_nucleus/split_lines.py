# -*- coding: utf-8 -*-
"""生成行列分割线。"""

from __future__ import annotations

from statistics import median
from typing import List, Tuple

from codes.reconstruct.grid_nucleus.types import ColumnBand, RowCluster


def build_col_lines(bands: List[ColumnBand]) -> List[float]:
    if not bands:
        return []
    lines = [bands[0].left]
    for i in range(len(bands) - 1):
        lines.append((bands[i].right + bands[i + 1].left) / 2.0)
    lines.append(bands[-1].right)
    # 强制严格递增
    for i in range(1, len(lines)):
        if lines[i] <= lines[i - 1]:
            lines[i] = lines[i - 1] + 1.0
    return lines


def build_row_lines(rows: List[RowCluster]) -> List[float]:
    if not rows:
        return []
    tops, bottoms = [], []
    for r in rows:
        if not r.nuclei:
            tops.append(r.cy - 5)
            bottoms.append(r.cy + 5)
            continue
        tops.append(median([n.y0 for n in r.nuclei]))
        bottoms.append(median([n.y1 for n in r.nuclei]))
    lines = [float(tops[0])]
    for i in range(len(rows) - 1):
        lines.append((float(bottoms[i]) + float(tops[i + 1])) / 2.0)
    lines.append(float(bottoms[-1]))
    for i in range(1, len(lines)):
        if lines[i] <= lines[i - 1]:
            lines[i] = lines[i - 1] + 1.0
    return lines
