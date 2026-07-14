# -*- coding: utf-8 -*-
"""Item 级数据守恒：scope 内每个 liteparse 片段必须出现在 TABLE/TEXT 输出中。"""

from __future__ import annotations

import copy
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from codes.table_engine.geometry.column_anchors import col_index_by_anchor, is_pd_range_cell_text
from codes.table_engine.models import BBox, Cell, DocumentEntry, PageSource, SourceItem, StructuredTable, TextBlock


def table_source_item_ids(table: StructuredTable) -> Set[str]:
    ids: Set[str] = set()
    for row in table.rows:
        for cell in row:
            if cell is None:
                continue
            for sid in cell.source_items or []:
                if sid:
                    ids.add(str(sid))
    desc = table.metadata.get("description_source_items") or []
    ids.update(str(s) for s in desc)
    return ids


def refresh_scope_source_items(table: StructuredTable) -> StructuredTable:
    """按当前表 cell 重建 scope_source_items（结构拆分后避免跨表补挂）。"""
    table.metadata["scope_source_items"] = sorted(table_source_item_ids(table))
    return table


def _table_y_span(table: StructuredTable, *, margin: float = 12.0) -> Optional[Tuple[float, float]]:
    ys: List[float] = []
    for row in table.rows:
        for cell in row:
            if cell is None:
                continue
            ys.append(float(cell.bbox.y0))
            ys.append(float(cell.bbox.y1))
    if not ys:
        return None
    return (min(ys) - margin, max(ys) + margin)


def entries_covered_item_ids(entries: List[DocumentEntry]) -> Set[str]:
    covered: Set[str] = set()
    for entry in entries:
        if entry.kind == "text" and entry.text_block:
            covered.update(str(s) for s in entry.text_block.source_items or [])
        if entry.kind == "table" and entry.table:
            covered |= table_source_item_ids(entry.table)
    return covered


def scope_item_ids(table: StructuredTable) -> Set[str]:
    raw = table.metadata.get("scope_source_items") or []
    return {str(s) for s in raw if s}


def _item_lookup(page: PageSource) -> Dict[str, SourceItem]:
    return {it.item_index: it for it in page.items}


def _table_text_blob(table: StructuredTable) -> str:
    parts: List[str] = []
    for row in table.rows:
        for cell in row:
            if cell is None:
                continue
            t = str(cell.text or "").strip()
            if t:
                parts.append(t)
    return " ".join(parts)


def _union_text_preserve(existing: str, incoming: str) -> str:
    """合并单元格文本：只增不减，禁止静默丢字。"""
    a = str(existing or "").strip()
    b = str(incoming or "").strip()
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return a
    ac = a.replace(" ", "")
    bc = b.replace(" ", "")
    if bc in ac:
        return a
    if ac in bc:
        return b
    return a if len(ac) >= len(bc) else b


def table_preserves_text_content(
    before: StructuredTable,
    after: StructuredTable,
    *,
    require_source_ids: bool = False,
) -> bool:
    """变换后表内已有文本与 source 引用不得丢失。"""
    if require_source_ids:
        before_ids = table_source_item_ids(before)
        after_ids = table_source_item_ids(after)
        if not before_ids.issubset(after_ids):
            return False
    after_blob = _table_text_blob(after).replace(" ", "")
    for row in before.rows:
        for cell in row:
            if cell is None:
                continue
            t = str(cell.text or "").strip()
            if not t:
                continue
            if t.replace(" ", "") not in after_blob:
                return False
    return True


def apply_table_transform_guard(
    before: StructuredTable,
    after: StructuredTable,
    *,
    require_source_ids: bool = False,
) -> StructuredTable:
    """表变换守恒闸门：丢字则回滚。"""
    if table_preserves_text_content(
        before, after, require_source_ids=require_source_ids,
    ):
        return after
    return before


def partition_scope_source_items(
    table: StructuredTable,
    parent_scope_ids: Sequence[str],
    item_lookup: Dict[str, SourceItem],
    *,
    margin: float = 8.0,
) -> List[str]:
    """按子表 Y 带从父 scope 切分 item（拆分后勿仅用 cell 内 id 缩小 scope）。"""
    y_lo = float(table.y0) - margin
    y_hi = float(table.y1) + margin
    out: List[str] = []
    for sid in parent_scope_ids:
        it = item_lookup.get(str(sid))
        if it is None:
            continue
        if not str(it.text or "").strip():
            continue
        ym = float(it.y_mid or (it.bbox.y0 + it.bbox.y1) / 2.0)
        if y_lo <= ym <= y_hi:
            out.append(str(sid))
    return sorted(set(out))


def assign_split_scope_source_items(
    table: StructuredTable,
    parent_scope_ids: Sequence[str],
    item_lookup: Optional[Dict[str, SourceItem]],
) -> StructuredTable:
    """结构拆分后：按几何切分父 scope，保证子表仍对范围内 item 负责。"""
    if item_lookup and parent_scope_ids:
        table.metadata["scope_source_items"] = partition_scope_source_items(
            table, parent_scope_ids, item_lookup,
        )
    else:
        refresh_scope_source_items(table)
    return table


def expand_scope_source_items(table: StructuredTable) -> StructuredTable:
    """scope 只扩不缩：并入 cell 已有 source，禁止拆分时丢失待补挂 item。"""
    scope = set(scope_item_ids(table))
    scope |= table_source_item_ids(table)
    table.metadata["scope_source_items"] = sorted(scope)
    return table


def _text_from_source_items(
    source_ids: List[str],
    item_lookup: Dict[str, SourceItem],
) -> str:
    parts: List[Tuple[float, str]] = []
    seen: Set[str] = set()
    for sid in source_ids:
        it = item_lookup.get(str(sid))
        if it is None:
            continue
        t = str(it.text or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        parts.append((float(it.bbox.y0), float(it.bbox.x0), t))
    if not parts:
        return ""
    parts.sort(key=lambda p: (p[0], p[1]))
    return " ".join(t for _, _, t in parts)


def reconcile_cell_texts_from_sources(
    table: StructuredTable,
    item_lookup: Dict[str, SourceItem],
) -> StructuredTable:
    """按 source_items 对齐 cell.text；已有更完整文本时禁止覆盖变短。"""
    from codes.table_engine.geometry.cell_decomposition import cell_should_skip_source_reglue

    decomposed_ids = {
        str(s) for s in (table.metadata.get("decomposed_source_ids") or []) if s
    }

    for row in table.rows:
        for cell in row:
            if cell is None or not cell.source_items:
                continue
            existing = str(cell.text or "").strip()
            src = [str(s) for s in cell.source_items if s]
            expected = _text_from_source_items(src, item_lookup)
            if not expected or expected == existing:
                continue
            if cell_should_skip_source_reglue(
                cell,
                row,
                full_source_text=expected,
                decomposed_source_ids=decomposed_ids or None,
            ):
                continue
            if len(src) == 1 and existing:
                it = item_lookup.get(src[0])
                if it is not None:
                    full = str(it.text or "").strip()
                    parts = _orphan_text_parts(full)
                    if parts and existing in parts:
                        continue
                    if existing in full and len(full) > len(existing) + 3:
                        if parts and all(
                            any(p in str(c.text or "") for c in row if c)
                            for p in parts
                        ):
                            continue
                        continue
            cell.text = _union_text_preserve(existing, expected)
    return table


def _nearest_row_index(table: StructuredTable, y: float) -> int:
    best_i = 0
    best_d = float("inf")
    for ri, row in enumerate(table.rows):
        ys: List[float] = []
        for cell in row:
            if cell is None:
                continue
            ys.append(float(cell.bbox.y0))
            ys.append(float(cell.bbox.y1))
        if not ys:
            continue
        mid = (min(ys) + max(ys)) / 2.0
        d = abs(y - mid)
        if d < best_d:
            best_d = d
            best_i = ri
    return best_i


def _ensure_row_width(row: List[Optional[Cell]], ncols: int) -> List[Optional[Cell]]:
    if len(row) >= ncols:
        return row
    return list(row) + [None] * (ncols - len(row))


def _merge_into_cell(
    cell: Optional[Cell],
    it: SourceItem,
    item_lookup: Dict[str, SourceItem],
    *,
    row: int,
    col: int,
) -> Cell:
    sid = str(it.item_index)
    text = str(it.text or "").strip()
    if cell is None:
        return Cell(
            text=text,
            bbox=BBox(it.bbox.x0, it.bbox.y0, it.bbox.x1, it.bbox.y1),
            row=row,
            col=col,
            source_items=[sid],
        )
    src = list(cell.source_items or [])
    if sid not in src:
        src.append(sid)
    merged_text = _text_from_source_items(src, item_lookup)
    if not merged_text:
        merged_text = text
    existing = str(cell.text or "").strip()
    cell.text = _union_text_preserve(existing, merged_text)
    x0 = min(cell.bbox.x0, it.bbox.x0)
    y0 = min(cell.bbox.y0, it.bbox.y0)
    x1 = max(cell.bbox.x1, it.bbox.x1)
    y1 = max(cell.bbox.y1, it.bbox.y1)
    cell.source_items = src
    cell.bbox = BBox(x0, y0, x1, y1)
    return cell


def _orphan_text_parts(text: str) -> List[str]:
    t = str(text or "").strip()
    if not t:
        return []
    m = re.match(r"^(\d+[a-z]?)\s+(.+)$", t, re.I)
    if m:
        return [m.group(1).strip(), m.group(2).strip()]
    if " " not in t:
        return []
    parts = [p.strip() for p in t.split() if len(p.strip()) >= 4]
    return parts if len(parts) >= 2 else []


def _row_cells_cover_orphan_parts(
    row: List[Optional[Cell]],
    parts: List[str],
) -> bool:
    if not parts:
        return False
    texts = [str(c.text or "").strip() for c in row if c and str(c.text or "").strip()]
    if not texts:
        return False
    return all(any(p in t for t in texts) for p in parts)


def _distribute_orphan_source_to_row(
    row: List[Optional[Cell]],
    it: SourceItem,
    parts: List[str],
) -> bool:
    """行内已分列呈现 orphan 各片段时，只补 source_items，不改 cell 文本。"""
    sid = str(it.item_index)
    touched = False
    for cell in row:
        if cell is None:
            continue
        t = str(cell.text or "").strip()
        if not t:
            continue
        if not any(p in t for p in parts):
            continue
        src = list(cell.source_items or [])
        if sid not in src:
            src.append(sid)
            cell.source_items = src
            touched = True
    return touched


def _orphan_category_fragment_subsumed(
    table: StructuredTable,
    it: SourceItem,
) -> bool:
    """CR6 暴露类别已 rollup 合并后，勿再把「机构」等碎片补挂到数据行。"""
    ranges = table.grid.ranges
    if len(ranges) < 2 or ranges[0].role != "category":
        return False
    t = str(it.text or "").strip()
    if not t or len(t) > 12 or is_pd_range_cell_text(t):
        return False
    if float(it.bbox.x0) >= 72.0:
        return False
    for row in table.rows:
        if not row:
            continue
        cell = row[0]
        if cell is None:
            continue
        ct = str(cell.text or "").strip()
        if len(ct) > len(t) and t in ct:
            return True
    return False


def attach_orphan_scope_items(
    table: StructuredTable,
    item_lookup: Dict[str, SourceItem],
    *,
    text_covered: Optional[Set[str]] = None,
) -> Tuple[StructuredTable, int]:
    """将 scope 内未进 TABLE、且未在 TEXT 中的 item 按坐标补挂到最近格。"""
    scope_ids = scope_item_ids(table)
    if not scope_ids:
        return table, 0

    text_ok = text_covered or set()
    covered = table_source_item_ids(table)
    orphans = [
        item_lookup[sid]
        for sid in scope_ids
        if sid not in covered
        and sid not in text_ok
        and sid in item_lookup
    ]
    orphans = [it for it in orphans if str(it.text or "").strip()]
    if not orphans:
        return table, 0

    out = copy.deepcopy(table)
    y_span = _table_y_span(out)
    if y_span is not None:
        y_lo, y_hi = y_span
        orphans = [
            it for it in orphans
            if y_lo <= float(it.bbox.y0) <= y_hi
        ]
    if not orphans:
        return table, 0

    col_ranges = [(cr.x0, cr.x1) for cr in out.grid.ranges]
    ncols = len(col_ranges) or max((len(r) for r in out.rows), default=0)
    if ncols < 1:
        return table, 0

    attached = 0
    for it in sorted(orphans, key=lambda x: (x.bbox.y0, x.bbox.x0)):
        if _orphan_category_fragment_subsumed(out, it):
            continue
        parts = _orphan_text_parts(str(it.text or ""))
        ri = _nearest_row_index(out, float(it.bbox.y0))
        out.rows[ri] = _ensure_row_width(list(out.rows[ri]), ncols)
        if parts and _row_cells_cover_orphan_parts(out.rows[ri], parts):
            if _distribute_orphan_source_to_row(out.rows[ri], it, parts):
                attached += 1
            continue
        ci = col_index_by_anchor(
            float(it.bbox.x0), float(it.bbox.x1), str(it.text), col_ranges,
        )
        if not (0 <= ci < ncols):
            ci = min(ncols - 1, max(0, ci))
        out.rows[ri] = _ensure_row_width(list(out.rows[ri]), ncols)
        out.rows[ri][ci] = _merge_into_cell(
            out.rows[ri][ci], it, item_lookup, row=ri, col=ci,
        )
        attached += 1

    if attached:
        out.metadata["item_conservation_attached"] = attached
    return out, attached


def column_has_scope_item_support(
    table: StructuredTable,
    col_j: int,
    item_lookup: Dict[str, SourceItem],
    *,
    text_covered: Optional[Set[str]] = None,
) -> bool:
    """剪枝前：该列是否已有表内内容（仅空列不因子 scope 几何占位而保留）。"""
    if col_j >= len(table.grid.ranges):
        return False
    for row in table.rows:
        if col_j >= len(row):
            continue
        cell = row[col_j]
        if cell is None:
            continue
        if str(cell.text or "").strip():
            return True
        if cell.source_items:
            return True
    return False


def audit_scope_conservation(
    entries: List[DocumentEntry],
    page: PageSource,
) -> List[str]:
    """返回须在表内出现但仍缺失的 scope item id（TEXT 已覆盖的不算丢失）。"""
    item_lookup = _item_lookup(page)
    text_covered = entries_covered_item_ids(
        [e for e in entries if e.kind == "text"],
    )
    missing: List[str] = []
    for entry in entries:
        if entry.kind != "table" or entry.table is None:
            continue
        scope_ids = scope_item_ids(entry.table)
        if not scope_ids:
            continue
        covered = table_source_item_ids(entry.table)
        for sid in sorted(scope_ids):
            if sid in covered or sid in text_covered:
                continue
            if sid in item_lookup and str(item_lookup[sid].text or "").strip():
                missing.append(sid)
    return missing


def spill_uncovered_scope_items_to_text(
    entries: List[DocumentEntry],
    page: PageSource,
) -> List[DocumentEntry]:
    """scope 内仍未进 TABLE 的 item → TEXT（最后兜底，确保不丢字）。"""
    missing = audit_scope_conservation(entries, page)
    if not missing:
        return entries
    lookup = _item_lookup(page)
    orphans = [lookup[sid] for sid in missing if sid in lookup]
    orphans = [it for it in orphans if str(it.text or "").strip()]
    if not orphans:
        return entries
    orphans.sort(key=lambda it: (it.bbox.y0, it.bbox.x0))
    text = " ".join(str(it.text).strip() for it in orphans)
    y0 = min(it.bbox.y0 for it in orphans)
    y1 = max(it.bbox.y1 for it in orphans)
    max_eid = max((e.entry_id for e in entries), default=-1)
    block = TextBlock(
        page=page.page_number,
        y0=y0,
        y1=y1,
        text=text,
        source_items=[str(it.item_index) for it in orphans],
    )
    entries = list(entries)
    entries.append(
        DocumentEntry(
            entry_id=max_eid + 1,
            kind="text",
            page=page.page_number,
            y0=y0,
            y1=y1,
            text_block=block,
        ),
    )
    return entries


def apply_item_conservation(
    entries: List[DocumentEntry],
    page: PageSource,
    warnings: Optional[List[str]] = None,
) -> List[DocumentEntry]:
    """流水线硬约束：补挂孤儿 item、对齐 cell 文本、审计 scope 守恒。"""
    item_lookup = _item_lookup(page)
    warn = warnings if warnings is not None else []
    text_covered = entries_covered_item_ids(
        [e for e in entries if e.kind == "text"],
    )

    for entry in entries:
        if entry.kind != "table" or entry.table is None:
            continue
        table = entry.table
        for _ in range(3):
            table = reconcile_cell_texts_from_sources(table, item_lookup)
            table, n_attached = attach_orphan_scope_items(
                table, item_lookup, text_covered=text_covered,
            )
            if n_attached <= 0:
                break
        entry.table = table

    entries = spill_uncovered_scope_items_to_text(entries, page)

    missing = audit_scope_conservation(entries, page)
    if missing:
        samples = []
        for sid in missing[:5]:
            it = item_lookup.get(sid)
            if it:
                samples.append(f"{it.text[:20]!r}@{it.bbox.y0:.0f}")
        warn.append(
            f"P{page.page_number}: scope item loss {len(missing)} "
            f"({'; '.join(samples)})"
        )
        for entry in entries:
            if entry.kind == "table" and entry.table:
                entry.table.metadata["scope_item_loss"] = len(missing)

    return entries
