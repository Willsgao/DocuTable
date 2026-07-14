# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.conservation.item_conservation import (
    table_source_item_ids, scope_item_ids, attach_orphan_scope_items, _item_lookup
)
from codes.table_engine.split.structure_split import split_table_by_structure
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.table_access import dense_rows

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
scope = plan_page_scopes(page).scopes[0]
table = build_table_from_scope(scope)
parts = split_table_by_structure(table)
lookup = _item_lookup(page)
part0 = parts[0]
print("part0 scope items", len(scope_item_ids(part0)))
print("part0 covered", len(table_source_item_ids(part0)))
orphans = [sid for sid in scope_item_ids(part0) if sid not in table_source_item_ids(part0) and sid in lookup]
print("orphans count", len(orphans))
for sid in orphans[:15]:
    it = lookup[sid]
    print(f"  y={it.bbox.y0:.0f} {it.text[:30]!r}")
