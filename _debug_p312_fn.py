# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.split.table_text_split import build_page_entries
from codes.table_engine.split.content_partition import filter_description_captions
from codes.table_engine.split.structure_split import apply_structure_split
from codes.table_engine.table_access import dense_rows

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
plan = plan_page_scopes(page)
tables = [build_table_from_scope(plan.scopes[0])]
entries = build_page_entries(tables=tables, gap_texts=filter_description_captions(tables, list(plan.gap_texts)))
entries = apply_structure_split(entries)
for ei, e in enumerate(entries):
    if e.kind == "table" and e.table:
        rows = dense_rows(e.table)
        print(f"entry {ei} rows={len(rows)} last={rows[-1][:2]}")
        if any("(i)" in c and "业务及管理费主要指" in c for r in rows for c in r):
            print("  HAS footnote row in table")
