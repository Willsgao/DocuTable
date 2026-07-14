# -*- coding: utf-8
"""单表构建入口（Step 1+3）。"""

from __future__ import annotations

from typing import List, Optional

from codes.table_engine.geometry.boundary_expand import expand_scope_items
from codes.table_engine.geometry.cell_builder import build_structured_table
from codes.table_engine.geometry.grid_infer import (
    infer_constraint_grid,
    merge_grid_with_layout,
    refine_col_ranges_by_coordinates,
)
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows
from codes.table_engine.layout.base import LayoutContext
from codes.table_engine.layout.registry import select_layout
from codes.table_engine.models import PageSource, RegionBox, SourceItem, StructuredTable
from codes.table_engine.scope.header_scope import scope_y0_for_region
from codes.table_engine.scope.region_scope import (
    TableScope,
    build_table_scope,
    collect_items_in_band,
    merge_items_dedup,
)


def scope_items_for_region(
    page: PageSource,
    region: RegionBox,
    *,
    y_margin_below: float = 30.0,
    pre_header_items: Optional[List[SourceItem]] = None,
) -> List[SourceItem]:
    pre = list(pre_header_items or [])
    scope_y0 = scope_y0_for_region(page, region)
    if pre:
        scope_y0 = min(scope_y0, min(it.bbox.y0 for it in pre))
    band = collect_items_in_band(
        page,
        region,
        scope_y0=scope_y0,
        y_margin_below=y_margin_below,
    )
    scoped = merge_items_dedup(pre, band)
    return expand_scope_items(page, region, scoped)


def pick_primary_region(page: PageSource) -> Optional[RegionBox]:
    """取最大 table region（避免 P11 等页仅命中表头小 region）。"""
    if not page.table_regions:
        return None
    return max(
        page.table_regions,
        key=lambda r: (r.y1 - r.y0) * (r.x1 - r.x0),
    )


def build_table_from_scope(scope: TableScope) -> Optional[StructuredTable]:
    if len(scope.items) < 3:
        return None

    dicts = source_items_to_dicts(scope.items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    if len(rows) < 2:
        return None

    rows = refine_clustered_rows(rows)
    ctx = LayoutContext(
        page=scope.page_number,
        scope_y0=scope.scope_y0,
        region_y0=scope.region.y0,
        region_y1=scope.region.y1,
        items=dicts,
        rows=rows,
    )
    selection, _plugin = select_layout(ctx)
    x_lo = float(scope.region.x0) - 5.0
    x_hi = float(scope.region.x1) + 5.0
    grid = infer_constraint_grid(rows, x_lo, x_hi)
    col_ranges, layout_id, grid_meta = merge_grid_with_layout(
        grid,
        selection.layout_id,
        selection.col_ranges,
        selection.confidence,
        rows=rows,
        x_lo=x_lo,
        x_hi=x_hi,
    )
    if layout_id not in ("pillar_cc1", "pillar_cc2", "pillar_ccrf", "pillar_sec1", "pillar_dsib", "pillar_gsib"):
        col_ranges = refine_col_ranges_by_coordinates(rows, col_ranges, x_lo, x_hi)
    if len(col_ranges) < 2:
        return None

    table = build_structured_table(
        scope.page_number,
        rows,
        col_ranges,
        layout_id,
        layout_roles=selection.roles,
    )
    for i, cr in enumerate(table.grid.ranges):
        if i < len(selection.roles):
            cr.role = selection.roles[i]
    table.layout_id = layout_id
    table.grid.layout_id = layout_id
    if grid_meta:
        table.metadata.update(grid_meta)
    if grid:
        table.metadata["grid_confidence"] = grid.confidence
    table.description_text = scope.description_text
    desc_src = getattr(scope, "description_source_items", None) or []
    if desc_src:
        table.metadata["description_source_items"] = [
            it.item_index for it in desc_src
        ]
    table.metadata["layout_confidence"] = selection.confidence
    table.metadata["scope_y0"] = scope.scope_y0
    table.metadata["region_y0"] = scope.region.y0
    table.metadata["pre_header_count"] = len(scope.pre_header_items)
    table.metadata["scope_source_items"] = [
        it.item_index for it in scope.items if str(it.text or "").strip()
    ]
    if scope.metadata:
        table.metadata.update(scope.metadata)
    return table


def build_table_from_region(
    page: PageSource,
    region_index: Optional[int] = None,
) -> Optional[StructuredTable]:
    if not page.table_regions:
        return None

    if region_index is None:
        region = pick_primary_region(page)
        if region is None:
            return None
        region_index = page.table_regions.index(region)
    else:
        if region_index >= len(page.table_regions):
            return None
        region = page.table_regions[region_index]

    scope = build_table_scope(page, region, region_index)
    return build_table_from_scope(scope)


# 兼容 Step 1/2 测试
_scope_y0 = scope_y0_for_region
