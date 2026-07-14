# -*- coding: utf-8

"""表尾脚注/注释行剥离。"""



from __future__ import annotations



import copy

from typing import List, Tuple



from codes.table_engine.models import DocumentEntry, StructuredTable, TextBlock
from codes.table_engine.conservation.item_conservation import refresh_scope_source_items
from codes.table_engine.split.row_classify import is_tail_annotation_row

from codes.table_engine.table_access import dense_rows





def _pad_row(row: List[str], width: int) -> List[str]:

    cells = [str(c).strip() for c in row]

    if len(cells) < width:

        cells.extend([""] * (width - len(cells)))

    return cells[:width]





def strip_tail_annotations(

    table: StructuredTable,

    *,

    max_scan: int = 5,

    min_keep_rows: int = 2,

) -> Tuple[StructuredTable, List[str]]:

    rows = dense_rows(table)

    if len(rows) < min_keep_rows:

        return table, []



    width = table.grid.col_count or max((len(r) for r in rows), default=0)

    limit = min(max_scan, len(rows) - min_keep_rows)

    stripped: List[str] = []

    remove = 0



    for _ in range(limit):

        row = rows[-1 - remove]

        if not is_tail_annotation_row(row, width):

            break

        text = " ".join(c for c in _pad_row(row, width) if c).strip()

        if text:

            stripped.insert(0, text)

        remove += 1



    if remove <= 0:

        return table, stripped



    from codes.table_engine.split.table_text_split import slice_structured_table



    out = slice_structured_table(table, 0, len(rows) - remove)

    return out, stripped





def apply_footnote_strip(entries: List[DocumentEntry]) -> List[DocumentEntry]:

    """剥离表尾注释行，输出为 TEXT 条目（表→文转移，非静默删除）。"""

    from codes.table_engine.split.table_text_split import _text_block_to_entry, row_y_bounds



    out: List[DocumentEntry] = []

    for entry in entries:

        if entry.kind != "table" or entry.table is None:

            out.append(entry)

            continue

        orig = entry.table

        table, stripped = strip_tail_annotations(orig)

        new_entry = copy.copy(entry)

        new_entry.table = table

        new_entry.y0 = table.y0

        new_entry.y1 = table.y1

        out.append(new_entry)

        if stripped:
            orig_rows = dense_rows(orig)
            tail_start = len(orig_rows) - len(stripped)
            y0, y1 = row_y_bounds(orig, tail_start, len(orig_rows))
            tail_ids: List[str] = []
            for ri in range(tail_start, len(orig.rows)):
                for cell in orig.rows[ri]:
                    if cell is None:
                        continue
                    for sid in cell.source_items or []:
                        if sid and sid not in tail_ids:
                            tail_ids.append(str(sid))
            block = TextBlock(
                page=table.page,
                y0=y0,
                y1=y1,
                text="\n".join(stripped),
                source_items=tail_ids,
            )
            refresh_scope_source_items(table)
            eid = max((e.entry_id for e in out), default=-1) + 1
            out.append(_text_block_to_entry(block, eid))

    return out


