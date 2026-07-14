# -*- coding: utf-8
"""用 PDF 文本层补全 liteparse OCR 漏检的表格碎片（仅补缺，不覆盖已有 item）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Sequence, TYPE_CHECKING

from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.models import BBox, RegionBox, SourceItem
from codes.table_engine.source.item_normalizer import compute_item_index

if TYPE_CHECKING:
    import fitz  # PyMuPDF

_Y_TOL = 5.0
_X_TOL = 12.0
_MAX_SUPPLEMENT_LEN = 24
_NUMERIC_RE = re.compile(r"^[\d(),.\-+％%]+$")


def _open_pdf(pdf_path: str) -> Optional["fitz.Document"]:
    path = Path(pdf_path)
    if not path.is_file():
        return None
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        return fitz.open(str(path))
    except Exception:
        return None


def _in_regions(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    regions: Sequence[RegionBox],
    *,
    margin: float = 4.0,
) -> bool:
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    for reg in regions:
        if (
            reg.x0 - margin <= cx <= reg.x1 + margin
            and reg.y0 - margin <= cy <= reg.y1 + margin
        ):
            return True
    return False


def _word_covered(text: str, x0: float, y0: float, x1: float, y1: float, items: Sequence[SourceItem]) -> bool:
    """已有 item 覆盖该 PDF 词（同带同文或 bbox 重叠）。"""
    wmid = (y0 + y1) / 2.0
    wt = str(text or "").strip()
    if not wt:
        return True
    for it in items:
        if abs(it.y_mid - wmid) > _Y_TOL:
            continue
        ib = it.bbox
        if ib.x1 < x0 - 3 or ib.x0 > x1 + 3:
            continue
        it_text = str(it.text or "").strip()
        if it_text == wt:
            return True
        if wt in it_text or it_text in wt:
            return True
        if is_numeric_data_cell(wt) and is_numeric_data_cell(it_text):
            if abs(ib.x0 - x0) <= _X_TOL:
                return True
    return False


def _should_supplement_word(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t) > _MAX_SUPPLEMENT_LEN:
        return False
    if is_numeric_data_cell(t):
        return True
    if _NUMERIC_RE.match(t.replace(" ", "")):
        return True
    return False


def supplement_page_items_from_pdf(
    doc: "fitz.Document",
    page_num: int,
    items: List[SourceItem],
    regions: Sequence[RegionBox],
) -> List[SourceItem]:
    """在 table region 内用 PDF 文本层补缺数值碎片。"""
    if not regions or page_num < 1:
        return items

    page = doc[page_num - 1]
    out = list(items)
    seen_keys: set[tuple] = {
        (round(it.bbox.x0, 1), round(it.y_mid, 1), str(it.text).strip())
        for it in items
    }

    for word in page.get_text("words") or []:
        if len(word) < 5:
            continue
        x0, y0, x1, y1, text = float(word[0]), float(word[1]), float(word[2]), float(word[3]), str(word[4])
        wt = text.strip()
        if not _should_supplement_word(wt):
            continue
        if not _in_regions(x0, y0, x1, y1, regions):
            continue
        if _word_covered(wt, x0, y0, x1, y1, out):
            continue
        key = (round(x0, 1), round((y0 + y1) / 2.0, 1), wt)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(
            SourceItem(
                text=wt,
                bbox=BBox(x0, y0, x1, y1),
                page=page_num,
                item_index=compute_item_index(page_num, wt, x0, y0),
                y_mid=(y0 + y1) / 2.0,
                merged_from=[],
            )
        )

    out.sort(key=lambda it: (it.y_mid, it.bbox.x0))
    return out


def supplement_document_items_from_pdf(
    pdf_path: str,
    pages: List,
) -> None:
    """就地补全 PageSource.items（仅 is_table_page）。"""
    doc = _open_pdf(pdf_path)
    if doc is None:
        return
    try:
        for page in pages:
            if not page.is_table_page or not page.table_regions:
                continue
            page.items = supplement_page_items_from_pdf(
                doc,
                page.page_number,
                list(page.items),
                page.table_regions,
            )
    finally:
        doc.close()
