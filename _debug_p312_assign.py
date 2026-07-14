# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows
from codes.table_engine.geometry.grid_infer import infer_constraint_grid, merge_grid_with_layout, refine_col_ranges_by_coordinates
from codes.table_engine.layout.registry import select_layout
from codes.table_engine.layout.base import LayoutContext
from codes.table_engine.geometry.cell_builder import assign_rows_to_columns, _cell_text_from_items
from codes.table_engine.geometry.cell_builder import build_structured_table

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
scope = plan_page_scopes(page).scopes[0]
dicts = source_items_to_dicts(scope.items)
rows = refine_clustered_rows(cluster_items_by_y(dicts, use_dynamic_threshold=True))
ctx = LayoutContext(page=312, scope_y0=scope.scope_y0, region_y0=scope.region.y0, region_y1=scope.region.y1, items=dicts, rows=rows)
sel, _ = select_layout(ctx)
x_lo, x_hi = scope.region.x0 - 5, scope.region.x1 + 5
grid = infer_constraint_grid(rows, x_lo, x_hi)
col_ranges, layout_id, _ = merge_grid_with_layout(grid, sel.layout_id, sel.col_ranges, sel.confidence, rows=rows, x_lo=x_lo, x_hi=x_hi)
col_ranges = refine_col_ranges_by_coordinates(rows, col_ranges, x_lo, x_hi)
print("ranges", [(round(a), round(b)) for a,b in col_ranges])
col_items = assign_rows_to_columns(rows, col_ranges, layout_id)
for ri, row in enumerate(col_items):
    y0 = min(float(it.get("y0",0)) for items in row for it in items) if any(row) else 0
    if 218 < y0 < 228:
        for ci, items in enumerate(row):
            if items:
                print(f"row{ri} col{ci}", _cell_text_from_items(items), [(it['text'], it['x0']) for it in items])
