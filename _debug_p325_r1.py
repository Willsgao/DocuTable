# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows
from codes.table_engine.table_builder import build_table_from_scope

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 325)
scopes = plan_page_scopes(page).scopes
# region 1 scope
scope = scopes[1] if len(scopes) > 1 else scopes[0]
for it in sorted(scope.items, key=lambda i: (i.bbox.y0, i.bbox.x0)):
    if it.bbox.y0 > 390:
        print(f"y={it.bbox.y0:.0f} x0={it.bbox.x0:.0f} x1={it.bbox.x1:.0f} {it.text.strip()!r}")

dicts = source_items_to_dicts(scope.items)
rows = refine_clustered_rows(cluster_items_by_y(dicts, use_dynamic_threshold=True))
print("--- refined rows y>390 ---")
for row in rows:
    y0 = min(float(it.get("y0", 0)) for it in row.get("items", []))
    if y0 > 390:
        parts = [(f"{it['x0']:.0f}", it["text"]) for it in sorted(row.get("items", []), key=lambda d: d["x0"])]
        print(f"y={y0:.0f}", parts)

table = build_table_from_scope(scope)
if table:
    from codes.table_engine.table_access import dense_rows
    print("col ranges", [(round(r.x0), round(r.x1)) for r in table.grid.ranges])
    print("layout", table.layout_id)
