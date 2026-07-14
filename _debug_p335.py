# -*- coding: utf-8 -*-
from pathlib import Path

from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.table_builder import build_table_from_scope
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows
from codes.table_engine.geometry.column_anchors import col_index_by_anchor, item_column_anchor
from codes.table_engine.geometry.cell_builder import assign_rows_to_columns
from codes.table_engine.table_access import dense_rows

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 335)
scope = plan_page_scopes(page).scopes[0]
rows = refine_clustered_rows(cluster_items_by_y(source_items_to_dicts(scope.items), use_dynamic_threshold=True))
t = build_table_from_scope(scope)
col_ranges = [(r.x0, r.x1) for r in t.grid.ranges]
print("layout", t.layout_id)
print("col_ranges:")
for i, cr in enumerate(col_ranges):
    print(f"  col{i}: [{cr[0]:.1f}, {cr[1]:.1f}]")

for row in rows:
    texts = [it["text"] for it in row["items"]]
    if "本集团" in texts or "本行" in texts:
        print("entity row items:")
        for it in row["items"]:
            text = it["text"]
            ci = col_index_by_anchor(float(it["x0"]), float(it["x1"]), text, col_ranges)
            anchor = item_column_anchor(it)
            print(f"  {text!r} x0={it['x0']:.1f} x1={it['x1']:.1f} anchor={anchor:.1f} ci={ci}")

matrix = assign_rows_to_columns(rows, col_ranges, t.layout_id, page.page_number)
for i, row in enumerate(matrix):
    cells = [c.text if c else "" for c in row]
    if "本集团" in cells or "本行" in cells:
        print("assigned row", i, cells)
