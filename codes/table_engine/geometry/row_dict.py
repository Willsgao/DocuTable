# -*- coding: utf-8
"""Y 聚类行 dict 构建。"""

from __future__ import annotations

from typing import List


def build_row_dict(row_items: List[dict]) -> dict:
    sorted_items = sorted(row_items, key=lambda it: it.get("x0", 0))
    return {
        "items": sorted_items,
        "y_min": min(it["y0"] for it in sorted_items),
        "y_max": max(it["y1"] for it in sorted_items),
        "texts": [it["text"] for it in sorted_items],
    }


def compute_dynamic_y_threshold(items: List[dict], fallback: float = 5.0) -> float:
    if not items:
        return fallback
    heights = [max(it["y1"] - it["y0"], 1.0) for it in items]
    avg = sum(heights) / len(heights)
    return max(3.0, min(8.0, avg * 0.85))


def cluster_items_by_y(
    items: List[dict],
    *,
    use_dynamic_threshold: bool = True,
    threshold: float = 5.0,
) -> List[dict]:
    """将 items 按 y_mid 聚类为逻辑行。"""
    if not items:
        return []

    effective = (
        compute_dynamic_y_threshold(items, fallback=threshold)
        if use_dynamic_threshold
        else threshold
    )
    sorted_items = sorted(items, key=lambda it: it["y_mid"])
    rows: List[dict] = []
    current = [sorted_items[0]]
    current_y = sorted_items[0]["y_mid"]

    for it in sorted_items[1:]:
        if abs(it["y_mid"] - current_y) <= effective:
            current.append(it)
        else:
            rows.append(build_row_dict(current))
            current = [it]
            current_y = it["y_mid"]
    if current:
        rows.append(build_row_dict(current))
    return rows
