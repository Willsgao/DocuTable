# -*- coding: utf-8 -*-
from pathlib import Path

from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_access import dense_rows
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.split.structure_split import find_structure_break_row, split_table_by_structure

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 322)
result = build_page(page)
# rebuild unsplit: use first table from tables if available, else merge entries
from codes.table_engine.pipeline import build_page as bp
plan = plan_page_scopes(page)
table = build_table_from_scope(plan.scopes[0])

parts = split_table_by_structure(table)
t2 = parts[1]
print("t2 final", [r for r in dense_rows(t2) if "逾期" in "".join(r)])
