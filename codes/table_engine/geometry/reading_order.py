# -*- coding: utf-8 -*-
"""同格文本阅读序：左右并列严格左→右，禁止因 y 抖动改序。"""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Sequence


def item_cy(it: Dict[str, Any]) -> float:
    y0 = float(it.get("y0", 0) or 0)
    y1 = float(it.get("y1", y0) or y0)
    return (y0 + y1) / 2.0


def items_horizontally_stacked(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """两字框是否上下折行叠在同一列带（x 大量重叠）。左右并列返回 False。"""
    ax0 = float(a.get("x0", 0) or 0)
    ax1 = float(a.get("x1", ax0) or ax0)
    bx0 = float(b.get("x0", 0) or 0)
    bx1 = float(b.get("x1", bx0) or bx0)
    overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    min_w = min(max(ax1 - ax0, 1.0), max(bx1 - bx0, 1.0))
    return overlap >= 0.5 * min_w


def sort_items_reading_order(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """阅读序：文本前后顺序绝对不可颠倒。

    - 左右并列（如「注释」「人民币」）：严格按 x0 左→右；y 抖动不得改序。
    - 上下折行（x 重叠）：按行带自上而下，行内再左→右。
    """
    items_list = list(items)
    if len(items_list) <= 1:
        return items_list

    stacked = False
    for i, a in enumerate(items_list):
        for b in items_list[i + 1 :]:
            if items_horizontally_stacked(a, b) and abs(item_cy(a) - item_cy(b)) > 3.0:
                stacked = True
                break
        if stacked:
            break

    if not stacked:
        return sorted(items_list, key=lambda it: float(it.get("x0", 0) or 0))

    heights = [
        abs(float(it.get("y1", 0) or 0) - float(it.get("y0", 0) or 0))
        for it in items_list
    ]
    heights = [h for h in heights if h > 0]
    h_med = median(heights) if heights else 10.0
    line_tol = max(3.0, float(h_med) * 0.55)
    by_cy = sorted(
        items_list,
        key=lambda it: (item_cy(it), float(it.get("x0", 0) or 0)),
    )
    lines: List[List[Dict[str, Any]]] = []
    for it in by_cy:
        if not lines or abs(item_cy(it) - item_cy(lines[-1][0])) > line_tol:
            lines.append([it])
        else:
            lines[-1].append(it)
    out: List[Dict[str, Any]] = []
    for line in lines:
        out.extend(sorted(line, key=lambda it: float(it.get("x0", 0) or 0)))
    return out


def join_texts_reading_order(items: Sequence[Dict[str, Any]]) -> str:
    """按阅读序拼接文本；同文去重，顺序不得改变。"""
    if not items:
        return ""
    if len(items) == 1:
        return str(items[0].get("text", "")).strip()
    ordered = sort_items_reading_order(items)
    texts: List[str] = []
    seen: set[str] = set()
    for it in ordered:
        t = str(it.get("text", "")).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        texts.append(t)
    if len(texts) == 1:
        return texts[0]
    return " ".join(texts)
