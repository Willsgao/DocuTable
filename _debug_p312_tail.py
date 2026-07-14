# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_access import dense_rows

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
result = build_page(page)
for ei, e in enumerate(result.entries):
    if e.table:
        rows = dense_rows(e.table)
        print(f"=== entry {ei} kind={e.kind} rows={len(rows)} ===")
        for i, r in enumerate(rows):
            joined = " | ".join(c for c in r if c.strip())
            if len(joined) > 80:
                joined = joined[:80] + "..."
            print(f"{i:2d} {joined}")
        print()
