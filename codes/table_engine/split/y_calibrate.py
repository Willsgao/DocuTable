# -*- coding: utf-8
"""DocumentEntry Y 坐标校准（source item bbox）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from codes.table_engine.models import DocumentEntry, PageSource, SourceItem


def _items_by_index(page: PageSource) -> Dict[str, SourceItem]:
    return {it.item_index: it for it in page.items}


def _bounds_from_indices(
    page: PageSource,
    indices: List[str],
) -> Optional[tuple[float, float, float, float]]:
    if not indices:
        return None
    lookup = _items_by_index(page)
    items = [lookup[idx] for idx in indices if idx in lookup]
    if not items:
        return None
    x0 = min(it.bbox.x0 for it in items)
    y0 = min(it.bbox.y0 for it in items)
    x1 = max(it.bbox.x1 for it in items)
    y1 = max(it.bbox.y1 for it in items)
    return x0, y0, x1, y1


def calibrate_entry_y(entry: DocumentEntry, page: PageSource) -> DocumentEntry:
    if entry.kind == "table" and entry.table is not None:
        entry.y0 = entry.table.y0
        entry.y1 = entry.table.y1
        return entry

    block = entry.text_block
    if block is None:
        return entry

    bounds = _bounds_from_indices(page, list(block.source_items))
    if bounds:
        _, y0, _, y1 = bounds
        entry.y0 = y0
        entry.y1 = y1
        block.y0 = y0
        block.y1 = y1
        return entry

    if block.text.strip():
        snippet = block.text.strip()[:24]
        hits = [
            it for it in page.items
            if snippet and snippet in it.text
        ]
        if hits:
            y0 = min(it.bbox.y0 for it in hits)
            y1 = max(it.bbox.y1 for it in hits)
            entry.y0 = y0
            entry.y1 = y1
            block.y0 = y0
            block.y1 = y1
    return entry


def apply_y_calibration(
    entries: List[DocumentEntry],
    page: PageSource,
) -> List[DocumentEntry]:
    return [calibrate_entry_y(entry, page) for entry in entries]
