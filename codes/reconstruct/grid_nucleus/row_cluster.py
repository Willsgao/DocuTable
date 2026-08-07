# -*- coding: utf-8 -*-
"""按中心 Y 聚类成行。"""

from __future__ import annotations

from statistics import median
from typing import List

from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster


def cluster_rows(
    nuclei: List[Nucleus],
    *,
    gap_factor: float = 0.65,
) -> List[RowCluster]:
    """按中心 Y 聚类成行。

    财报正文字高≈行距时，gap_factor≥1 会把相邻两行并成一行（如 30/31），
    默认 0.65；并参考最近邻中心距估计行距。
    """
    if not nuclei:
        return []
    heights = [n.height for n in nuclei if n.height > 0]
    h_med = median(heights) if heights else 10.0

    ordered = sorted(nuclei, key=lambda n: n.cy)
    # 估计行距：相邻字块中心距的中位数（过滤过近的同字）
    nn_gaps: List[float] = []
    for i in range(1, len(ordered)):
        d = ordered[i].cy - ordered[i - 1].cy
        if d >= max(2.0, h_med * 0.35):
            nn_gaps.append(d)
    pitch = median(nn_gaps) if nn_gaps else h_med
    # 阈值须明显小于行距，否则并行走样
    gap = min(
        max(3.0, h_med * float(gap_factor)),
        max(3.0, pitch * 0.55),
    )

    clusters: List[List[Nucleus]] = []
    centers: List[float] = []

    for n in ordered:
        if not clusters:
            clusters.append([n])
            centers.append(n.cy)
            continue
        if abs(n.cy - centers[-1]) <= gap:
            clusters[-1].append(n)
            # 更新簇中心为均值
            centers[-1] = sum(x.cy for x in clusters[-1]) / len(clusters[-1])
        else:
            clusters.append([n])
            centers.append(n.cy)

    rows: List[RowCluster] = []
    for i, (grp, cy) in enumerate(sorted(zip(clusters, centers), key=lambda t: t[1])):
        grp_sorted = sorted(grp, key=lambda n: n.x0)
        for n in grp_sorted:
            n.row_id = i
        rows.append(RowCluster(row_id=i, cy=cy, nuclei=grp_sorted))
    return rows
