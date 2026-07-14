# -*- coding: utf-8
"""SourceItem 与行聚类中间 dict 互转。"""

from __future__ import annotations

from typing import List

from codes.table_engine.models import BBox, SourceItem


def source_items_to_dicts(items: List[SourceItem]) -> List[dict]:
    out: List[dict] = []
    for it in items:
        out.append({
            "text": it.text,
            "x0": it.bbox.x0,
            "x1": it.bbox.x1,
            "y0": it.bbox.y0,
            "y1": it.bbox.y1,
            "y_mid": it.y_mid,
            "item_index": it.item_index,
            "font_size": it.font_size,
            "font_name": it.font_name,
        })
    return out


def dict_to_source_item(page: int, d: dict) -> SourceItem:
    return SourceItem(
        text=str(d.get("text", "")),
        bbox=BBox(
            float(d.get("x0", 0)),
            float(d.get("y0", 0)),
            float(d.get("x1", 0)),
            float(d.get("y1", 0)),
        ),
        page=page,
        item_index=str(d.get("item_index", "")),
        y_mid=float(d.get("y_mid", 0)),
    )
