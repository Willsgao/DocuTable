# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
scope = plan_page_scopes(page).scopes[0]
dicts = source_items_to_dicts(scope.items)
rows_raw = cluster_items_by_y(dicts, use_dynamic_threshold=True)
rows = refine_clustered_rows(rows_raw)
for row in rows:
    y0 = min(float(it.get("y0", 0)) for it in row.get("items", []))
    if 170 < y0 < 250:
        parts = [(f"{it['x0']:.0f}-{it['x1']:.0f}", it["text"]) for it in sorted(row.get("items", []), key=lambda d: d["x0"])]
        print(f"y={y0:.0f}", parts)

# body row with 652
for row in rows:
    y0 = min(float(it.get("y0", 0)) for it in row.get("items", []))
    if 295 < y0 < 315:
        parts = [(f"{it['x0']:.0f}-{it['x1']:.0f}", it["text"]) for it in sorted(row.get("items", []), key=lambda d: d["x0"])]
        print(f"body y={y0:.0f}", parts)
