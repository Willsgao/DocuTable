# -*- coding: utf-8
"""误拆碎片重组：table + 节标题 text + 续表 → 单表。"""

from __future__ import annotations

import copy
import re
from typing import List, Optional

from codes.table_engine.models import Cell, DocumentEntry, StructuredTable, TextBlock, BBox
from codes.table_engine.split.row_classify import row_has_body_value_data, row_values_all_empty
from codes.table_engine.split.table_text_split import row_y_bounds, slice_structured_table
from codes.table_engine.split.trailing_header_reattach import _scan_lower_header_state
from codes.table_engine.table_access import dense_rows

_FOOTNOTE_MARKER_RE = re.compile(r"^[（(]?\d+[)）\.．、]")
_NOTE_PREFIX_RE = re.compile(r"^注[：:]")


def _row_cells(row: List[str]) -> List[str]:
    return [str(c).strip() for c in row if str(c).strip()]


def _text_is_section_gap(block: TextBlock) -> bool:
    """间隙文本为表内节标题折行，非表后叙述段落。"""
    lines = [ln.strip() for ln in block.text.split("\n") if ln.strip()]
    if not lines:
        return False
    for line in lines:
        if _NOTE_PREFIX_RE.match(line) or _FOOTNOTE_MARKER_RE.match(line):
            return False
        if line.rstrip().endswith(("。", "；")):
            return False
        if re.search(r"\d+\.\d+", line) and len(line) > 20:
            return False
    return True


def _text_is_merge_spacer(block: TextBlock) -> bool:
    """间隙为整行空白，或表内节标题折行（非表后叙述）。"""
    if not str(block.text or "").strip():
        return True
    return _text_is_section_gap(block)


def _table_starts_without_header_band(rows: List[List[str]], *, scan: int = 8) -> bool:
    """续表片段：首几行无日期/单位表头带，直接是标签或数据。"""
    if not rows:
        return False
    sample = rows[: min(scan, len(rows))]
    state = _scan_lower_header_state(sample)
    if state.has_date or state.has_unit:
        return False
    if state.has_column_header and state.first_body_row >= 2:
        return False
    if row_has_body_value_data(rows[0]):
        return True
    if row_values_all_empty(rows[0]) and _row_cells(rows[0]):
        return True
    for row in sample[1:4]:
        if row_has_body_value_data(row):
            return True
    return bool(_row_cells(rows[0]))


def _cols_compatible(upper: StructuredTable, lower: StructuredTable) -> bool:
    u = upper.grid.col_count or 0
    l = lower.grid.col_count or 0
    if u < 2 or l < 2:
        return u == l
    return abs(u - l) <= 1


def _label_rows_from_text(
    block: TextBlock,
    table: StructuredTable,
    col_count: int,
) -> List[List[Optional[Cell]]]:
    """间隙节标题 → 标签列单行矩阵。"""
    lines = [ln.strip() for ln in block.text.split("\n") if ln.strip()]
    out: List[List[Optional[Cell]]] = []
    y_step = max(8.0, (block.y1 - block.y0) / max(len(lines), 1))
    y = block.y0
    x0 = table.x0 + 5.0
    x1 = x0 + 120.0
    for line in lines:
        cells: List[Optional[Cell]] = [None] * col_count
        cells[0] = Cell(
            text=line,
            bbox=BBox(x0, y, x1, y + y_step),
            row=len(out),
            col=0,
            source_items=[],
        )
        out.append(cells)
        y += y_step
    return out


def _concat_tables(
    upper: StructuredTable,
    middle: List[List[Optional[Cell]]],
    lower: StructuredTable,
) -> StructuredTable:
    u_rows = copy.deepcopy(upper.rows)
    l_rows = copy.deepcopy(lower.rows)
    base = len(u_rows) + len(middle)
    for ri, row in enumerate(l_rows):
        for cell in row:
            if cell is not None:
                cell.row = base + ri
    merged_rows = u_rows + middle + l_rows
    out = copy.copy(upper)
    out.rows = merged_rows
    out.y0 = upper.y0
    out.y1 = lower.y1
    out.x0 = min(upper.x0, lower.x0)
    out.x1 = max(upper.x1, lower.x1)
    return out


def _try_rejoin_triplet(
    upper: DocumentEntry,
    mid: DocumentEntry,
    lower: DocumentEntry,
) -> Optional[DocumentEntry]:
    if (
        upper.table is None
        or lower.table is None
        or mid.text_block is None
        or upper.page != lower.page != mid.page
    ):
        return None
    if not _text_is_merge_spacer(mid.text_block):
        return None
    if not _cols_compatible(upper.table, lower.table):
        return None

    lower_rows = dense_rows(lower.table)
    if not _table_starts_without_header_band(lower_rows):
        return None

    gap = lower.y0 - upper.y1
    if gap > 130:
        return None

    ncol = upper.table.grid.col_count or lower.table.grid.col_count or 5
    middle_rows = _label_rows_from_text(mid.text_block, upper.table, ncol)
    merged = _concat_tables(upper.table, middle_rows, lower.table)
    merged.y0, merged.y1 = upper.y0, lower.y1
    return DocumentEntry(
        kind="table",
        page=upper.page,
        y0=merged.y0,
        y1=merged.y1,
        table=merged,
        entry_id=upper.entry_id,
    )


def apply_fragment_rejoin(entries: List[DocumentEntry]) -> List[DocumentEntry]:
    """合并误拆的 table–节标题 text–续表 三连块。"""
    if len(entries) < 3:
        return entries

    out: List[DocumentEntry] = []
    i = 0
    while i < len(entries):
        if (
            i + 2 < len(entries)
            and entries[i].kind == "table"
            and entries[i + 1].kind == "text"
            and entries[i + 2].kind == "table"
        ):
            joined = _try_rejoin_triplet(entries[i], entries[i + 1], entries[i + 2])
            if joined is not None:
                out.append(joined)
                i += 3
                continue
        out.append(entries[i])
        i += 1
    return out
