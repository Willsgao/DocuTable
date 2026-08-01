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


def _cell_source_items_matrix(table: StructuredTable) -> list[list[list[str]]]:
    """与 dense data 同形：每格对应 source item_index 列表（空格为 []）。"""
    n_cols = table.grid.col_count
    out: list[list[list[str]]] = []
    for row in table.rows:
        row_ids: list[list[str]] = []
        for ci in range(n_cols):
            cell = row[ci] if ci < len(row) else None
            if cell is None or not str(cell.text).strip():
                row_ids.append([])
            else:
                row_ids.append([str(s) for s in (cell.source_items or []) if str(s)])
        out.append(row_ids)
    return out


def to_legacy_table(table: StructuredTable, *, table_id: int = 0) -> dict:
    data = table.iter_rows_dense()
    col_bounds = _grid_col_bounds(table)
    row_bounds = _cell_row_bounds(table)
    cell_sources = _cell_source_items_matrix(table)
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
        # 溯源：与 data 同形，供 UI/守恒校验；空非空格应尽量非空
        "_cell_source_items": cell_sources,
    }


def to_legacy_text(block: TextBlock, *, entry_id: int = 0) -> dict:
    text = block.text.strip()
    role = getattr(block, "role", None)
    if role == "page_header":
        category = "页眉"
        segment = "table_engine_page_header"
    elif role == "page_footer":
        category = "页脚"
        segment = "table_engine_page_footer"
    else:
        category = "文本段落"
        segment = "table_engine_text"
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
        "segment_source": segment,
        "parse_status": "success" if text else "empty",
        "table_category": category,
        "text_role": role or "",
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
    src = legacy.get("_cell_source_items")
    if not isinstance(src, list) or len(src) != len(table.rows):
        return False
    expected = _cell_source_items_matrix(table)
    return src == expected


def legacy_nonempty_cells_missing_source(legacy: dict) -> list[tuple[int, int, str]]:
    """非空 data 格却无 source_items → 溯源断链（人工改格高危）。"""
    data = legacy.get("data") or []
    src = legacy.get("_cell_source_items") or []
    bad: list[tuple[int, int, str]] = []
    for ri, row in enumerate(data):
        src_row = src[ri] if ri < len(src) else []
        for ci, cell in enumerate(row):
            text = str(cell or "").strip()
            if not text:
                continue
            ids = src_row[ci] if ci < len(src_row) else []
            if not ids:
                bad.append((ri, ci, text[:40]))
    return bad


def audit_legacy_source_coverage(legacy: dict) -> dict:
    """返回溯源覆盖统计，供 UI/验证脚本使用。"""
    data = legacy.get("data") or []
    nonempty = 0
    with_src = 0
    for ri, row in enumerate(data):
        src_row = (legacy.get("_cell_source_items") or [None] * len(data))
        src_row = src_row[ri] if ri < len(src_row) else []
        for ci, cell in enumerate(row):
            if not str(cell or "").strip():
                continue
            nonempty += 1
            ids = src_row[ci] if ci < len(src_row) else []
            if ids:
                with_src += 1
    missing = legacy_nonempty_cells_missing_source(legacy)
    return {
        "nonempty_cells": nonempty,
        "with_source": with_src,
        "missing_source": len(missing),
        "coverage": (with_src / nonempty) if nonempty else 1.0,
        "missing_samples": missing[:12],
    }
