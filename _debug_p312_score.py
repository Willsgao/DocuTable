# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows
from codes.table_engine.layout.registry import select_layout
from codes.table_engine.layout.base import LayoutContext
from codes.table_engine.geometry.grid_infer import (
    infer_constraint_grid, refine_col_ranges_by_coordinates, _score_col_ranges,
    _ranges_from_numeric_gutters, _all_layout_items, _subdivide_ranges_by_numeric_gutters,
    _ranges_from_anchor_centers, _collect_column_anchor_xs,
)

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
scope = plan_page_scopes(page).scopes[0]
rows = refine_clustered_rows(cluster_items_by_y(source_items_to_dicts(scope.items), use_dynamic_threshold=True))
ctx = LayoutContext(page=312, scope_y0=scope.scope_y0, region_y0=scope.region.y0, region_y1=scope.region.y1, items=source_items_to_dicts(scope.items), rows=rows)
sel, _ = select_layout(ctx)
x_lo, x_hi = scope.region.x0 - 5, scope.region.x1 + 5
grid = infer_constraint_grid(rows, x_lo, x_hi)
base = grid.col_ranges if grid else sel.col_ranges
refined = refine_col_ranges_by_coordinates(rows, list(sel.col_ranges), x_lo, x_hi)
print("layout", [(round(a),round(b)) for a,b in sel.col_ranges], _score_col_ranges(sel.col_ranges, rows))
print("grid", [(round(a),round(b)) for a,b in base], _score_col_ranges(base, rows))
print("refined", [(round(a),round(b)) for a,b in refined], _score_col_ranges(refined, rows))
gr = _ranges_from_numeric_gutters(rows, _all_layout_items(rows), x_lo, x_hi)
print("gutter", [(round(a),round(b)) for a,b in gr], _score_col_ranges(gr, rows))
sub = _subdivide_ranges_by_numeric_gutters(sel.col_ranges, rows)
print("subdivide", [(round(a),round(b)) for a,b in sub], _score_col_ranges(sub, rows))
