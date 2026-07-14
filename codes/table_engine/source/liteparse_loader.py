# -*- coding: utf-8
"""liteparse pages.json 加载器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from codes.table_engine.models import LiteparseDocument, PageSource, RegionBox
from codes.table_engine.source.item_normalizer import normalize_page_items
from codes.table_engine.source.pdf_text_supplement import supplement_document_items_from_pdf


def load_liteparse_document(path: Union[str, Path]) -> LiteparseDocument:
    """从 pages.json 加载整份文档。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"pages.json 不存在: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    pages: list[PageSource] = []

    for page_dict in data.get("pages", []):
        page_num = int(page_dict.get("page_number", 0))
        regions: list[RegionBox] = []
        for ri, reg in enumerate(page_dict.get("table_regions", []) or []):
            regions.append(RegionBox(
                x0=float(reg.get("x0", 0)),
                y0=float(reg.get("y0", 0)),
                x1=float(reg.get("x1", 0)),
                y1=float(reg.get("y1", 0)),
                confidence=float(reg.get("confidence", 0)),
                region_index=ri,
            ))

        items = normalize_page_items(
            page_dict.get("text_items", []) or [],
            page_num,
        )
        pages.append(PageSource(
            page_number=page_num,
            page_width=float(page_dict.get("page_width", 0)),
            page_height=float(page_dict.get("page_height", 0)),
            items=items,
            table_regions=regions,
            is_table_page=bool(page_dict.get("is_table_page", False)),
            error=page_dict.get("error"),
        ))

    pdf_path = str(data.get("pdf_path", "") or "")
    if pdf_path:
        supplement_document_items_from_pdf(pdf_path, pages)

    return LiteparseDocument(
        pdf_path=str(data.get("pdf_path", "")),
        total_pages=int(data.get("total_pages", len(pages))),
        pages=pages,
        parse_time_sec=float(data.get("parse_time_sec", 0)),
        error=data.get("error"),
    )


def load_page(path: Union[str, Path], page_num: int) -> PageSource:
    """加载单页（内部仍读整文件，后续可加页级缓存）。"""
    doc = load_liteparse_document(path)
    page = doc.get_page(page_num)
    if page is None:
        raise ValueError(f"页码 {page_num} 不存在于 {path}")
    return page
