# -*- coding: utf-8 -*-
"""UI / legacy 表格 → StructuredTable 溯源桥接。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from codes.table_engine.export.cell_trace import trace_cell_to_dicts
from codes.table_engine.integration.processor_bridge import resolve_pages_json_path
from codes.table_engine.models import Document, StructuredTable
from codes.table_engine.pipeline import DocumentBuilder
from codes.table_engine.source.liteparse_loader import load_liteparse_document


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.is_file() else 0.0


@lru_cache(maxsize=8)
def _cached_document(pages_json: str, mtime: float) -> Document:
    return DocumentBuilder().build(pages_json)


@lru_cache(maxsize=8)
def _cached_liteparse(pages_json: str, mtime: float):
    return load_liteparse_document(pages_json)


def get_cached_document(pdf_path: str) -> Optional[Document]:
    pages_json = resolve_pages_json_path(pdf_path)
    if pages_json is None:
        return None
    return _cached_document(str(pages_json), _mtime(pages_json))


def find_structured_table(
    document: Document,
    legacy_table: dict,
) -> Optional[StructuredTable]:
    table_id = legacy_table.get("table_id")
    page = legacy_table.get("page")
    y0 = float(legacy_table.get("y0", 0) or 0)

    if table_id is not None:
        for entry in document.entries:
            if (
                entry.kind == "table"
                and entry.entry_id == table_id
                and entry.table is not None
            ):
                return entry.table

    candidates = [
        e.table
        for e in document.entries
        if e.kind == "table" and e.page == page and e.table is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda t: abs(float(t.y0) - y0))


def trace_legacy_cell_bbox(
    legacy_table: dict,
    row: int,
    col: int,
    pdf_path: str,
) -> Optional[dict]:
    """legacy 表格 dict + 行列 → 源 item bbox（供 UI 展示）。"""
    if not legacy_table.get("_structured"):
        return None
    pages_json = resolve_pages_json_path(pdf_path)
    if pages_json is None:
        return None

    key = str(pages_json)
    mt = _mtime(pages_json)
    document = _cached_document(key, mt)
    table = find_structured_table(document, legacy_table)
    if table is None:
        return None

    lite = _cached_liteparse(key, mt)
    detail = trace_cell_to_dicts(table, row, col, lite)
    if not detail:
        return None

    sources = detail.get("source_items") or []
    bbox_parts = []
    for src in sources:
        if src.get("missing"):
            bbox_parts.append(f"idx={src.get('item_index')}?")
            continue
        bbox_parts.append(
            f"({src['x0']:.1f},{src['y0']:.1f})-({src['x1']:.1f},{src['y1']:.1f})"
        )

    return {
        "page": detail.get("page"),
        "row": row,
        "col": col,
        "cell_text": detail.get("cell_text", ""),
        "source_items": sources,
        "bbox_summary": " | ".join(bbox_parts) if bbox_parts else "",
    }


def format_cell_trace_line(info: dict) -> str:
    page = info.get("page", "?")
    row = info.get("row", "?")
    col = info.get("col", "?")
    bbox = info.get("bbox_summary") or "（无坐标）"
    text = str(info.get("cell_text", ""))[:40]
    return f"P{page} [{row},{col}] {text!r} | bbox: {bbox}"
