# -*- coding: utf-8 -*-
from pathlib import Path
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows
from codes.table_engine.geometry.grid_infer import _ranges_from_numeric_gutters, _all_layout_items, _infer_note_column_bounds

page = load_page(Path("data/mid_cache/601939建设银行2024年年度报告/liteparse/pages.json"), 312)
scope = plan_page_scopes(page).scopes[0]
rows = refine_clustered_rows(cluster_items_by_y(source_items_to_dicts(scope.items), use_dynamic_threshold=True))
x_lo, x_hi = scope.region.x0 - 5, scope.region.x1 + 5
print("note_bounds", _infer_note_column_bounds(rows))
gr = _ranges_from_numeric_gutters(rows, _all_layout_items(rows), x_lo, x_hi)
print("gutter", [(round(a),round(b)) for a,b in gr])
