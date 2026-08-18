# -*- coding: utf-8
"""表头完整性：识别表后若首行缺表头带，向上回溯 gap 内遗漏行并回补。"""

from __future__ import annotations

import copy
from typing import List, Optional, Sequence, Set, Tuple

from codes.table_engine.geometry.cell_builder import build_structured_table
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.models import PageSource, RegionBox, SourceItem, StructuredTable
from codes.table_engine.scope.region_scope import TableScope, dedupe_scope_row_duplicates, merge_items_dedup
from codes.table_engine.scope.header_scope import (
    has_annual_column_header_band,
    is_annual_report_column_header_row,
    is_annual_report_unit_row,
    is_pre_table_header_band_row,
    is_single_year_label_row,
    is_stats_column_header_row,
    row_is_footnote_prose_row,
)
from codes.table_engine.split.boundary_overlap import row_content_fingerprint
from codes.table_engine.split.row_classify import (
    is_inter_table_narrative_row,
    is_prependable_header_band_row,
    row_has_body_value_data,
    row_is_intra_table_label_row,
)
from codes.table_engine.split.trailing_header_reattach import (
    _LowerHeaderState,
    _header_row_redundant_in_lower,
    _scan_lower_header_state,
)
from codes.table_engine.table_access import dense_rows

_MAX_UPWARD_SCAN_PT = 220.0
_MAX_HEADER_ROWS_UPWARD = 6


def _row_cells_from_items(row_items: Sequence[SourceItem]) -> List[str]:
    return [str(it.text).strip() for it in row_items if str(it.text).strip()]


def _cluster_item_rows(items: Sequence[SourceItem]) -> List[List[SourceItem]]:
    if not items:
        return []
    dicts = source_items_to_dicts(list(items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    groups: List[List[SourceItem]] = []
    for row in rows:
        row_items = [
            index_map[d["item_index"]]
            for d in row.get("items", [])
            if d.get("item_index") in index_map
        ]
        if row_items:
            groups.append(sorted(row_items, key=lambda it: it.x0))
    return groups


def _row_text_matrix(items: Sequence[SourceItem]) -> List[List[str]]:
    return [_row_cells_from_items(g) for g in _cluster_item_rows(items)]


def _gap_row_is_header_candidate(row_items: Sequence[SourceItem]) -> bool:
    from codes.table_engine.scope.header_scope import is_annual_header_wrap_subrow

    cells = _row_cells_from_items(row_items)
    if not cells:
        return False
    if is_inter_table_narrative_row(cells):
        return False
    if is_annual_header_wrap_subrow(row_items):
        return False
    if is_prependable_header_band_row(cells):
        return True
    if is_annual_report_unit_row(cells):
        return True
    if is_annual_report_column_header_row(cells):
        return True
    return is_pre_table_header_band_row(row_items)


# 非页顶 region（y0 超过此阈值）识别为表后，必须有列标表头带（项目+报告期等）
_PAGE_TOP_HEADERLESS_MAX_REGION_Y0 = 200.0


def scope_expects_header_band(page: PageSource, scope: TableScope) -> bool:
    """识别为表格后是否必须有表头：页顶无表头矩阵除外。"""
    if scope.metadata.get("headerless_gap_table"):
        return False
    return scope.region.y0 >= _PAGE_TOP_HEADERLESS_MAX_REGION_Y0


def table_header_band_present(
    rows: List[List[str]],
    *,
    scan: int = 12,
) -> bool:
    """表头带是否完整：列标 +（单位或日期或主体列）。"""
    if not rows:
        return False
    if has_annual_column_header_band(rows, scan=scan):
        return True
    st = _scan_lower_header_state(rows[: min(scan, len(rows))])
    if st.has_entity_scope and (st.has_date or st.has_column_header):
        return True
    if st.has_column_header and (st.has_date or st.has_unit):
        return True
    return False


def table_header_band_incomplete(
    rows: List[List[str]],
    *,
    state: Optional[_LowerHeaderState] = None,
    expects_header: bool = True,
) -> bool:
    """首行或表头带缺单位/列标/日期，且首条数据行已出现。"""
    if not rows:
        return bool(expects_header)
    if table_header_band_present(rows):
        return False
    if not expects_header:
        return False
    st = state or _scan_lower_header_state(rows[: min(10, len(rows))])
    if row_has_body_value_data(rows[0]):
        return True
    if st.first_body_row < len(rows) and row_has_body_value_data(rows[st.first_body_row]):
        return True
    return True


def _collect_items_above_region(
    page: PageSource,
    region: RegionBox,
    *,
    y_hi: float,
    y_lo: float,
    exclude_ids: Set[str],
) -> List[SourceItem]:
    x_lo = region.x0 - 12.0
    x_hi = region.x1 + 12.0
    out: List[SourceItem] = []
    for it in page.items:
        if it.item_index in exclude_ids:
            continue
        if not str(it.text or "").strip():
            continue
        if it.bbox.y0 > y_hi or it.bbox.y1 < y_lo:
            continue
        if it.bbox.x1 < x_lo or it.bbox.x0 > x_hi:
            continue
        out.append(it)
    return sorted(out, key=lambda it: (it.y_mid, it.x0))


def peel_missing_header_items_above(
    page: PageSource,
    region: RegionBox,
    *,
    y_hi: float,
    scope_item_ids: Set[str],
    header_state: _LowerHeaderState,
    max_rows: int = _MAX_HEADER_ROWS_UPWARD,
) -> List[SourceItem]:
    """自 y_hi 向上扫描，剥离可并入当前表的表头行 source items。"""
    y_lo = max(0.0, y_hi - _MAX_UPWARD_SCAN_PT)
    candidates = _collect_items_above_region(
        page,
        region,
        y_hi=y_hi,
        y_lo=y_lo,
        exclude_ids=scope_item_ids,
    )
    if not candidates:
        return []

    row_groups = _cluster_item_rows(candidates)
    if not row_groups:
        return []

    state = copy.copy(header_state)
    peeled_rows: List[List[SourceItem]] = []
    for row_items in reversed(row_groups):
        if len(peeled_rows) >= max_rows:
            break
        cells = _row_cells_from_items(row_items)
        if is_inter_table_narrative_row(cells):
            break
        from codes.table_engine.scope.header_scope import is_annual_header_wrap_subrow
        if is_annual_header_wrap_subrow(row_items):
            if not any(
                is_annual_report_column_header_row(_row_cells_from_items(r))
                for r in peeled_rows
            ):
                break
            peeled_rows.insert(0, list(row_items))
            continue
        if not _gap_row_is_header_candidate(row_items):
            break
        padded = cells + [""] * max(0, 4 - len(cells))
        if _header_row_redundant_in_lower(padded, state):
            continue
        peeled_rows.insert(0, list(row_items))
        if is_annual_report_unit_row(cells):
            state.has_unit = True
        if is_annual_report_column_header_row(cells):
            state.has_column_header = True
        if is_prependable_header_band_row(cells):
            state.has_column_header = True

    if not peeled_rows:
        return []

    out: List[SourceItem] = []
    for group in peeled_rows:
        out.extend(group)
    return out


def supplement_scope_missing_headers(page: PageSource, scope: TableScope) -> TableScope:
    """建表前：非页顶表必须有表头带；缺则向上搜遗漏表头并入 scope。"""
    if scope.metadata.get("headerless_gap_table"):
        return scope
    rows = _row_text_matrix(scope.items)
    if not rows:
        return scope

    expects = scope_expects_header_band(page, scope)
    state = _scan_lower_header_state(rows[: min(10, len(rows))])
    if not table_header_band_incomplete(rows, state=state, expects_header=expects):
        return scope

    scope_ids = {it.item_index for it in scope.items}
    if scope.items:
        y_hi = min(it.bbox.y0 for it in scope.items) - 2.0
    else:
        y_hi = scope.region.y0 - 2.0

    found = peel_missing_header_items_above(
        page,
        scope.region,
        y_hi=y_hi,
        scope_item_ids=scope_ids,
        header_state=state,
    )
    if not found:
        return scope

    scope.pre_header_items = merge_items_dedup(found, list(scope.pre_header_items))
    scope.items = merge_items_dedup(found, scope.items)
    scope.scope_y0 = min(scope.scope_y0, min(it.bbox.y0 for it in found))
    scope.metadata["header_supplemented"] = True
    return scope


_MAX_BODY_UPWARD_SCAN_PT = 220.0
_MAX_BODY_DOWNWARD_SCAN_PT = 120.0
_LARGE_INTER_REGION_GAP = 45.0


def _row_items_ok_to_prepend_body(row_items: List[SourceItem]) -> bool:
    from codes.table_engine.scope.gap_capture import _gap_row_is_scope_table_body_row

    cells = _row_cells_from_items(row_items)
    if _gap_row_is_scope_table_body_row(row_items):
        return True
    if row_is_intra_table_label_row(cells):
        return True
    return False


def _collect_items_below_region(
    page: PageSource,
    region: RegionBox,
    *,
    y_lo: float,
    y_hi: float,
    exclude_ids: Set[str],
) -> List[SourceItem]:
    x_lo = region.x0 - 12.0
    x_hi = region.x1 + 12.0
    out: List[SourceItem] = []
    for it in page.items:
        if it.item_index in exclude_ids:
            continue
        if not str(it.text or "").strip():
            continue
        if it.bbox.y0 > y_hi or it.bbox.y1 < y_lo:
            continue
        if it.bbox.x1 < x_lo or it.bbox.x0 > x_hi:
            continue
        out.append(it)
    return sorted(out, key=lambda it: (it.y_mid, it.x0))


def supplement_scope_missing_body_below(page: PageSource, scope: TableScope) -> TableScope:
    """邻表大间隙中落在上一表 scope 外的表尾数据行向下回补（如变化原因表末行）。"""
    from codes.table_engine.geometry.boundary_expand import (
        _next_overlapping_region_y0,
        _row_is_section_break_items,
    )
    from codes.table_engine.scope.gap_capture import (
        _gap_row_is_scope_table_body_row,
        _gap_row_is_subsection_heading,
    )

    if scope.metadata.get("trailing_gap_table") or scope.metadata.get("headerless_gap_table"):
        return scope

    next_y0 = _next_overlapping_region_y0(page, scope.region)
    if next_y0 is None:
        return scope
    gap = next_y0 - scope.region.y1
    if gap <= _LARGE_INTER_REGION_GAP:
        return scope

    scope_ids = {it.item_index for it in scope.items}
    y_lo = scope.region.y1 + 8.0
    y_hi = min(next_y0 - 10.0, y_lo + _MAX_BODY_DOWNWARD_SCAN_PT)
    candidates = _collect_items_below_region(
        page,
        scope.region,
        y_lo=y_lo,
        y_hi=y_hi,
        exclude_ids=scope_ids,
    )
    if not candidates:
        return scope

    existing_fps: set[Tuple[str, Tuple[str, ...]]] = set()
    for row_items in _cluster_item_rows(scope.items):
        existing_fps.add(row_content_fingerprint(_row_cells_from_items(row_items)))

    extra: List[SourceItem] = []
    for row_items in _cluster_item_rows(candidates):
        if _row_is_section_break_items(row_items):
            break
        if _gap_row_is_subsection_heading(row_items):
            break
        if is_inter_table_narrative_row(_row_cells_from_items(row_items)):
            break
        if is_pre_table_header_band_row(row_items):
            break
        if not _gap_row_is_scope_table_body_row(row_items):
            break
        fp = row_content_fingerprint(_row_cells_from_items(row_items))
        if fp in existing_fps:
            continue
        extra.extend(row_items)

    if not extra:
        return scope

    scope.items = dedupe_scope_row_duplicates(merge_items_dedup(scope.items, extra))
    scope.items.sort(key=lambda it: (it.bbox.y0, it.bbox.x0))
    scope.scope_y1 = max(scope.scope_y1, max(it.bbox.y1 for it in extra))
    scope.metadata["body_supplemented_below"] = True
    return scope


def supplement_scope_missing_body_above(page: PageSource, scope: TableScope) -> TableScope:
    """region 顶边落在表体中时，向上回补同表数据行（如利息表缺「债券及其他投资」）。"""
    from codes.table_engine.geometry.boundary_expand import _row_is_section_break_items
    from codes.table_engine.scope.gap_capture import _gap_row_is_scope_table_body_row

    if scope.metadata.get("headerless_gap_table"):
        return scope
    if not scope.items:
        return scope

    dicts = source_items_to_dicts(scope.items)
    clustered = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    if clustered:
        index_map = {it.item_index: it for it in scope.items}
        first_row_items = [
            index_map[d["item_index"]]
            for d in clustered[0].get("items", [])
            if d.get("item_index") in index_map
        ]
        if first_row_items:
            cells = _row_cells_from_items(first_row_items)
            # 表顶已是年头/列头：完整表起始，禁止向上吞邻表表体
            if (
                is_single_year_label_row(cells)
                or is_stats_column_header_row(cells)
                or is_annual_report_column_header_row(cells)
                or is_pre_table_header_band_row(first_row_items)
            ):
                return scope
            if not _gap_row_is_scope_table_body_row(first_row_items):
                return scope

    min_y = min(it.bbox.y0 for it in scope.items)
    y_hi = min_y - 1.0
    y_lo = max(0.0, y_hi - _MAX_BODY_UPWARD_SCAN_PT)
    # 不得越过上一 liteparse region（紧邻两年表）
    prev_y1 = [
        r.y1 for r in page.table_regions
        if r.y1 < float(scope.region.y0) - 1.0
    ]
    if prev_y1:
        y_lo = max(y_lo, max(prev_y1) + 1.0)
    if y_lo >= y_hi:
        return scope
    scope_ids = {it.item_index for it in scope.items}
    candidates = _collect_items_above_region(
        page,
        scope.region,
        y_hi=y_hi,
        y_lo=y_lo,
        exclude_ids=scope_ids,
    )
    if not candidates:
        return scope

    row_groups = _cluster_item_rows(candidates)
    peeled: List[SourceItem] = []
    for row_items in reversed(row_groups):
        if _row_is_section_break_items(row_items):
            break
        if is_inter_table_narrative_row(_row_cells_from_items(row_items)):
            break
        if is_pre_table_header_band_row(row_items):
            break
        if not _row_items_ok_to_prepend_body(row_items):
            break
        peeled[0:0] = list(row_items)

    if not peeled:
        return scope

    scope.items = merge_items_dedup(peeled, scope.items)
    scope.scope_y0 = min(scope.scope_y0, min(it.bbox.y0 for it in peeled))
    scope.metadata["body_supplemented_above"] = True
    return scope


def supplement_scope_missing_intra_label_rows(
    page: PageSource,
    scope: TableScope,
) -> TableScope:
    """表体带内仅标签的小节行（如利息支出）未进 scope 时补挂。"""
    if scope.metadata.get("trailing_gap_table") or scope.metadata.get("headerless_gap_table"):
        return scope
    if not scope.items:
        return scope

    scope_ids = {it.item_index for it in scope.items}
    y0 = float(scope.scope_y0) - 2.0
    y1 = float(scope.scope_y1) + 2.0
    x_lo = scope.region.x0 - 12.0
    x_hi = scope.region.x1 + 12.0
    band_items = [
        it
        for it in page.items
        if it.item_index not in scope_ids
        and y0 <= it.bbox.y0 <= y1
        and x_lo <= it.bbox.cx <= x_hi
        and str(it.text or "").strip()
    ]
    if not band_items:
        return scope

    existing_fps: set[Tuple[str, Tuple[str, ...]]] = set()
    for row_items in _cluster_item_rows(scope.items):
        existing_fps.add(row_content_fingerprint(_row_cells_from_items(row_items)))

    extra: List[SourceItem] = []
    for row_items in _cluster_item_rows(band_items):
        cells = _row_cells_from_items(row_items)
        # 表格下方的脚注可能落在 region 下沿附近，但不能作为表内小节补回。
        if row_is_footnote_prose_row(row_items):
            continue
        if not row_is_intra_table_label_row(cells):
            continue
        if row_content_fingerprint(cells) in existing_fps:
            continue
        if any(
            is_numeric_data_cell(str(it.text).strip()) and it.bbox.x0 > 160
            for it in row_items
            if str(it.text).strip()
        ):
            continue
        extra.extend(row_items)

    if not extra:
        return scope

    scope.items = dedupe_scope_row_duplicates(merge_items_dedup(scope.items, extra))
    scope.items.sort(key=lambda it: (it.bbox.y0, it.bbox.x0))
    scope.metadata["intra_label_supplemented"] = True
    return scope


def prepend_source_items_to_table(
    table: StructuredTable,
    header_items: Sequence[SourceItem],
) -> StructuredTable:
    """用既有列网格把 gap 表头 items 建成行并 prepend 到 StructuredTable。"""
    if not header_items or not table.grid.ranges:
        return table

    dicts = source_items_to_dicts(list(header_items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    if not rows:
        return table

    col_ranges = [(r.x0, r.x1) for r in table.grid.ranges]
    roles = [r.role for r in table.grid.ranges]
    mini = build_structured_table(
        table.page,
        rows,
        col_ranges,
        table.layout_id or "generic",
        layout_roles=roles,
    )
    if not mini.rows:
        return table

    out = copy.copy(table)
    out.rows = copy.deepcopy(mini.rows) + copy.deepcopy(table.rows)
    for ri, row in enumerate(out.rows):
        for cell in row:
            if cell is not None:
                cell.row = ri
    all_cells = [c for row in out.rows for c in row if c is not None]
    if all_cells:
        out.y0 = min(c.bbox.y0 for c in all_cells)
        out.y1 = max(c.bbox.y1 for c in all_cells)
        out.x0 = min(c.bbox.x0 for c in all_cells)
        out.x1 = max(c.bbox.x1 for c in all_cells)
    out.metadata["header_supplemented"] = True
    return out


def scan_table_header_incomplete(
    table: StructuredTable,
    *,
    expects_header: Optional[bool] = None,
) -> bool:
    rows = dense_rows(table)
    if not rows:
        return bool(expects_header)
    if expects_header is None:
        expects_header = not bool(table.metadata.get("headerless_gap_table"))
        if expects_header:
            ry0 = float(table.metadata.get("region_y0", table.y0))
            if ry0 < _PAGE_TOP_HEADERLESS_MAX_REGION_Y0:
                expects_header = False
    return table_header_band_incomplete(rows, expects_header=expects_header)
