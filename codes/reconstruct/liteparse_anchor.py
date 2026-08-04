# -*- coding: utf-8 -*-
"""把 liteparse 页内 text_items 锚定到单表，供还原主链使用。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def _page_of(table: Dict[str, Any]) -> Optional[int]:
    for key in ("page", "page_num", "page_number"):
        v = table.get(key)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def _table_bbox(table: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """尽量取表区域；没有则 None（退化为整页字）。"""
    for key in ("bbox", "table_bbox", "rect"):
        b = table.get(key)
        if isinstance(b, (list, tuple)) and len(b) >= 4:
            try:
                return float(b[0]), float(b[1]), float(b[2]), float(b[3])
            except (TypeError, ValueError):
                pass
    return None


def _item_xyxy(item: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(item, dict):
        return None
    if all(k in item for k in ("x0", "y0", "x1", "y1")):
        try:
            return float(item["x0"]), float(item["y0"]), float(item["x1"]), float(item["y1"])
        except (TypeError, ValueError):
            return None
    if "bbox" in item and isinstance(item["bbox"], (list, tuple)) and len(item["bbox"]) >= 4:
        b = item["bbox"]
        try:
            return float(b[0]), float(b[1]), float(b[2]), float(b[3])
        except (TypeError, ValueError):
            return None
    # liteparse 原始：x,y,width,height
    if "x" in item and "y" in item:
        try:
            x = float(item.get("x") or 0)
            y = float(item.get("y") or 0)
            w = float(item.get("width") or 0)
            h = float(item.get("height") or 0)
            return x, y, x + w, y + h
        except (TypeError, ValueError):
            return None
    return None


def _overlaps(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
    *,
    pad: float = 2.0,
) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (
        ax1 + pad < bx0 or bx1 + pad < ax0 or ay1 + pad < by0 or by1 + pad < ay0
    )


def get_liteparse_page(
    liteparse_data: Optional[Dict[str, Any]],
    page_num: Any,
) -> Optional[Dict[str, Any]]:
    if not liteparse_data or page_num is None:
        return None
    try:
        want = int(page_num)
    except (TypeError, ValueError):
        return None
    for p in liteparse_data.get("pages") or []:
        if not isinstance(p, dict):
            continue
        pn = p.get("page_number", p.get("page", p.get("page_num")))
        try:
            if int(pn) == want:
                return p
        except (TypeError, ValueError):
            continue
    return None


def attach_liteparse_words(
    table: Dict[str, Any],
    liteparse_data: Optional[Dict[str, Any]] = None,
    *,
    liteparse_page: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写入 table['_source_words']：本表区域（或整页）的 liteparse 字框。

    返回附着摘要 dict。
    """
    page = liteparse_page
    if page is None and liteparse_data is not None:
        page = get_liteparse_page(liteparse_data, _page_of(table))

    summary: Dict[str, Any] = {
        "anchored": False,
        "word_count": 0,
        "page": _page_of(table),
        "used_table_bbox": False,
    }
    if not page:
        table["_source_words"] = list(table.get("_source_words") or [])
        summary["reason"] = "no_liteparse_page"
        table["_liteparse_anchor"] = summary
        return summary

    raw_items = page.get("text_items") or []
    bbox = _table_bbox(table)
    words: List[Dict[str, Any]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        xy = _item_xyxy(it)
        if xy is None:
            continue
        if bbox is not None and not _overlaps(xy, bbox):
            continue
        words.append({
            "text": text,
            "x0": xy[0],
            "y0": xy[1],
            "x1": xy[2],
            "y1": xy[3],
        })

    if bbox is not None:
        summary["used_table_bbox"] = True
    # 无 bbox 时用整页字（仍比没有强）
    if not words and bbox is not None:
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            text = str(it.get("text") or "").strip()
            xy = _item_xyxy(it)
            if not text or xy is None:
                continue
            words.append({
                "text": text,
                "x0": xy[0], "y0": xy[1], "x1": xy[2], "y1": xy[3],
            })
        summary["used_table_bbox"] = False
        summary["fallback"] = "full_page"

    table["_source_words"] = words
    summary["anchored"] = True
    summary["word_count"] = len(words)
    table["_liteparse_anchor"] = summary
    return summary
