# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.table_access import dense_rows
from codes.table_engine.conservation.item_conservation import apply_item_conservation
from codes.table_engine.split.grid_prune import apply_grid_prune
from codes.table_engine.split.structure_split import apply_structure_split, split_table_by_structure
from codes.table_engine.split.trailing_header_reattach import apply_trailing_header_reattach
from codes.table_engine.split.footnote_strip import apply_footnote_strip
from codes.table_engine.split.table_text_split import build_page_entries
from codes.table_engine.models import DocumentEntry

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
scope = plan_page_scopes(page).scopes[0]
table = build_table_from_scope(scope)

def show(label, t):
    rows = dense_rows(t)
    print(f"--- {label} rows={len(rows)} ---")
    for i in [15,16,17,18,19,20]:
        if i < len(rows):
            r = rows[i]
            s = " | ".join(c for c in r if c.strip())
            print(f"  {i} {s[:100]}")

show("built", table)
parts = split_table_by_structure(table)
print("split parts", [len(dense_rows(p)) for p in parts])
show("part0", parts[0])
if len(parts)>1: show("part1 head", parts[1])

from codes.table_engine.source.liteparse_loader import load_page
item_lookup = {it.item_index: it for it in page.items}
t2 = apply_item_conservation(parts[0], item_lookup)
show("part0 after conservation", t2)
