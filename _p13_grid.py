# -*- coding: utf-8 -*-
from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.scope.gap_capture import plan_page_scopes
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.grid_infer import infer_constraint_grid, merge_grid_with_layout, refine_col_ranges_by_coordinates
from codes.table_engine.layout.base import LayoutContext
from codes.table_engine.layout.registry import select_layout
from codes.table_engine.geometry.cell_builder import _assign_item_to_columns
from codes.table_engine.geometry.row_refiner import refine_clustered_rows

page = load_page(DEFAULT_PILLAR_CACHE, 13)
scope = plan_page_scopes(page).scopes[0]
dicts = source_items_to_dicts(scope.items)
rows = refine_clustered_rows(cluster_items_by_y(dicts, use_dynamic_threshold=True))
ctx = LayoutContext(
    page=13, scope_y0=scope.scope_y0, region_y0=scope.region.y0,
    region_y1=scope.region.y1, items=dicts, rows=rows,
)
selection, _ = select_layout(ctx)
print("layout:", selection.layout_id, selection.col_ranges)

x_lo = float(scope.region.x0) - 5
x_hi = float(scope.region.x1) + 5
grid = infer_constraint_grid(rows, x_lo, x_hi)
print("grid:", grid.method if grid else None, "cols", grid.col_count if grid else 0)
if grid:
    print("grid ranges:", [(round(a,1), round(b,1)) for a,b in grid.col_ranges])

ranges, lid, meta = merge_grid_with_layout(grid, selection.layout_id, selection.col_ranges, selection.confidence)
ranges = refine_col_ranges_by_coordinates(rows, ranges, x_lo, x_hi)
print("merged:", lid, "n=", len(ranges))
for i, r in enumerate(ranges):
    print(f"  col{i}: {round(r[0],1)}-{round(r[1],1)}")


def trace_row(key_serial, key_label):
    for row in rows:
        texts = [str(d.get("text", "")) for d in row.get("items", [])]
        if key_serial in texts and any(key_label in t for t in texts):
            print(f"--- row {key_serial} {key_label} ---")
            for it in row.get("items", []):
                col_items = [[] for _ in range(len(ranges))]
                _assign_item_to_columns(it, ranges, col_items, len(ranges), lid)
                ci = next((i for i, c in enumerate(col_items) if c), -1)
                print(f"  {it.get('text')!r} x0={float(it.get('x0')):.1f} x1={float(it.get('x1')):.1f} -> col {ci}")
            return


trace_row("1", "现金")
trace_row("3", "贵金属")
trace_row("14", "固定")
