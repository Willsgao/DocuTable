# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 320)
scope = plan_page_scopes(page).scopes[0]
for it in sorted(scope.items, key=lambda i: (i.bbox.y0, i.bbox.x0)):
    t = it.text.strip()
    if any(k in t for k in ("本集团", "本行", "2024", "2023", "12月")):
        print(f"y={it.bbox.y0:.0f} x0={it.bbox.x0:.0f} {t!r}")

dicts = source_items_to_dicts(scope.items)
rows_raw = cluster_items_by_y(dicts, use_dynamic_threshold=True)
print("--- raw ---")
for row in rows_raw:
    y0 = min(float(it.get("y0", 0)) for it in row.get("items", []))
    if 265 < y0 < 310:
        parts = [(f"{it['x0']:.0f}", it["text"]) for it in sorted(row.get("items", []), key=lambda d: d["x0"])]
        print(f"y={y0:.0f}", parts)

rows = refine_clustered_rows(rows_raw)
print("--- refined ---")
for row in rows:
    y0 = min(float(it.get("y0", 0)) for it in row.get("items", []))
    if 265 < y0 < 310:
        parts = [(f"{it['x0']:.0f}", it["text"]) for it in sorted(row.get("items", []), key=lambda d: d["x0"])]
        print(f"y={y0:.0f}", parts)
