# -*- coding: utf-8
"""StructuredTable / Document → legacy dict（唯一 data[][] 出口）。"""

from __future__ import annotations

from typing import Any, Dict, List

from codes.table_engine.models import Document, DocumentEntry, StructuredTable, TextBlock


def _grid_col_bounds(table: StructuredTable) -> list[float]:
    """ColumnGrid → 列边界列表（长度 = col_count + 1）。"""
    ranges = table.grid.ranges
    if not ranges:
        return []
    bounds = [float(ranges[0].x0)]
    for col in ranges:
        bounds.append(float(col.x1))
    return bounds


def _cell_row_bounds(table: StructuredTable) -> list[tuple[float, float]]:
    """从 cell bbox 推导行边界；缺 cell 时等比分摊表高。"""
    out: list[tuple[float, float]] = []
    for row in table.rows:
        y0s: list[float] = []
        y1s: list[float] = []
        for cell in row:
            if cell is None or not str(cell.text).strip():
                continue
            y0s.append(float(cell.bbox.y0))
            y1s.append(float(cell.bbox.y1))
        if y0s:
            out.append((min(y0s), max(y1s)))
    if len(out) == len(table.rows) and out:
        return out
    n = max(len(table.rows), 1)
    span = max(float(table.y1 - table.y0), 1.0)
    step = span / n
    y0 = float(table.y0)
    return [(y0 + i * step, y0 + (i + 1) * step) for i in range(n)]


def to_legacy_table(table: StructuredTable, *, table_id: int = 0) -> dict:
    data = table.iter_rows_dense()
    col_bounds = _grid_col_bounds(table)
    row_bounds = _cell_row_bounds(table)
    return {
        "type": "table",
        "page": table.page,
        "y0": float(table.y0),
        "y1": float(table.y1),
        "x0": float(table.x0),
        "x1": float(table.x1),
        "data": data,
        "rows": len(data),
        "cols": table.grid.col_count,
        "layout_id": table.layout_id,
        "caption": table.caption,
        "description_text": table.description_text,
        "notes": table.notes,
        "segment_source": table.metadata.get("segment_source", "table_engine"),
        "confidence": float(table.metadata.get("layout_confidence", 0.85)),
        "extractor": "table_engine",
        "parse_status": "success" if data else "empty",
        "table_category": "财务数据表",
        "is_real_table": True,
        "is_complete": True,
        "has_header": len(data) >= 2,
        "has_numeric_data": True,
        "quality_decision": "accepted",
        "table_id": table_id,
        "_structured": True,
        "metadata": dict(table.metadata),
        "_col_bounds": col_bounds,
        "_row_bounds": row_bounds,
    }


def to_legacy_text(block: TextBlock, *, entry_id: int = 0) -> dict:
    text = block.text.strip()
    return {
        "type": "text",
        "page": block.page,
        "y0": float(block.y0),
        "y1": float(block.y1),
        "context_text": text,
        "data": text,
        "rows": 0,
        "cols": 0,
        "confidence": 0.75,
        "extractor": "table_engine",
        "segment_source": "table_engine_text",
        "parse_status": "success" if text else "empty",
        "table_category": "文本段落",
        "is_real_table": False,
        "is_complete": False,
        "has_header": False,
        "has_numeric_data": False,
        "quality_decision": "accepted",
        "table_id": entry_id,
        "source_items": list(block.source_items),
    }


def entry_to_legacy(entry: DocumentEntry) -> dict:
    if entry.kind == "table" and entry.table is not None:
        legacy = to_legacy_table(entry.table, table_id=entry.entry_id)
        legacy["y0"] = float(entry.y0)
        legacy["y1"] = float(entry.y1)
        suffix = entry.table.metadata.get("split_suffix")
        if suffix:
            legacy["_split_suffix"] = suffix
        return legacy
    if entry.text_block is not None:
        legacy = to_legacy_text(entry.text_block, entry_id=entry.entry_id)
        legacy["y0"] = float(entry.y0)
        legacy["y1"] = float(entry.y1)
        return legacy
    return {
        "type": "text",
        "page": entry.page,
        "y0": float(entry.y0),
        "y1": float(entry.y1),
        "context_text": "",
        "data": "",
        "parse_status": "empty",
        "table_id": entry.entry_id,
    }


def document_to_legacy_list(document: Document) -> List[dict]:
    return [entry_to_legacy(entry) for entry in document.entries]


def verify_legacy_table_matches_structured(table: StructuredTable, legacy: dict) -> bool:
    if legacy.get("data") != table.iter_rows_dense():
        return False
    if legacy.get("rows") != len(table.rows):
        return False
    if legacy.get("cols") != table.grid.col_count:
        return False
    return True
