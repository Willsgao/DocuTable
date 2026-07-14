# -*- coding: utf-8
"""删除 StructuredTable 中整行/整列为空的网格线。"""

from __future__ import annotations

import copy
import re
from typing import Dict, List, Optional, Set, Tuple

from codes.table_engine.conservation.item_conservation import entries_covered_item_ids
from codes.table_engine.geometry.column_anchors import is_pd_range_cell_text
from codes.table_engine.geometry.numeric import is_numeric_data_cell, is_report_date_cell, is_year_cell
from codes.table_engine.models import Cell, ColumnGrid, ColumnRange, DocumentEntry, PageSource, SourceItem, StructuredTable
from codes.table_engine.scope.header_scope import (
    is_annual_report_column_header_row,
    is_annual_report_unit_row,
    is_rmb_unit_lead_row,
)
from codes.table_engine.split.row_classify import DASH_VALUES, is_entity_scope_header_row

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_YEAR_HEADER_RE = re.compile(r"^\d{4}\s*年")


def _cell_is_blank(cell: Optional[Cell]) -> bool:
    if cell is None:
        return True
    text = str(cell.text or "").strip()
    if not text:
        return True
    if text in DASH_VALUES:
        return False
    return False


def _pad_row(row: List[Optional[Cell]], width: int) -> List[Optional[Cell]]:
    if len(row) >= width:
        return list(row[:width])
    return list(row) + [None] * (width - len(row))


def _table_bounds(table: StructuredTable) -> Tuple[float, float, float, float]:
    x0 = y0 = None
    x1 = y1 = None
    for row in table.rows:
        for cell in row:
            if cell is None:
                continue
            if x0 is None:
                x0, y0, x1, y1 = cell.bbox.x0, cell.bbox.y0, cell.bbox.x1, cell.bbox.y1
            else:
                x0 = min(x0, cell.bbox.x0)
                y0 = min(y0, cell.bbox.y0)
                x1 = max(x1, cell.bbox.x1)
                y1 = max(y1, cell.bbox.y1)
    if x0 is None:
        return table.x0, table.y0, table.x1, table.y1
    return x0, y0, x1, y1


def _is_dsib_layout(table: StructuredTable) -> bool:
    if table.layout_id == "pillar_dsib":
        return True
    ranges = table.grid.ranges
    return (
        len(ranges) >= 4
        and ranges[0].role == "level1"
        and ranges[2].role == "indicator"
    )


def prune_blank_rows_columns(
    table: StructuredTable,
    *,
    item_lookup: Optional[Dict[str, SourceItem]] = None,
    text_covered: Optional[Set[str]] = None,
) -> StructuredTable:
    """删除整行/整列为空的网格线；有 scope item 支撑的列不剪。"""
    if not table.rows:
        return table

    ncol = table.grid.col_count or max((len(r) for r in table.rows), default=0)
    if ncol <= 0:
        return table

    nrow = len(table.rows)
    padded = [_pad_row(row, ncol) for row in table.rows]

    blank_rows = {
        i
        for i, row in enumerate(padded)
        if all(_cell_is_blank(cell) for cell in row)
    }
    blank_cols: set[int] = set()
    if item_lookup is not None:
        from codes.table_engine.conservation.item_conservation import (
            column_has_scope_item_support,
        )
    for j in range(ncol):
        if not all(_cell_is_blank(padded[i][j]) for i in range(nrow)):
            continue
        if item_lookup is not None and column_has_scope_item_support(
            table, j, item_lookup, text_covered=text_covered,
        ):
            continue
        blank_cols.add(j)

    if not blank_rows and not blank_cols:
        return table

    keep_rows = [i for i in range(nrow) if i not in blank_rows]
    keep_cols = [j for j in range(ncol) if j not in blank_cols]
    if not keep_rows or not keep_cols:
        return table

    new_rows: List[List[Optional[Cell]]] = []
    for new_ri, old_ri in enumerate(keep_rows):
        new_row: List[Optional[Cell]] = []
        for new_ci, old_ci in enumerate(keep_cols):
            cell = padded[old_ri][old_ci]
            if cell is None:
                new_row.append(None)
                continue
            cloned = copy.deepcopy(cell)
            cloned.row = new_ri
            cloned.col = new_ci
            new_row.append(cloned)
        new_rows.append(new_row)

    old_ranges = list(table.grid.ranges)
    if len(old_ranges) < ncol:
        old_ranges.extend(
            ColumnRange(x0=table.x0, x1=table.x1, col_index=len(old_ranges) + k)
            for k in range(ncol - len(old_ranges))
        )
    new_ranges = [
        ColumnRange(
            x0=old_ranges[j].x0,
            x1=old_ranges[j].x1,
            col_index=ni,
            role=old_ranges[j].role,
        )
        for ni, j in enumerate(keep_cols)
        if j < len(old_ranges)
    ]

    out = copy.copy(table)
    out.rows = new_rows
    out.grid = ColumnGrid(
        ranges=new_ranges,
        layout_id=table.grid.layout_id,
        confidence=table.grid.confidence,
    )
    out.x0, out.y0, out.x1, out.y1 = _table_bounds(out)
    return out


def _is_label_like_text(text: str) -> bool:
    t = str(text or "").strip()
    return bool(t) and bool(_CJK_RE.search(t)) and not is_numeric_data_cell(t)


def _cells_x_overlap(a: Cell, b: Cell, *, margin: float = 18.0) -> bool:
    return a.bbox.x0 <= b.bbox.x1 + margin and b.bbox.x0 <= a.bbox.x1 + margin


def _row_has_value_cells(row: List[Optional[Cell]], ncol: int) -> bool:
    for j in range(min(len(row), ncol)):
        cell = row[j]
        if cell is None:
            continue
        t = str(cell.text or "").strip()
        if t and is_numeric_data_cell(t):
            return True
    return False


def _row_has_serial_in_col(row: List[Optional[Cell]], col: int) -> bool:
    if col >= len(row) or row[col] is None:
        return False
    t = str(row[col].text or "").strip()
    return bool(re.match(r"^\d+[a-z]?$", t, re.I))


def _is_cr6_category_pd_layout(table: StructuredTable) -> bool:
    ranges = table.grid.ranges
    if len(ranges) < 2:
        return False
    return ranges[0].role == "category" and ranges[1].role == "pd_range"


def realign_leading_label_column(table: StructuredTable) -> StructuredTable:
    """单位行在 col0、数据标签在 col1 且 x 重叠时，合并到同一标签列。"""
    if not table.rows:
        return table

    if _is_cr6_category_pd_layout(table):
        return table

    if _is_dsib_layout(table):
        return table

    ncol = table.grid.col_count or max((len(r) for r in table.rows), default=0)
    if ncol < 2:
        return table

    unit_row = -1
    unit_col = -1
    for ri, row in enumerate(table.rows[:6]):
        texts = [
            (row[j].text if j < len(row) and row[j] else "")
            for j in range(ncol)
        ]
        if is_rmb_unit_lead_row(texts):
            unit_row = ri
            for j, cell in enumerate(row):
                if cell and str(cell.text).strip():
                    unit_col = j
                    break
            break

    if unit_row < 0 or unit_col < 0:
        return table

    # 仅处理单位说明落在前列（col0/col1）的情形；居中单位行不应牵动数据区表头（如本行）。
    if unit_col > 1:
        return table

    label_col = unit_col + 1
    if label_col >= ncol:
        return table

    unit_cell = table.rows[unit_row][unit_col]
    if unit_cell is None:
        return table

    shift_rows = 0
    for ri in range(unit_row + 1, len(table.rows)):
        row = table.rows[ri]
        if label_col >= len(row):
            continue
        row_texts = [
            (row[j].text if j < len(row) and row[j] else "")
            for j in range(ncol)
        ]
        if is_entity_scope_header_row(row_texts):
            continue
        if ri > unit_row:
            if not _row_has_serial_in_col(row, unit_col) and not _row_has_value_cells(row, ncol):
                continue
        label_cell = row[label_col]
        unit_empty = unit_col >= len(row) or row[unit_col] is None or not str(row[unit_col].text).strip()
        if (
            label_cell
            and str(label_cell.text).strip()
            and unit_empty
            and _is_label_like_text(label_cell.text)
            and not is_pd_range_cell_text(str(label_cell.text).strip())
            and _cells_x_overlap(unit_cell, label_cell)
        ):
            shift_rows += 1

    if shift_rows < 1:
        return table

    new_rows = copy.deepcopy(table.rows)
    for ri in range(unit_row, len(new_rows)):
        row = new_rows[ri]
        while len(row) < ncol:
            row.append(None)
        row_texts = [
            (row[j].text if j < len(row) and row[j] else "")
            for j in range(ncol)
        ]
        if is_entity_scope_header_row(row_texts):
            continue
        if ri > unit_row:
            if not _row_has_serial_in_col(row, unit_col) and not _row_has_value_cells(row, ncol):
                continue
        label_cell = row[label_col] if label_col < len(row) else None
        unit_empty = unit_col >= len(row) or row[unit_col] is None or not str(row[unit_col].text).strip()
        if not label_cell or not str(label_cell.text).strip() or not unit_empty:
            continue
        if ri > unit_row and not _is_label_like_text(label_cell.text):
            continue
        if is_pd_range_cell_text(str(label_cell.text).strip()):
            continue
        if ri > unit_row and not _cells_x_overlap(unit_cell, label_cell):
            continue
        moved = copy.deepcopy(label_cell)
        moved.col = unit_col
        row[unit_col] = moved
        row[label_col] = None

    out = copy.copy(table)
    out.rows = new_rows
    return out


def _cell_text(cell: Optional[Cell]) -> str:
    return str(cell.text or "").strip() if cell else ""


def _is_value_column_header_text(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if is_year_cell(t) or _YEAR_HEADER_RE.match(t):
        return True
    if is_report_date_cell(t):
        return True
    return False


def _find_annual_header_row_index(
    padded: List[List[Optional[Cell]]],
    ncol: int,
) -> int:
    for ri in range(min(8, len(padded))):
        texts = [
            _cell_text(padded[ri][j] if j < len(padded[ri]) else None)
            for j in range(ncol)
        ]
        if is_annual_report_column_header_row(texts):
            return ri
    return -1


def _row_is_unit_or_meta(
    row: List[Optional[Cell]],
    ncol: int,
) -> bool:
    texts = [
        _cell_text(row[j] if j < len(row) else None)
        for j in range(ncol)
    ]
    return is_annual_report_unit_row(texts) or is_rmb_unit_lead_row(texts)


def realign_value_header_column_shift(table: StructuredTable) -> StructuredTable:
    """居中表头与右对齐数据分列不一致：表头列下方全空、右邻列有数据无表头 → 表头右移。"""
    if not table.rows:
        return table
    if _is_dsib_layout(table) or _is_cr6_category_pd_layout(table):
        return table

    ncol = table.grid.col_count or max((len(r) for r in table.rows), default=0)
    if ncol < 4:
        return table

    padded = [_pad_row(row, ncol) for row in table.rows]
    header_ri = _find_annual_header_row_index(padded, ncol)
    if header_ri < 0:
        return table

    body_rows = [
        padded[ri]
        for ri in range(header_ri + 1, len(padded))
        if not _row_is_unit_or_meta(padded[ri], ncol)
    ]
    if len(body_rows) < 2:
        return table

    header_row = padded[header_ri]
    shift_cols: List[int] = []
    for j in range(1, ncol - 1):
        header_text = _cell_text(header_row[j] if j < len(header_row) else None)
        if not _is_value_column_header_text(header_text):
            continue
        next_header = _cell_text(
            header_row[j + 1] if j + 1 < len(header_row) else None
        )
        if _is_value_column_header_text(next_header):
            continue

        empty_below = 0
        data_right = 0
        for row in body_rows:
            left = _cell_text(row[j] if j < len(row) else None)
            right = _cell_text(row[j + 1] if j + 1 < len(row) else None)
            if not left:
                empty_below += 1
            if is_numeric_data_cell(right) or right in DASH_VALUES:
                data_right += 1

        n = len(body_rows)
        if empty_below / n >= 0.75 and data_right / n >= 0.5:
            shift_cols.append(j)

    if not shift_cols:
        return table

    new_rows = copy.deepcopy(table.rows)
    for ri in range(len(new_rows)):
        while len(new_rows[ri]) < ncol:
            new_rows[ri].append(None)

    for j in shift_cols:
        target = j + 1
        if target >= ncol:
            continue
        src = new_rows[header_ri][j]
        if src is None or not _cell_text(src):
            continue
        if new_rows[header_ri][target] is not None and _cell_text(new_rows[header_ri][target]):
            continue
        moved = copy.deepcopy(src)
        moved.col = target
        new_rows[header_ri][target] = moved
        new_rows[header_ri][j] = None

    out = copy.copy(table)
    out.rows = new_rows
    return out


def apply_grid_prune(
    entries: List[DocumentEntry],
    page: Optional[PageSource] = None,
) -> List[DocumentEntry]:
    item_lookup: Optional[Dict[str, SourceItem]] = None
    text_covered: Set[str] = set()
    if page is not None:
        item_lookup = {it.item_index: it for it in page.items}
    text_covered = entries_covered_item_ids(
        [e for e in entries if e.kind == "text"],
    )
    for entry in entries:
        if entry.kind == "table" and entry.table is not None:
            entry.table = prune_blank_rows_columns(
                prune_blank_rows_columns(
                    realign_value_header_column_shift(
                        realign_leading_label_column(entry.table),
                    ),
                    item_lookup=item_lookup,
                    text_covered=text_covered,
                ),
                item_lookup=item_lookup,
                text_covered=text_covered,
            )
    return entries
