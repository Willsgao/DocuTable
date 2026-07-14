# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.table_access import dense_rows
from codes.table_engine.split.structure_split import find_structure_break_row
from codes.table_engine.split.row_classify import is_inter_table_narrative_row, is_intra_table_section_row

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
scope = plan_page_scopes(page).scopes[0]
table = build_table_from_scope(scope)
rows = dense_rows(table)
print("built rows", len(rows))
for i, r in enumerate(rows):
    print(i, r)
print("--- structure break ---")
print(find_structure_break_row(rows))
