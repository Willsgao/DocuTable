# -*- coding: utf-8 -*-
from pathlib import Path

from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.table_access import dense_rows
from codes.table_engine.split.table_text_split import build_page_entries
from codes.table_engine.split.structure_split import apply_structure_split
from codes.table_engine.split.fragment_rejoin import apply_fragment_rejoin
from codes.table_engine.split.trailing_header_reattach import apply_trailing_header_reattach
from codes.table_engine.split.footnote_strip import apply_footnote_strip
from codes.table_engine.split.content_partition import apply_content_partition
from codes.table_engine.split.header_audit import apply_header_audit
from codes.table_engine.split.y_calibrate import apply_y_calibration
from codes.table_engine.split.grid_prune import apply_grid_prune
from codes.table_engine.conservation.item_conservation import apply_item_conservation, _item_lookup, attach_orphan_scope_items, reconcile_cell_texts_from_sources, entries_covered_item_ids
from codes.table_engine.split.structure_split import apply_sibling_compound_header_repair

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 330)
plan = plan_page_scopes(page)
tables = [build_table_from_scope(s) for s in plan.scopes if build_table_from_scope(s)]
entries = build_page_entries(tables=tables, gap_texts=list(plan.gap_texts))

def show_t3(name, entries):
    for e in entries:
        if e.kind == "table" and e.table and any(any("8,910,166" in c for c in r) for r in dense_rows(e.table)):
            r = dense_rows(e.table)
            print(name, "R3", r[3] if len(r) > 3 else None)

steps = [("start", lambda e: e)]
for name, fn in [
    ("structure_split", apply_structure_split),
    ("fragment_rejoin", apply_fragment_rejoin),
    ("trailing_header_reattach", apply_trailing_header_reattach),
    ("footnote_strip", apply_footnote_strip),
    ("content_partition", lambda e: apply_content_partition(e, page)),
    ("header_audit", lambda e: apply_header_audit(e, page, [])),
    ("y_calibrate", lambda e: apply_y_calibration(e, page)),
    ("grid_prune", lambda e: apply_grid_prune(e, page)),
    ("item_conservation", lambda e: apply_item_conservation(e, page, [])),
    ("sibling_repair", apply_sibling_compound_header_repair),
    ("grid_prune2", lambda e: apply_grid_prune(e, page)),
]:
    entries = fn(entries)
    show_t3(name, entries)
