# -*- coding: utf-8 -*-
import json
from pathlib import Path

from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_access import dense_rows

cache = Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json")
page = load_page(cache, 322)

print("=== RAW ITEMS (header band) ===")
for it in page.items:
    t = it.text.strip()
    if ("已逾期" in t or "已发生" in t or "2024年12月" in t or "2023年12月" in t or "贷款和垫款" in t) and 200 < it.y0 < 340:
        print(f"y={it.y0:.1f}-{it.y1:.1f} x={it.x0:.1f}-{it.x1:.1f} | {t}")

print("\n=== PIPELINE OUTPUT ===")
for ei, e in enumerate(build_page(page).entries):
    if e.kind != "table" or not e.table:
        continue
    t = e.table
    rows = dense_rows(t)
    print(f"TABLE {ei} rows={len(rows)} cols={len(rows[0]) if rows else 0}")
    if t.grid:
        for i, cr in enumerate(t.grid.ranges):
            print(f"  col{i}: [{cr.x0:.1f}, {cr.x1:.1f}]")
    for ri, r in enumerate(rows):
        if any("已逾期" in c or "已发生" in c or "2023年" in c or "2024年" in c for c in r):
            print(f"  R{ri}: {r}")
