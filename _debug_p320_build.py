# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_access import dense_rows

cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
page = load_page(cache, 320)
result = build_page(page)
for e in result.entries:
    if e.table:
        rows = dense_rows(e.table)
        print("rows", len(rows))
        for i, r in enumerate(rows):
            print(i, r)
