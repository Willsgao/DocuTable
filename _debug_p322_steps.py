# -*- coding: utf-8 -*-
from pathlib import Path

from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.table_access import dense_rows
from codes.table_engine.split.table_text_split import build_page_entries
from codes.table_engine.split.structure_split import apply_structure_split
from codes.table_engine.conservation.item_conservation import apply_item_conservation

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 322)
plan = plan_page_scopes(page)
tables = [build_table_from_scope(s) for s in plan.scopes if build_table_from_scope(s)]
entries = build_page_entries(tables=tables, gap_texts=list(plan.gap_texts))

def show(name, entries):
    for e in entries:
        if e.kind == "table" and e.table and any(any("4,031" in c for c in r) for r in dense_rows(e.table)):
            print(name, dense_rows(e.table)[1])

show("start", entries)
entries = apply_structure_split(entries, page)
show("structure_split", entries)
entries = apply_item_conservation(entries, page, [])
show("conservation", entries)
