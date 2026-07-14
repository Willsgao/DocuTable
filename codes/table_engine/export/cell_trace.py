# -*- coding: utf-8
"""Cell → liteparse SourceItem 溯源。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from codes.table_engine.models import LiteparseDocument, StructuredTable


def trace_cell(
    table: StructuredTable,
    row: int,
    col: int,
    document: LiteparseDocument,
) -> List[Dict[str, Any]]:
    if row < 0 or row >= len(table.rows):
        return []
    row_cells = table.rows[row]
    if col < 0 or col >= len(row_cells) or row_cells[col] is None:
        return []
    cell = row_cells[col]
    page = document.get_page(table.page)
    if page is None:
        return []

    lookup = {it.item_index: it for it in page.items}
    out: List[Dict[str, Any]] = []
    for idx in cell.source_items:
        it = lookup.get(idx)
        if it is None:
            out.append({"item_index": idx, "missing": True})
            continue
        out.append({
            "item_index": it.item_index,
            "text": it.text,
            "page": it.page,
            "x0": it.bbox.x0,
            "y0": it.bbox.y0,
            "x1": it.bbox.x1,
            "y1": it.bbox.y1,
            "y_mid": it.y_mid,
            "font_size": it.font_size,
        })
    return out


def trace_cell_to_dicts(
    table: StructuredTable,
    row: int,
    col: int,
    document: LiteparseDocument,
) -> Optional[dict]:
    hits = trace_cell(table, row, col, document)
    if not hits:
        return None
    cell = table.rows[row][col]
    return {
        "page": table.page,
        "row": row,
        "col": col,
        "cell_text": cell.text if cell else "",
        "source_items": hits,
    }
