# -*- coding: utf-8
"""表首缺表头时：从紧邻上方 TEXT / gap 向上回溯并 prepend 到表。"""

from __future__ import annotations

from typing import List, Optional, Set, Tuple
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.models import DocumentEntry, PageSource, SourceItem, TextBlock
from codes.table_engine.scope.header_supplement import (
    peel_missing_header_items_above,
    prepend_source_items_to_table,
    scan_table_header_incomplete,
)
from codes.table_engine.split.trailing_header_reattach import _scan_lower_header_state
from codes.table_engine.conservation.item_conservation import table_source_item_ids
from codes.table_engine.table_access import dense_rows

_MAX_TEXT_HEADER_GAP = 200.0


def _region_for_table(page: PageSource, entry: DocumentEntry):
    if entry.table is None:
        return None
    table = entry.table
    ry0 = float(table.metadata.get("region_y0", table.y0))
    for region in page.table_regions:
        if abs(region.y0 - ry0) <= 18.0:
            return region
    return None


def _items_from_text_block(
    page: PageSource,
    block: TextBlock,
) -> List[SourceItem]:
    ids = set(block.source_items or [])
    if ids:
        return [it for it in page.items if it.item_index in ids]
    y_lo = block.y0 - 2.0
    y_hi = block.y1 + 2.0
    return [
        it for it in page.items
        if y_lo <= it.bbox.y0 <= y_hi and str(it.text or "").strip()
    ]


def _trim_text_block_after_header_peel(
    block: TextBlock,
    peeled_ids: Set[str],
    page: PageSource,
) -> Optional[TextBlock]:
    if not peeled_ids:
        return block
    remaining = [
        it for it in _items_from_text_block(page, block)
        if it.item_index not in peeled_ids
    ]
    if not remaining:
        return None
    lines: List[str] = []
    dicts = source_items_to_dicts(remaining)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in remaining}
    for row in rows:
        row_items = sorted(
            [
                index_map[d["item_index"]]
                for d in row.get("items", [])
                if d.get("item_index") in index_map
            ],
            key=lambda it: it.x0,
        )
        line = " ".join(str(it.text).strip() for it in row_items if str(it.text).strip())
        if line.strip():
            lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        return None
    y0 = min(it.bbox.y0 for it in remaining)
    y1 = max(it.bbox.y1 for it in remaining)
    return TextBlock(
        page=block.page,
        y0=y0,
        y1=y1,
        text=text,
        source_items=[it.item_index for it in remaining],
    )


def _reattach_from_preceding_text(
    table_entry: DocumentEntry,
    text_entry: DocumentEntry,
    page: PageSource,
) -> Tuple[DocumentEntry, Optional[DocumentEntry]]:
    if table_entry.table is None or text_entry.text_block is None:
        return table_entry, text_entry

    gap = table_entry.y0 - text_entry.y1
    if gap > _MAX_TEXT_HEADER_GAP:
        return table_entry, text_entry

    region = _region_for_table(page, table_entry)
    if region is None:
        return table_entry, text_entry

    rows = dense_rows(table_entry.table)
    state = _scan_lower_header_state(rows[: min(10, len(rows))])
    if not scan_table_header_incomplete(table_entry.table):
        return table_entry, text_entry

    scope_ids = set(table_source_item_ids(table_entry.table))
    y_hi = min(table_entry.y0, text_entry.y1) + 2.0
    found = peel_missing_header_items_above(
        page,
        region,
        y_hi=y_hi,
        scope_item_ids=scope_ids,
        header_state=state,
    )
    if not found:
        return table_entry, text_entry

    peeled_ids = {it.item_index for it in found}
    new_table = prepend_source_items_to_table(table_entry.table, found)
    scope_ids.update(peeled_ids)
    new_table.metadata["scope_source_items"] = sorted(scope_ids)
    new_table.metadata["pre_header_count"] = int(
        new_table.metadata.get("pre_header_count", 0)
    ) + len(found)

    trimmed = _trim_text_block_after_header_peel(
        text_entry.text_block, peeled_ids, page,
    )
    new_text_entry: Optional[DocumentEntry] = None
    if trimmed is not None:
        new_text_entry = DocumentEntry(
            kind="text",
            page=text_entry.page,
            y0=trimmed.y0,
            y1=trimmed.y1,
            text_block=trimmed,
            entry_id=text_entry.entry_id,
        )

    return (
        DocumentEntry(
            kind="table",
            page=table_entry.page,
            y0=new_table.y0,
            y1=new_table.y1,
            table=new_table,
            entry_id=table_entry.entry_id,
        ),
        new_text_entry,
    )


def apply_leading_header_reattach(
    entries: List[DocumentEntry],
    page: PageSource,
) -> List[DocumentEntry]:
    """每张表检查表头完整性；缺则向上回溯紧邻 TEXT / gap 并回补。"""
    if not entries:
        return entries

    out: List[DocumentEntry] = []
    for entry in entries:
        if (
            entry.kind == "table"
            and entry.table is not None
            and scan_table_header_incomplete(entry.table)
        ):
            current = entry
            for back in range(len(out) - 1, -1, -1):
                prev = out[back]
                if prev.kind != "text" or prev.text_block is None:
                    break
                if prev.page != current.page:
                    break
                if current.y0 - prev.y1 > _MAX_TEXT_HEADER_GAP:
                    break
                new_table, new_text = _reattach_from_preceding_text(
                    current, prev, page,
                )
                if new_table.table is current.table:
                    break
                current = new_table
                if new_text is None:
                    out.pop(back)
                else:
                    out[back] = new_text
                if not scan_table_header_incomplete(current.table):
                    break
            out.append(current)
            continue
        out.append(entry)
    return out
