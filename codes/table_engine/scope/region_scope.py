# -*- coding: utf-8
"""单表 scope：region + pre_header + 向下扩展。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Sequence, Tuple

from codes.table_engine.geometry.boundary_expand import (
    append_table_continuation_below,
    effective_y_margin_below,
    expand_scope_items,
    large_gap_above_region,
)
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.models import PageSource, RegionBox, SourceItem
from codes.table_engine.scope.header_scope import (
    consolidate_annual_pre_header_items,
    scope_y0_for_region,
)
from codes.table_engine.split.boundary_overlap import row_content_fingerprint, _row_opens_new_table_header
from codes.table_engine.split.row_classify import row_has_body_value_data

_LARGE_INTER_REGION_GAP = 45.0


def _regions_overlap_x(a: RegionBox, b: RegionBox, margin: float = 10.0) -> bool:
    return not (a.x1 < b.x0 - margin or b.x1 < a.x0 - margin)


def _floor_scope_y0_from_prev_region(
    page: PageSource,
    region: RegionBox,
    scope_y0: float,
) -> float:
    """避免后续 region scope 上扩吞入上一张已检测表的表体。"""
    prev = [r for r in page.table_regions if r.y1 < region.y0 - 5.0]
    if not prev:
        return scope_y0
    above = max(prev, key=lambda r: r.y1)
    if not _regions_overlap_x(above, region):
        return scope_y0
    gap = region.y0 - above.y1
    if gap > _LARGE_INTER_REGION_GAP:
        floor = region.y0 - 10.0
    else:
        floor = above.y1 + 30.0
    return max(scope_y0, floor)


@dataclass
class TableScope:
    """Phase A 输出：一张逻辑表的 item 集合（仍含坐标）。"""

    page_number: int
    region_index: int
    region: RegionBox
    scope_y0: float
    scope_y1: float
    items: List[SourceItem]
    pre_header_items: List[SourceItem] = field(default_factory=list)
    description_text: str = ""
    description_source_items: List[SourceItem] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def merge_items_dedup(*groups: Iterable[SourceItem]) -> List[SourceItem]:
    seen: set[str] = set()
    out: List[SourceItem] = []
    for group in groups:
        for it in group:
            if it.item_index in seen:
                continue
            seen.add(it.item_index)
            out.append(it)
    out.sort(key=lambda x: (x.y_mid, x.x0))
    return out


def dedupe_scope_row_duplicates(items: List[SourceItem]) -> List[SourceItem]:
    """scope 内相同标签+数值的行只保留首次出现（防 region 边界双写）。"""
    if len(items) < 4:
        return items
    dicts = source_items_to_dicts(items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    seen: set[Tuple[str, Tuple[str, ...]]] = set()
    keep_ids: set[str] = set()
    for row in rows:
        row_items = sorted(
            [
                index_map[d["item_index"]]
                for d in row.get("items", [])
                if d.get("item_index") in index_map
            ],
            key=lambda it: it.x0,
        )
        if not row_items:
            continue
        cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
        if not cells:
            for it in row_items:
                keep_ids.add(it.item_index)
            continue
        pseudo = [cells[0]] + [""] * 8
        for ci, it in enumerate(row_items[1:], start=1):
            if ci < len(pseudo):
                pseudo[ci] = str(it.text).strip()
        if _row_opens_new_table_header(pseudo):
            for it in row_items:
                keep_ids.add(it.item_index)
            continue
        fp = row_content_fingerprint(pseudo)
        if fp in seen:
            continue
        if not row_has_body_value_data(pseudo) and not fp[0]:
            for it in row_items:
                keep_ids.add(it.item_index)
            continue
        seen.add(fp)
        for it in row_items:
            keep_ids.add(it.item_index)
    if not keep_ids:
        return items
    return [it for it in items if it.item_index in keep_ids]


def collect_items_in_band(
    page: PageSource,
    region: RegionBox,
    *,
    scope_y0: float,
    y_margin_below: float = 30.0,
    y_margin_above: float = 10.0,
    x_margin: float = 10.0,
) -> List[SourceItem]:
    scoped: List[SourceItem] = []
    for it in page.items:
        cx, cy = it.bbox.cx, it.bbox.cy
        if (
            region.x0 - x_margin <= cx <= region.x1 + x_margin
            and scope_y0 - y_margin_above <= cy <= region.y1 + y_margin_below
        ):
            scoped.append(it)
    return scoped


_GAP_PRE_HEADER_Y_FLOOR = 8.0


def _filter_pre_header_for_region(
    page: PageSource,
    region: RegionBox,
    pre: Sequence[SourceItem],
) -> List[SourceItem]:
    """大间隙时丢弃落在上一 region 表体内的误 peel；保留间隙内已路由表头。"""
    if not pre or not large_gap_above_region(page, region):
        return list(pre)
    prev_regions = [r for r in page.table_regions if r.y1 < region.y0 - 5.0]
    if not prev_regions:
        return list(pre)
    above = max(prev_regions, key=lambda r: r.y1)
    y_floor = above.y1 + _GAP_PRE_HEADER_Y_FLOOR
    return [it for it in pre if it.bbox.y0 >= y_floor]


_HEADERLESS_GAP_REGION_BASE = 9000


def build_headerless_gap_scope(
    page: PageSource,
    body_items: Sequence[SourceItem],
    *,
    virtual_index: int = _HEADERLESS_GAP_REGION_BASE,
) -> TableScope:
    """首个 region 上方间隙中的无表头矩阵表体 → 独立 TableScope。"""
    items = merge_items_dedup(body_items)
    items = dedupe_scope_row_duplicates(items)
    if not items:
        raise ValueError("headerless gap scope requires body items")
    x0 = min(it.bbox.x0 for it in items) - 5.0
    x1 = max(it.bbox.x1 for it in items) + 5.0
    y0 = min(it.bbox.y0 for it in items)
    y1 = max(it.bbox.y1 for it in items)
    region = RegionBox(x0, y0, x1, y1, 1.0, virtual_index)
    return TableScope(
        page_number=page.page_number,
        region_index=virtual_index,
        region=region,
        scope_y0=y0,
        scope_y1=y1,
        items=items,
        metadata={"headerless_gap_table": True},
    )


def build_table_scope(
    page: PageSource,
    region: RegionBox,
    region_index: int,
    *,
    pre_header_items: Sequence[SourceItem] | None = None,
    description_text: str = "",
    description_source_items: Sequence[SourceItem] | None = None,
    y_margin_below: float | None = None,
) -> TableScope:
    pre = _filter_pre_header_for_region(page, region, list(pre_header_items or []))
    pre = consolidate_annual_pre_header_items(pre)
    desc_src = list(description_source_items or [])
    scope_y0 = scope_y0_for_region(page, region)
    if pre:
        scope_y0 = min(scope_y0, min(it.bbox.y0 for it in pre))
    scope_y0 = _floor_scope_y0_from_prev_region(page, region, scope_y0)
    if large_gap_above_region(page, region):
        scope_y0 = max(scope_y0, region.y0 - 10.0)

    margin_below = (
        y_margin_below
        if y_margin_below is not None
        else effective_y_margin_below(page, region)
    )
    margin_above = 2.0 if pre else 10.0
    band = collect_items_in_band(
        page,
        region,
        scope_y0=scope_y0,
        y_margin_below=margin_below,
        y_margin_above=margin_above,
    )
    items = merge_items_dedup(pre, band)
    items = expand_scope_items(page, region, items)
    items = append_table_continuation_below(page, region, items, margin_below)
    items = dedupe_scope_row_duplicates(items)

    scope_y1 = region.y1 + margin_below
    if items:
        scope_y1 = max(scope_y1, max(it.bbox.y1 for it in items))

    return TableScope(
        page_number=page.page_number,
        region_index=region_index,
        region=region,
        scope_y0=scope_y0,
        scope_y1=scope_y1,
        items=items,
        pre_header_items=list(pre),
        description_text=description_text,
        description_source_items=desc_src,
        metadata={
            "region_y0": region.y0,
            "region_y1": region.y1,
            "pre_header_count": len(pre),
        },
    )
