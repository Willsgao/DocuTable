# -*- coding: utf-8 -*-
from pathlib import Path

from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.table_access import dense_rows
from codes.table_engine.pipeline import build_page

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 328)
plan = plan_page_scopes(page)
for si, scope in enumerate(plan.scopes):
    t = build_table_from_scope(scope)
    if t and any(any("24,655,387" in (c.text if c else "") for c in r) for r in t.rows):
        print(f"=== SCOPE {si} raw build ===")
        for i, row in enumerate(t.rows):
            print(i, [c.text if c else "" for c in row])

print("\n=== FULL PIPELINE table3 ===")
for e in build_page(page).entries:
    if e.kind == "table" and e.table and any(
        any("24,655,387" in c for c in r) for r in dense_rows(e.table)
    ):
        for i, r in enumerate(dense_rows(e.table)):
            print(i, r)
