# -*- coding: utf-8
"""StructuredTable 表文几何分裂（Y 边界来自 cell bbox）。"""

from __future__ import annotations

import copy
import re
from typing import List, Optional, Tuple

from codes.table_engine.conservation.item_conservation import refresh_scope_source_items
from codes.table_engine.geometry.numeric import is_month_day_cell, is_numeric_data_cell, is_report_date_cell, is_year_cell
from codes.table_engine.models import Cell, DocumentEntry, StructuredTable, TextBlock
from codes.table_engine.scope.header_scope import (
    has_annual_column_header_band,
    has_letter_column_header_row,
    is_date_only_header_row,
    is_rmb_unit_lead_row,
    row_has_pillar_table_caption,
    row_has_reporting_date,
)
from codes.table_engine.split.content_partition import description_already_in_table
from codes.table_engine.split.row_classify import (
    cell_has_body_value_data,
    find_last_body_value_row,
    is_inter_table_narrative_row,
    is_tail_annotation_row,
    row_has_body_value_data,
    row_is_annual_subsection_caption_row,
    row_is_numbered_subsection_caption_row,
    row_is_note_section_caption_row,
    row_is_table_tail_section_caption_row,
    _label_only_block_is_post_table_narrative,
)
from codes.table_engine.table_access import dense_rows

_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.I)
_SERIAL_LEAD_RE = re.compile(r"^(\d+[a-z]?)(?:\s|$)", re.I)
_TABLE_CAPTION_RE = re.compile(r"表\s*\d+\s*[\(（][A-Za-z0-9]+[\)）]")
_DASH_VALUES = frozenset(("-", "－", "—", "–"))


def _first_serial_token(cell: str) -> str:
    text = str(cell or "").strip()
    if not text:
        return ""
    if is_year_cell(text) or is_report_date_cell(text) or is_month_day_cell(text):
        return ""
    if _ROW_NUMBER_RE.match(text):
        return text
    m = _SERIAL_LEAD_RE.match(text)
    return m.group(1) if m else ""


def _is_serial_data_row(row: List[str]) -> bool:
    cells = _row_cells(row)
    if not cells:
        return False
    first = cells[0]
    serial = _first_serial_token(first)
    if not serial:
        return False
    if any(is_numeric_data_cell(c) or str(c).strip() in _DASH_VALUES for c in cells[1:]):
        return True
    # 合并格「序号 说明」（如 28 调整后表内外资产余额a2）仍为表体行
    if _SERIAL_LEAD_RE.match(first):
        return True
    return len(cells) >= 2


def find_last_serial_data_row(rows: List[List[str]]) -> int:
    """最后一条带序号的数据行（含「1 并表总资产」合并格）。"""
    last = -1
    for i, row in enumerate(rows):
        if _is_serial_data_row(row):
            last = i
    return last


def find_first_serial_block_end(rows: List[List[str]]) -> int:
    """第一组序号数据块末行（遇到第二张表表头/表题即停）。"""
    last = -1
    for i, row in enumerate(rows):
        if _is_serial_data_row(row):
            last = i
            continue
        if last < 0:
            continue
        cells = _row_cells(row)
        if is_rmb_unit_lead_row(cells) and i > last + 1:
            return last
        if row_has_pillar_table_caption(cells):
            return last
    return last



def peel_post_body_tail(table: StructuredTable) -> Tuple[StructuredTable, Optional[TextBlock]]:
    """表体末行之后、连续表尾注释/叙述行 → TextBlock（表内节标题不剥）。"""
    rows = dense_rows(table)
    ncol = table.grid.col_count or max((len(r) for r in rows), default=0)
    last_data = find_last_body_value_row(rows)

    if last_data < 0 or last_data >= len(rows) - 1:
        return table, None

    start = last_data + 1
    if start >= len(rows):
        return table, None

    end = start
    while end < len(rows) and not row_has_body_value_data(rows[end]):
        end += 1
    if end <= start or not _label_only_block_is_post_table_narrative(rows[start:end]):
        return table, None

    tail_lines: List[str] = []
    for row in rows[start:end]:
        line = " ".join(str(c).strip() for c in row if str(c).strip())
        if line:
            tail_lines.append(line)

    if not tail_lines:
        return table, None

    tail_ids: List[str] = []
    for ri in range(start, end):
        for cell in table.rows[ri]:
            if cell is None:
                continue
            for sid in cell.source_items or []:
                if sid and sid not in tail_ids:
                    tail_ids.append(str(sid))

    trimmed = slice_structured_table(table, 0, start)
    tail_y0 = _row_y_bounds(table, start, end)[0]
    _, tail_y1 = _row_y_bounds(table, start, end)
    block = TextBlock(
        page=table.page,
        y0=tail_y0,
        y1=tail_y1,
        text="\n".join(tail_lines),
        source_items=tail_ids,
    )
    return trimmed, block


def _row_cells(row: List[str]) -> List[str]:
    return [str(c).strip() for c in row if str(c).strip()]


def _has_pillar_grid_header(rows: List[List[str]], scan: int = 8) -> bool:
    sample = rows[: min(scan, len(rows))]
    if any(has_letter_column_header_row(_row_cells(r)) for r in sample):
        return True
    return any(is_rmb_unit_lead_row(_row_cells(r)) for r in sample)


def _count_period_header_rows(rows: List[List[str]]) -> int:
    n = 0
    for row in rows:
        cells = _row_cells(row)
        if is_date_only_header_row(cells):
            n += 1
            continue
        if row_has_reporting_date(cells):
            n += 1
            continue
        if sum(1 for c in cells if is_year_cell(c) or is_month_day_cell(c)) >= 2:
            n += 1
    return n


def _count_numbered_body_rows(rows: List[List[str]], start: int = 0) -> int:
    count = 0
    for row in rows[start:]:
        cells = _row_cells(row)
        if not cells:
            continue
        if cells[0] and _ROW_NUMBER_RE.match(cells[0]):
            vals = sum(
                1 for c in cells[1:]
                if is_numeric_data_cell(c)
                or str(c).strip() in ("-", "－", "—", "–")
            )
            if vals >= 1 or any(cells[1:]):
                count += 1
    return count


def is_pillar_disclosure_table_body(rows: List[List[str]]) -> bool:
    if not rows or len(rows) < 5:
        return False
    if not _has_pillar_grid_header(rows):
        return False
    if _count_period_header_rows(rows[:8]) < 1:
        return False
    first_num = next(
        (i for i, r in enumerate(rows) if _row_cells(r) and _ROW_NUMBER_RE.match(_row_cells(r)[0])),
        len(rows),
    )
    num_rows = _count_numbered_body_rows(rows, first_num)
    if num_rows < 3:
        return False
    body_len = max(len(rows) - first_num, 1)
    if num_rows < 5 and num_rows / body_len < 0.2:
        return False
    return True


def find_pillar_table_body_start_row(rows: List[List[str]]) -> int:
    for i, row in enumerate(rows):
        cells = _row_cells(row)
        if not cells:
            continue
        if cells[0] in ("资产", "负债"):
            return i
        if cells[0] == "1" and _ROW_NUMBER_RE.match(cells[0]):
            if sum(1 for c in cells[1:] if is_numeric_data_cell(c)) >= 1:
                return i

    caption_idx = -1
    for i, row in enumerate(rows):
        if row_has_pillar_table_caption(_row_cells(row)):
            caption_idx = i

    start = caption_idx + 1 if caption_idx >= 0 else 0
    for i in range(start, len(rows)):
        cells = _row_cells(row)
        if has_letter_column_header_row(cells):
            return i
        if is_rmb_unit_lead_row(cells):
            return i
        joined = "".join(cells)
        if ("人民币" in joined or "百万元" in joined) and len(cells) >= 2:
            if i + 1 < len(rows):
                nxt = _row_cells(rows[i + 1])
                if _count_period_header_rows([nxt]) >= 1 or any(is_year_cell(c) for c in nxt):
                    return i
    return 0


def _row_y_bounds(table: StructuredTable, row_start: int, row_end: int) -> Tuple[float, float]:
    y0: Optional[float] = None
    y1: Optional[float] = None
    for ri in range(max(0, row_start), min(row_end, len(table.rows))):
        for cell in table.rows[ri]:
            if cell is None:
                continue
            if y0 is None:
                y0, y1 = cell.bbox.y0, cell.bbox.y1
            else:
                y0 = min(y0, cell.bbox.y0)
                y1 = max(y1, cell.bbox.y1)
    if y0 is None:
        return table.y0, table.y1
    return y0, y1


def _row_x_bounds(table: StructuredTable, row_start: int, row_end: int) -> Tuple[float, float]:
    x0: Optional[float] = None
    x1: Optional[float] = None
    for ri in range(max(0, row_start), min(row_end, len(table.rows))):
        for cell in table.rows[ri]:
            if cell is None:
                continue
            if x0 is None:
                x0, x1 = cell.bbox.x0, cell.bbox.x1
            else:
                x0 = min(x0, cell.bbox.x0)
                x1 = max(x1, cell.bbox.x1)
    if x0 is None:
        return table.x0, table.x1
    return x0, x1


def _rows_to_text(rows: List[List[str]]) -> str:
    lines: List[str] = []
    for row in rows:
        line = " ".join(c for c in row if str(c).strip())
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def slice_structured_table(
    table: StructuredTable,
    row_start: int,
    row_end: Optional[int] = None,
) -> StructuredTable:
    end = len(table.rows) if row_end is None else row_end
    new_rows = copy.deepcopy(table.rows[row_start:end])
    out = copy.copy(table)
    out.rows = new_rows
    if new_rows:
        out.y0, out.y1 = _row_y_bounds(table, row_start, end)
        out.x0, out.x1 = _row_x_bounds(table, row_start, end)
    return out


def _text_block_to_entry(block: TextBlock, entry_id: int) -> DocumentEntry:
    return DocumentEntry(
        kind="text",
        page=block.page,
        y0=block.y0,
        y1=block.y1,
        text_block=block,
        entry_id=entry_id,
    )


def _table_to_entry(table: StructuredTable, entry_id: int) -> DocumentEntry:
    return DocumentEntry(
        kind="table",
        page=table.page,
        y0=table.y0,
        y1=table.y1,
        table=table,
        entry_id=entry_id,
    )


def _merge_text_blocks(blocks: List[TextBlock]) -> List[TextBlock]:
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda b: (b.y0, b.y1))
    merged: List[TextBlock] = [ordered[0]]
    for block in ordered[1:]:
        prev = merged[-1]
        # 页眉/页脚不与正文或彼此（不同 role）合并
        if (prev.role or block.role) and prev.role != block.role:
            merged.append(block)
            continue
        if block.y0 <= prev.y1 + 8 and block.page == prev.page:
            prev.text = f"{prev.text}\n{block.text}".strip()
            prev.y1 = max(prev.y1, block.y1)
            prev.source_items = list(prev.source_items) + list(block.source_items)
            if prev.role is None and block.role:
                prev.role = block.role
        else:
            merged.append(block)
    return merged


_INTER_TABLE_TAIL_MARKERS = ("下表列示", "表 15", "表15", "要求等相关信息")
_FOOTNOTE_LEAD_RE = re.compile(r"^[（(]?\d+[)）\.．、]")


def _is_footnote_or_narrative_text(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if _FOOTNOTE_LEAD_RE.match(t):
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    if cn >= 12 and t.rstrip().endswith(("。", "；")):
        return True
    return False


def _peeled_tail_emit_as_text(table: StructuredTable, block: TextBlock) -> bool:
    """表尾脚注/叙述进 TEXT；表间叙述/表头带（无表体数值）进 TEXT。"""
    if table.layout_id == "generic":
        return True
    text = block.text.strip()
    if any(m in text for m in _INTER_TABLE_TAIL_MARKERS):
        return True
    if row_has_pillar_table_caption([text]):
        return True
    if _is_footnote_or_narrative_text(text):
        return True
    if "下表列" in text:
        return True
    peel_rows = [
        [c for c in line.split() if c]
        for line in text.splitlines()
        if line.strip()
    ]
    if has_annual_column_header_band(peel_rows):
        return False
    # 剥下的尾部若无表体数值 → 表间表头/叙述，进 TEXT
    for line in text.splitlines():
        cells = line.split()
        if row_has_body_value_data(cells):
            col_count = table.grid.col_count or 0
            return col_count < 3
    return True


def _consume_peeled_tail(
    table: StructuredTable,
    block: Optional[TextBlock],
) -> Tuple[StructuredTable, Optional[TextBlock]]:
    if block is None:
        return table, None
    if _peeled_tail_emit_as_text(table, block):
        return table, block
    out = copy.copy(table)
    note = block.text.strip()
    out.notes = f"{out.notes}\n{note}".strip() if out.notes else note
    return out, None


def _is_annotation_like_row(row: List[str]) -> bool:
    cells = _row_cells(row)
    if not cells:
        return True
    if re.match(r"^[（(]?\d+[)）\.．、]", cells[0]):
        return True
    joined = "".join(cells)
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    vals = cells[1:] if len(cells) > 1 else []
    has_val = any(cell_has_body_value_data(v) for v in vals if v)
    if cn >= 10 and not has_val:
        return True
    if "下表列示" in joined or row_has_pillar_table_caption(cells):
        return True
    return False


def _find_annual_column_header_row_index(rows: List[List[str]]) -> int:
    """项目 + 多个报告期列 → 年报表头带行号。"""
    for i, row in enumerate(rows):
        cells = _row_cells(row)
        if not cells or cells[0].strip() != "项目":
            continue
        year_cols = sum(
            1 for c in cells[1:]
            if c and ("年" in c or is_year_cell(c) or is_month_day_cell(c))
        )
        if year_cols >= 2:
            return i
    return -1


def _table_has_annual_column_header_band(rows: List[List[str]]) -> bool:
    return _find_annual_column_header_row_index(rows) >= 0


def _table_is_annotation_only(rows: List[List[str]]) -> bool:
    if find_last_serial_data_row(rows) >= 0:
        return False
    if find_last_body_value_row(rows) >= 0:
        return False
    non_empty = [_row_cells(r) for r in rows if _row_cells(r)]
    if not non_empty:
        return True
    return all(_is_annotation_like_row(r) for r in rows if _row_cells(r))


def _trim_trailing_section_caption(
    table: StructuredTable,
) -> Tuple[StructuredTable, List[TextBlock]]:
    peeled: List[TextBlock] = []
    rows = dense_rows(table)
    while rows:
        last = rows[-1]
        if not row_is_table_tail_section_caption_row(last):
            break
        tail_ids: List[str] = []
        ri = len(rows) - 1
        if ri < len(table.rows):
            for cell in table.rows[ri]:
                if cell is None:
                    continue
                for sid in cell.source_items or []:
                    if sid and sid not in tail_ids:
                        tail_ids.append(str(sid))
        line = " ".join(str(c).strip() for c in last if str(c).strip())
        if line:
            y0, y1 = _row_y_bounds(table, ri, ri + 1)
            peeled.insert(
                0,
                TextBlock(
                    page=table.page,
                    y0=y0,
                    y1=y1,
                    text=line,
                    source_items=tail_ids,
                ),
            )
        table = slice_structured_table(table, 0, len(rows) - 1)
        rows = dense_rows(table)
    return table, peeled


def split_structured_table(table: StructuredTable) -> List[DocumentEntry]:
    """单张 StructuredTable → text + table 条目（披露表优先整表，Y 来自 bbox）。"""
    table, tail_block = peel_post_body_tail(table)
    if tail_block is not None:
        refresh_scope_source_items(table)
    table, peeled_tail = _consume_peeled_tail(table, tail_block)
    table, section_peels = _trim_trailing_section_caption(table)

    def _prepend_peels(entries: List[DocumentEntry], eid: int) -> Tuple[List[DocumentEntry], int]:
        out: List[DocumentEntry] = []
        for block in section_peels:
            out.append(_text_block_to_entry(block, eid))
            eid += 1
        if peeled_tail is not None:
            out.append(_text_block_to_entry(peeled_tail, eid))
            eid += 1
        out.extend(entries)
        return out, eid

    rows = dense_rows(table)
    if not rows:
        entries, _ = _prepend_peels([], 0)
        return entries

    if _table_is_annotation_only(rows):
        if (
            table.metadata.get("split_suffix")
            or _table_has_annual_column_header_band(rows)
        ):
            entries = [_table_to_entry(table, 0)]
            entries, _ = _prepend_peels(entries, 0)
            return entries
        text = _rows_to_text(rows)
        if text.strip():
            block = TextBlock(page=table.page, y0=table.y0, y1=table.y1, text=text)
            return [_text_block_to_entry(block, 0)]
        return []

    if len(rows) < 4:
        entries: List[DocumentEntry] = [_table_to_entry(table, 0)]
        entries, _ = _prepend_peels(entries, 0)
        return entries

    body_start = find_pillar_table_body_start_row(rows)
    header_idx = _find_annual_column_header_row_index(rows)
    if header_idx >= 0 and body_start > header_idx:
        body_start = header_idx
    body = rows[body_start:] if body_start > 0 else rows

    if is_pillar_disclosure_table_body(body):
        entries: List[DocumentEntry] = []
        eid = 0
        for block in section_peels:
            entries.append(_text_block_to_entry(block, eid))
            eid += 1
        if peeled_tail is not None:
            entries.append(_text_block_to_entry(peeled_tail, eid))
            eid += 1
        if body_start > 0:
            narr_rows = rows[:body_start]
            if not has_annual_column_header_band(narr_rows):
                text = _rows_to_text(narr_rows)
                if text.strip():
                    y0, y1 = _row_y_bounds(table, 0, body_start)
                    block = TextBlock(
                        page=table.page,
                        y0=y0,
                        y1=y1,
                        text=text,
                    )
                    entries.append(_text_block_to_entry(block, eid))
                    eid += 1
                tbl = slice_structured_table(table, body_start)
            else:
                tbl = table
        else:
            tbl = slice_structured_table(table, body_start) if body_start > 0 else table
        tbl.metadata["segment_source"] = "pillar_disclosure_table"
        entries.append(_table_to_entry(tbl, eid))
        return entries

    if is_pillar_disclosure_table_body(rows):
        entries = []
        eid = 0
        for block in section_peels:
            entries.append(_text_block_to_entry(block, eid))
            eid += 1
        if peeled_tail is not None:
            entries.append(_text_block_to_entry(peeled_tail, eid))
            eid += 1
        out = copy.copy(table)
        out.metadata["segment_source"] = "pillar_disclosure_table"
        entries.append(_table_to_entry(out, eid))
        return entries

    entries = []
    eid = 0
    for block in section_peels:
        entries.append(_text_block_to_entry(block, eid))
        eid += 1
    if peeled_tail is not None:
        entries.append(_text_block_to_entry(peeled_tail, eid))
        eid += 1
    entries.append(_table_to_entry(table, eid))
    return entries


def build_page_entries(
    *,
    tables: List[StructuredTable],
    gap_texts: List[TextBlock],
) -> List[DocumentEntry]:
    """gap 说明 + 表文分裂 → 单页 DocumentEntry 列表（阅读顺序）。"""
    text_blocks = _merge_text_blocks(list(gap_texts))

    caption_blocks: List[TextBlock] = []
    for table in tables:
        desc = table.description_text.strip()
        if not desc:
            continue
        if description_already_in_table(table, desc):
            continue
        if any(m in desc for m in ("第三支柱信息披露", "信息披露报告")) and "表" not in desc:
            continue
        desc_src = list(table.metadata.get("description_source_items") or [])
        caption_blocks.append(
            TextBlock(
                page=table.page,
                y0=max(0.0, table.y0 - 30.0),
                y1=table.y0,
                text=desc,
                source_items=desc_src,
            )
        )
    text_blocks = _merge_text_blocks(text_blocks + caption_blocks)

    entries: List[DocumentEntry] = []
    eid = 0

    for block in sorted(text_blocks, key=lambda b: (b.y0, b.y1)):
        entries.append(_text_block_to_entry(block, eid))
        eid += 1

    for table in tables:
        entries.append(_table_to_entry(table, eid))
        eid += 1

    entries.sort(key=lambda e: (e.page, e.y0, 0 if e.kind == "text" else 1, e.y1, e.entry_id))
    for i, entry in enumerate(entries):
        entry.entry_id = i
    return entries


def count_entries(entries: List[DocumentEntry]) -> Tuple[int, int]:
    tables = sum(1 for e in entries if e.kind == "table")
    texts = sum(1 for e in entries if e.kind == "text")
    return tables, texts


# 公开别名（测试/structure_split 使用）
row_y_bounds = _row_y_bounds
