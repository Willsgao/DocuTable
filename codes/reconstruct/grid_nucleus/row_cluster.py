# -*- coding: utf-8 -*-
"""按中心 Y 聚类成行。"""

from __future__ import annotations

import re
from statistics import median
from typing import List, Sequence

from codes.reconstruct.grid_nucleus.types import Nucleus, RowCluster

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SERIAL_RE = re.compile(r"^\d{1,3}[a-zA-Z]?$")
_AMOUNT_RE = re.compile(r"^[\(\)\-−–—]?\d[\d,]*(?:\.\d+)?%?$|^-$")


def _is_section_title_nucleus(n: Nucleus) -> bool:
    """独立小节标题核（核心一级资本：/ 核心一级资本：扣除项）。

    「其中：…」是表体层次科目，必须与同行序号/金额同簇，绝不当小节拆行。
    「资产」单独成核时可能是分组标题，也可能是「资产管理计划」折行上半——
    聚类阶段不把短词当小节（避免拆一体核）；真分组由落格后跨列/不并金额行处理。
    """
    t = str(n.text or "").strip()
    if not t:
        return False
    if "：" not in t and ":" not in t:
        return False
    # 其中：/其中: 明细行 ≠ 分组小节标题
    if re.match(r"^其中[：:]", t):
        return False
    cn = len(_CJK_RE.findall(t))
    if cn < 4:
        return False
    # 资本构成分组标题
    if re.match(
        r"^(?:核心一级资本|其他一级资本|二级资本|一级资本)[：:]",
        t,
    ):
        return True
    if "：扣除项" in t or ":扣除项" in t:
        return True
    # 短分组标题以冒号收尾（核心一级资本：）；长「其中：应在…」已排除
    if t.endswith(("：", ":")) and cn <= 12:
        return True
    return False


def _is_data_payload_nucleus(n: Nucleus) -> bool:
    t = str(n.text or "").strip()
    if not t:
        return False
    if _SERIAL_RE.match(t) or _AMOUNT_RE.match(t.replace(",", "")):
        return True
    return False


def _cluster_is_data_row(grp: Sequence[Nucleus]) -> bool:
    """簇里已有序号/金额 → 数据行，不得再并进小节标题。"""
    return any(_is_data_payload_nucleus(n) for n in grp)


def _cluster_is_section_title_only(grp: Sequence[Nucleus]) -> bool:
    texts = [str(n.text or "").strip() for n in grp if str(n.text or "").strip()]
    if not texts:
        return False
    return all(_is_section_title_nucleus(n) for n in grp if str(n.text or "").strip())


def cluster_rows(
    nuclei: List[Nucleus],
    *,
    gap_factor: float = 0.65,
) -> List[RowCluster]:
    """按中心 Y 聚类成行。

    财报正文字高≈行距时，gap_factor≥1 会把相邻两行并成一行（如 30/31），
    默认 0.65；并参考最近邻中心距估计行距。

    凝结核铁律：小节标题核（含冒号）不得与序号/金额数据行并簇，
    即使 y 中心接近（如「5 扣除前…」与下行「核心一级资本：扣除项」）。
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
        close = abs(n.cy - centers[-1]) <= gap
        # 小节标题 ↔ 数据行：禁止并簇（有交叉也各自成行，再标跨列）
        conflict = (
            (_is_section_title_nucleus(n) and _cluster_is_data_row(clusters[-1]))
            or (
                _is_data_payload_nucleus(n)
                and _cluster_is_section_title_only(clusters[-1])
            )
        )
        if close and not conflict:
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
