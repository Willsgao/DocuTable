# -*- coding: utf-8
"""表尾/表前专检：叙述 → TEXT；表头带校验后并入下一张表。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from codes.table_engine.geometry.numeric import is_year_cell
from codes.table_engine.models import DocumentEntry, StructuredTable, TextBlock
from codes.table_engine.scope.header_scope import (
    has_letter_column_header_row,
    is_annual_report_column_header_row,
    is_annual_report_unit_row,
    is_date_only_header_row,
    is_rmb_unit_lead_row,
)
from codes.table_engine.split.row_classify import (
    find_trailing_non_body_start,
    is_entity_scope_header_row,
    is_inter_table_narrative_row,
    is_prependable_header_band_row,
    row_has_body_value_data,
    row_has_date_in_values,
    row_has_header_text_in_values,
    trailing_block_is_next_table_header,
)
from codes.table_engine.split.table_text_split import (
    _rows_to_text,
    row_y_bounds,
    slice_structured_table,
)
from codes.table_engine.table_access import dense_rows

_HEADER_SCAN_ROWS = 8
_YEAR_IN_TEXT_RE = re.compile(r"(?:19|20)\d{2}年?")


def _row_cells(row: List[str]) -> List[str]:
    return [str(c).strip() for c in row if str(c).strip()]


_HEADER_ONLY_MAX_GAP = 280.0


def _is_trailing_candidate_row(row: List[str]) -> bool:
    return is_inter_table_narrative_row(row) or is_prependable_header_band_row(row)


@dataclass
class _LowerHeaderState:
    has_date: bool = False
    has_unit: bool = False
    has_column_header: bool = False
    has_entity_scope: bool = False
    first_body_row: int = 0


def _column_header_fingerprint(cells: List[str]) -> Optional[frozenset]:
    labels = {
        c.lower()
        for c in cells
        if c and len(c) <= 8 and not is_year_cell(c) and not _YEAR_IN_TEXT_RE.search(c)
    }
    return frozenset(labels) if len(labels) >= 2 else None


def _scan_lower_header_state(rows: List[List[str]]) -> _LowerHeaderState:
    scan = min(_HEADER_SCAN_ROWS, len(rows))
    has_date = False
    has_unit = False
    has_col = False
    has_entity = False
    first_body = len(rows)
    for i in range(scan):
        row = rows[i]
        cells = _row_cells(row)
        if row_has_date_in_values(row) or is_date_only_header_row(cells):
            has_date = True
        if is_rmb_unit_lead_row(cells):
            has_unit = True
        if is_annual_report_unit_row(cells):
            has_unit = True
        if is_annual_report_column_header_row(cells):
            has_col = True
            has_date = True
        if has_letter_column_header_row(cells) or row_has_header_text_in_values(row):
            has_col = True
        if is_entity_scope_header_row(row):
            has_entity = True
        if row_has_body_value_data(row) and first_body == len(rows):
            first_body = i
    if first_body == len(rows):
        first_body = scan
    return _LowerHeaderState(
        has_date=has_date,
        has_unit=has_unit,
        has_column_header=has_col,
        has_entity_scope=has_entity,
        first_body_row=first_body,
    )


def _header_row_redundant_in_lower(row: List[str], state: _LowerHeaderState) -> bool:
    cells = _row_cells(row)
    if row_has_date_in_values(row) or is_date_only_header_row(cells):
        return state.has_date
    if is_rmb_unit_lead_row(cells):
        return state.has_unit
    if is_annual_report_unit_row(cells):
        return state.has_unit
    if has_letter_column_header_row(cells) or row_has_header_text_in_values(row):
        return state.has_column_header
    if is_entity_scope_header_row(row):
        return state.has_entity_scope
    return False


def _partition_trailing_rows(
    trailing: List[List[str]],
) -> Optional[Tuple[List[List[str]], List[List[str]]]]:
    narrative: List[List[str]] = []
    headers: List[List[str]] = []
    for row in trailing:
        if is_inter_table_narrative_row(row):
            narrative.append(row)
        elif is_prependable_header_band_row(row):
            headers.append(row)
        else:
            return None
    return narrative, headers


def _filter_headers_for_lower(
    header_rows: List[List[str]],
    lower_rows: List[List[str]],
    upper: StructuredTable,
    lower: StructuredTable,
) -> List[int]:
    """返回可 prepend 的 header_rows 下标。"""
    if not header_rows:
        return []
    u_cols = upper.grid.col_count or 0
    l_cols = lower.grid.col_count or 0
    if u_cols >= 2 and l_cols >= 2 and abs(u_cols - l_cols) > 1:
        return []

    state = _scan_lower_header_state(lower_rows)
    selected: List[int] = []
    for i, row in enumerate(header_rows):
        if _header_row_redundant_in_lower(row, state):
            continue
        selected.append(i)
        cells = _row_cells(row)
        if row_has_date_in_values(row) or is_date_only_header_row(cells):
            state.has_date = True
        if is_rmb_unit_lead_row(cells):
            state.has_unit = True
        if is_annual_report_unit_row(cells):
            state.has_unit = True
        if has_letter_column_header_row(cells) or row_has_header_text_in_values(row):
            state.has_column_header = True
        if is_entity_scope_header_row(row):
            state.has_entity_scope = True

    if not selected:
        return []

    lower_fp = None
    for row in lower_rows[: state.first_body_row]:
        fp = _column_header_fingerprint(_row_cells(row))
        if fp:
            lower_fp = fp
            break
    if lower_fp:
        for idx in selected:
            row = header_rows[idx]
            if has_letter_column_header_row(_row_cells(row)) or row_has_header_text_in_values(row):
                cand_fp = _column_header_fingerprint(_row_cells(row))
                if cand_fp and len(cand_fp & lower_fp) < 2:
                    return []
    return selected


def _prepend_row_matrices(
    table: StructuredTable,
    header_rows: List[List[str]],
    source_table: StructuredTable,
    source_indices: List[int],
) -> StructuredTable:
    if not header_rows:
        return table
    src_rows = [copy.deepcopy(source_table.rows[i]) for i in source_indices]
    out = copy.copy(table)
    out.rows = src_rows + copy.deepcopy(table.rows)
    for ri, row in enumerate(out.rows):
        for cell in row:
            if cell is not None:
                cell.row = ri
    if out.rows:
        out.y0, out.y1 = row_y_bounds(out, 0, len(out.rows))
    return out


def _text_entry_from_rows(
    table: StructuredTable,
    row_start: int,
    row_end: int,
    page: int,
) -> Optional[DocumentEntry]:
    if row_end <= row_start:
        return None
    text = _rows_to_text(dense_rows(table)[row_start:row_end])
    if not text.strip():
        return None
    y0, y1 = row_y_bounds(table, row_start, row_end)
    block = TextBlock(page=page, y0=y0, y1=y1, text=text)
    return DocumentEntry(kind="text", page=page, y0=y0, y1=y1, text_block=block)


def strip_leading_narrative_from_table(
    entry: DocumentEntry,
) -> Tuple[DocumentEntry, Optional[DocumentEntry]]:
    """表首连续节标题/叙述 → TEXT。"""
    if entry.kind != "table" or entry.table is None:
        return entry, None
    rows = dense_rows(entry.table)
    cut = 0
    while cut < len(rows) and is_inter_table_narrative_row(rows[cut]):
        cut += 1
    if cut == 0:
        return entry, None
    text_entry = _text_entry_from_rows(entry.table, 0, cut, entry.page)
    trimmed = slice_structured_table(entry.table, cut)
    return (
        DocumentEntry(
            kind="table",
            page=entry.page,
            y0=trimmed.y0,
            y1=trimmed.y1,
            table=trimmed,
            entry_id=entry.entry_id,
        ),
        text_entry,
    )


def _reattach_pair(
    upper: DocumentEntry,
    lower: DocumentEntry,
) -> Tuple[DocumentEntry, DocumentEntry, List[DocumentEntry]]:
    text_entries: List[DocumentEntry] = []
    if upper.table is None or lower.table is None or upper.page != lower.page:
        return upper, lower, text_entries

    gap = lower.y0 - upper.y1

    rows = dense_rows(upper.table)
    peel_start = find_trailing_non_body_start(rows)
    if peel_start is None:
        return upper, lower, text_entries

    trailing = rows[peel_start:]
    if not trailing or not all(_is_trailing_candidate_row(r) for r in trailing):
        return upper, lower, text_entries

    partitioned = _partition_trailing_rows(trailing)
    if partitioned is None:
        return upper, lower, text_entries
    narrative_rows, header_rows = partitioned

    max_gap = 120.0
    if header_rows and not narrative_rows:
        max_gap = _HEADER_ONLY_MAX_GAP
    elif trailing_block_is_next_table_header(trailing):
        max_gap = _HEADER_ONLY_MAX_GAP
    if gap > max_gap:
        return upper, lower, text_entries

    narrative_end = peel_start + len(narrative_rows)
    if narrative_rows:
        te = _text_entry_from_rows(upper.table, peel_start, narrative_end, upper.page)
        if te is not None:
            text_entries.append(te)

    orig_table = upper.table
    lower_rows = dense_rows(lower.table)
    header_start = narrative_end
    selected_idx = _filter_headers_for_lower(
        header_rows, lower_rows, orig_table, lower.table,
    )

    trimmed = slice_structured_table(orig_table, 0, peel_start)
    upper = DocumentEntry(
        kind="table",
        page=upper.page,
        y0=trimmed.y0,
        y1=trimmed.y1,
        table=trimmed,
        entry_id=upper.entry_id,
    )

    if not selected_idx:
        if header_rows:
            te = _text_entry_from_rows(
                orig_table,
                header_start,
                peel_start + len(trailing),
                upper.page,
            )
            if te is not None:
                text_entries.append(te)
        return upper, lower, text_entries

    source_indices = [header_start + i for i in selected_idx]
    to_prepend = [header_rows[i] for i in selected_idx]
    lower.table = _prepend_row_matrices(
        lower.table,
        to_prepend,
        orig_table,
        source_indices,
    )
    lower = DocumentEntry(
        kind="table",
        page=lower.page,
        y0=lower.table.y0,
        y1=lower.table.y1,
        table=lower.table,
        entry_id=lower.entry_id,
    )
    return upper, lower, text_entries


def apply_trailing_header_reattach(entries: List[DocumentEntry]) -> List[DocumentEntry]:
    """表边界清理：表首叙述剥离；表尾表头带校验后并入下一张表。"""
    if not entries:
        return entries

    staged: List[DocumentEntry] = []
    for entry in entries:
        if entry.kind == "table" and entry.table is not None:
            table_entry, lead_text = strip_leading_narrative_from_table(entry)
            if lead_text is not None:
                staged.append(lead_text)
            staged.append(table_entry)
        else:
            staged.append(entry)

    out: List[DocumentEntry] = []
    i = 0
    while i < len(staged):
        entry = staged[i]
        if (
            entry.kind == "table"
            and entry.table is not None
            and entry.page is not None
        ):
            lower_j = i + 1
            while lower_j < len(staged) and staged[lower_j].kind == "text":
                lower_j += 1
            if (
                lower_j < len(staged)
                and staged[lower_j].kind == "table"
                and staged[lower_j].table is not None
                and staged[lower_j].page == entry.page
            ):
                upper, lower, text_entries = _reattach_pair(entry, staged[lower_j])
                out.append(upper)
                out.extend(text_entries)
                staged[lower_j] = lower
                i += 1
                continue
        out.append(entry)
        i += 1
    return out
