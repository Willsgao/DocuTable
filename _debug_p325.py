# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_access import dense_rows

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 325)
result = build_page(page)
for ei, e in enumerate(result.entries):
    if e.kind == "text" and e.text_block:
        t = e.text_block.text[:60].replace("\n", " ")
        print(f"TEXT {ei}: {t}")
    if e.table:
        rows = dense_rows(e.table)
        if any(any("82,590" in c for c in r) for r in rows):
            print(f"\n=== TABLE stage3 (entry {ei}) rows={len(rows)} ===")
            for i, r in enumerate(rows):
                print(i, r)
            print("ranges", [(round(cr.x0), round(cr.x1)) for cr in e.table.grid.ranges])
