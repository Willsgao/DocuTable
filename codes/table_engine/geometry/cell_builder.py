# -*- coding: utf-8
"""RowCluster → Cell 矩阵 → StructuredTable。"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Set, Tuple

from codes.table_engine.geometry.column_anchors import (
    col_index_by_anchor,
    col_index_by_x0,
    infer_mid_label_column_x0_clusters,
    is_entity_scope_label_text,
    is_item_in_label_column_zone,
    is_label_band_item,
    is_mid_label_column_item,
    is_report_period_cell,
    is_stage_column_header_text,
    is_value_column_header_text,
)
from codes.table_engine.geometry.cell_decomposition import decompose_row_items
from codes.table_engine.geometry.cell_numeric_repair import repair_row_if_needed
from codes.table_engine.geometry.data_column_assign import (
    is_data_value_item,
    reconcile_col_items_by_anchor,
)
from codes.table_engine.geometry.column_profile import (
    assign_pillar_row_to_columns,
    infer_column_profiles,
    row_uses_pillar_assignment,
    uses_pillar_row_assignment,
)
from codes.table_engine.geometry.row_refiner import estimate_layout_anchors
from codes.table_engine.layout.column_ranges import col_index_for_item as layout_col_index
from codes.table_engine.geometry.numeric import is_numeric_data_cell, looks_like_change_reason_description_not_label
from codes.table_engine.models import BBox, Cell, ColumnGrid, ColumnRange, StructuredTable

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_DASH_VALUES = frozenset(("-", "－", "—", "–"))


def _is_label_like_text(text: str) -> bool:
    t = str(text or "").strip()
    return bool(t) and bool(_CJK_RE.search(t)) and not is_numeric_data_cell(t)


def _is_value_like_text(text: str) -> bool:
    return is_data_value_item(text)


_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.I)


def _assign_item_to_columns(
    it: dict,
    col_ranges: List[Tuple[float, float]],
    col_items: List[List[dict]],
    n_cols: int,
    layout_id: str = "",
    *,
    mid_label_x0s: Optional[Sequence[float]] = None,
) -> None:
    """按坐标锚点分列（数值看 x1，表头看 x0）；layout 插件可覆盖 CC1 等特例。"""
    text = str(it.get("text", "")).strip()
    x0 = float(it.get("x0", 0))
    x1 = float(it.get("x1", 0))
    if (
        n_cols >= 4
        and looks_like_change_reason_description_not_label(text)
        and not is_item_in_label_column_zone(it, col_ranges)
    ):
        col_items[n_cols - 1].append(it)
        return
    if col_ranges and text and _ROW_NUMBER_RE.match(text) and x0 <= col_ranges[0][1] + 10:
        ci = 0
    elif (
        text == "项目"
        and _is_label_like_text(text)
        and col_ranges
        and is_item_in_label_column_zone(it, col_ranges)
    ):
        ci = 0
    elif (
        _is_label_like_text(text)
        and not _is_value_like_text(text)
        and not is_mid_label_column_item(it, mid_label_x0s)
        and col_ranges
        and is_item_in_label_column_zone(it, col_ranges)
        and not is_report_period_cell(text)
        and not is_value_column_header_text(
            text, x0=x0, mid_label_x0s=mid_label_x0s,
        )
    ):
        ci = 0
    elif is_value_column_header_text(
        text, x0=x0, mid_label_x0s=mid_label_x0s,
    ):
        # 账面余额/占比等：按中心落列，禁止挤进项目列
        mid = (x0 + x1) / 2.0 if x1 > x0 else x0
        from codes.table_engine.geometry.column_anchors import col_index_by_x0
        ci = col_index_by_x0(mid, col_ranges)
        if ci <= 0 and n_cols > 1:
            ci = 1
    elif layout_id and uses_pillar_row_assignment(layout_id):
        ci = layout_col_index(x0, x1, text, col_ranges, layout_id)
    elif _is_value_like_text(text) and not _ROW_NUMBER_RE.match(text):
        from codes.table_engine.geometry.column_anchors import col_index_by_x1
        ci = col_index_by_x1(x1, col_ranges)
    elif is_report_period_cell(text) and n_cols >= 2:
        data_col_start = float(col_ranges[1][0])
        if x0 >= float(col_ranges[0][0]) + 20 and x0 < data_col_start + 25:
            ci = 1
        else:
            ci = col_index_by_anchor(
                x0, x1, text, col_ranges, mid_label_x0s=mid_label_x0s,
            )
    else:
        ci = col_index_by_anchor(
            x0, x1, text, col_ranges, mid_label_x0s=mid_label_x0s,
        )
    if not (0 <= ci < n_cols):
        return

    if _is_label_like_text(text):
        if (
            is_stage_column_header_text(text)
            or is_entity_scope_label_text(text)
            or is_label_band_item(
                {"text": text, "x0": x0, "x1": x1},
                mid_label_x0s=mid_label_x0s,
            )
            or is_value_column_header_text(
                text, x0=x0, mid_label_x0s=mid_label_x0s,
            )
            or is_report_period_cell(text)
        ):
            col_items[ci].append(it)
            return
        if not col_items[ci] or all(_is_label_like_text(str(c.get("text", ""))) for c in col_items[ci]):
            col_items[ci].append(it)
            return
        if ci > 0 and (
            not col_items[ci - 1]
            or all(_is_label_like_text(str(c.get("text", ""))) for c in col_items[ci - 1])
        ):
            col_items[ci - 1].append(it)
            return

    if _is_value_like_text(text):
        if col_items[ci] and all(_is_label_like_text(str(c.get("text", ""))) for c in col_items[ci]):
            if ci + 1 < n_cols:
                col_items[ci + 1].append(it)
                return

    col_items[ci].append(it)


def _source_ids_from_row_items(items: List[dict]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for it in items:
        for sid in it.get("_source_item_ids") or []:
            s = str(sid)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        idx = str(it.get("item_index", ""))
        if idx and idx not in seen:
            seen.add(idx)
            out.append(idx)
    return out


_INDENT_STEP_PT = 12.0
_INDENT_THRESHOLD_PT = 5.0
_INDENT_SPACES_PER_LEVEL = 2


def _label_baseline_x0_from_rows(rows: List[dict]) -> float:
    xs: List[float] = []
    for row in rows:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if not t or not _is_label_like_text(t):
                continue
            if is_numeric_data_cell(t):
                continue
            xs.append(float(it.get("x0", 0)))
    return min(xs) if xs else 0.0


def _indent_level_for_item(x0: float, baseline_x0: float) -> int:
    delta = max(0.0, float(x0) - baseline_x0)
    if delta < _INDENT_THRESHOLD_PT:
        return 0
    return min(4, max(1, int(round(delta / _INDENT_STEP_PT))))


def _format_label_cell_text(items: List[dict], baseline_x0: float) -> str:
    raw = _cell_text_from_items(items)
    if not raw or not items:
        return raw
    x0 = min(float(it.get("x0", 0)) for it in items)
    level = _indent_level_for_item(x0, baseline_x0)
    if level <= 0:
        return raw
    return (" " * (_INDENT_SPACES_PER_LEVEL * level)) + raw


def _cell_text_from_items(items: List[dict]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return str(items[0].get("text", "")).strip()
    # 同行微抖动（地区/金额 y 差 1～2pt）优先按 x0，避免拼成「金额 地区」
    ys = [float(it.get("y0", 0)) for it in items]
    y_span = (max(ys) - min(ys)) if ys else 0.0
    if y_span <= 4.0:
        ordered = sorted(items, key=lambda it: float(it.get("x0", 0)))
    else:
        ordered = sorted(
            items,
            key=lambda it: (float(it.get("y0", 0)), float(it.get("x0", 0))),
        )
    texts: List[str] = []
    seen: Set[str] = set()
    for it in ordered:
        t = str(it.get("text", "")).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        texts.append(t)
    if len(texts) == 1:
        return texts[0]
    return " ".join(texts)


_YEAR_ONLY_HEADER_RE = re.compile(r"^(?:19|20)\d{2}\s*年$")
_SHORT_VALUE_COL_HEADER = frozenset({
    "占比", "比重", "比例", "数额", "代码", "金额", "期数",
    "变化原因", "主要原因", "账面余额", "账面价值", "公允价值",
})


def _is_annual_header_value_column_text(text: str) -> bool:
    """年报表头数值区列标：报告期、增减列、占比/变化原因列等。"""
    from codes.table_engine.geometry.numeric import is_report_date_header_part_text

    t = str(text or "").strip()
    if not t or t == "项目":
        return False
    if t in _SHORT_VALUE_COL_HEADER:
        return True
    if "变化原因" in t or "主要原因" in t:
        return True
    if is_report_period_cell(t) or is_report_date_header_part_text(t):
        return True
    if "比上年同期" in t or "比上年度" in t:
        return True
    if t in ("增减", "末增减", "期增减"):
        return True
    return False


def _annual_header_value_items(items: List[dict]) -> List[dict]:
    """表头带数值区 item（报告期 + 增减列），保留 x 顺序。"""
    picked: List[dict] = []
    seen: set[str] = set()
    for it in sorted(items, key=lambda d: float(d.get("x0", 0))):
        t = str(it.get("text", "")).strip()
        if not t or t in seen:
            continue
        if not _is_annual_header_value_column_text(t):
            continue
        if is_report_period_cell(t):
            picked.append(it)
            seen.add(t)
    filtered = _filter_period_header_items(picked) if picked else []
    seen = {str(it.get("text", "")).strip() for it in filtered}
    for it in sorted(items, key=lambda d: float(d.get("x0", 0))):
        t = str(it.get("text", "")).strip()
        if not t or t in seen:
            continue
        if _is_annual_header_value_column_text(t) and not is_report_period_cell(t):
            filtered.append(it)
            seen.add(t)
    return sorted(filtered, key=lambda it: float(it.get("x0", 0)))


def _filter_period_header_items(items: List[dict]) -> List[dict]:
    """表头带：有完整日期列时去掉误落的短「YYYY年」碎片。"""
    periods = [
        it for it in items
        if is_report_period_cell(str(it.get("text", "")).strip())
    ]
    if len(periods) < 2:
        return periods
    has_long = any(
        re.search(r"\d{1,2}\s*月", str(it.get("text", "")))
        for it in periods
    )
    if not has_long:
        return periods
    return [
        it for it in periods
        if re.search(r"\d{1,2}\s*月", str(it.get("text", "")))
        or not _YEAR_ONLY_HEADER_RE.match(str(it.get("text", "")).strip())
    ]


def _try_assign_annual_header_band_row(
    row_items: List[dict],
    col_items: List[List[dict]],
    n_cols: int,
    col_ranges: List[Tuple[float, float]],
) -> bool:
    """年报表头带：项目固定 col0，报告期/增减/变化原因按 x0 落列。

    先拆 OCR 粘连列表头，再按坐标落格——禁止把多列表头塞进同一格。
    """
    if n_cols < 2 or not row_items:
        return False
    from codes.table_engine.geometry.cell_numeric_repair import (
        expand_compound_report_date_header_row_items,
    )
    from codes.table_engine.scope.header_scope import is_annual_report_column_header_row

    # 即使传入 raw，也先按列界拆开粘连表头（如「2023年 增减幅度 变化原因」）
    expanded = expand_compound_report_date_header_row_items(row_items, col_ranges)
    ordered = sorted(expanded, key=lambda it: float(it.get("x0", 0)))
    cells = [str(it.get("text", "")).strip() for it in ordered if str(it.get("text", "")).strip()]
    if not cells:
        return False

    if not is_annual_report_column_header_row(cells):
        return False

    project_it = next(
        (
            it for it in ordered
            if str(it.get("text", "")).strip() == "项目"
            or str(it.get("text", "")).strip().startswith("项目")
        ),
        None,
    )
    period_items = _annual_header_value_items(ordered)
    if project_it is None and len(period_items) < 2:
        return False
    if project_it is not None:
        col_items[0].append(project_it)

    date_items = [
        it for it in period_items
        if is_report_period_cell(str(it.get("text", "")).strip())
    ]
    metric_items = [it for it in period_items if it not in date_items]

    def _assign_dates_sequential(dates: List[dict]) -> None:
        """多报告期按 x 序落入连续列，避免拆段后仍挤同一列。"""
        max_date_ci = n_cols - 2 if n_cols >= 5 else n_cols - 1
        if metric_items and n_cols >= 5:
            max_date_ci = n_cols - 3
        for i, pit in enumerate(dates):
            ci = min(1 + i, max(1, max_date_ci))
            col_items[ci].append(pit)

    def _assign_metrics() -> None:
        for pit in metric_items:
            text = str(pit.get("text", "")).strip()
            if "原因" in text and n_cols >= 4:
                ci = n_cols - 1
            elif ("增减" in text or "幅度" in text) and n_cols >= 5:
                ci = n_cols - 2
            else:
                x0 = float(pit.get("x0", 0))
                x1 = float(pit.get("x1", 0))
                ci = col_index_by_anchor(x0, x1, text, col_ranges)
                if ci <= 0:
                    ci = min(1 + len(date_items), n_cols - 1)
            if 0 <= ci < n_cols:
                col_items[ci].append(pit)

    if len(date_items) >= 2 and n_cols >= 4:
        # 先按锚点试分；若两日期撞列（种子均分 x 常见），再顺序重排
        trial: List[List[dict]] = [[] for _ in range(n_cols)]
        for pit in date_items:
            text = str(pit.get("text", "")).strip()
            x0 = float(pit.get("x0", 0))
            x1 = float(pit.get("x1", 0))
            ci = col_index_by_anchor(x0, x1, text, col_ranges)
            if ci <= 0:
                ci = 1
            ci = min(max(ci, 1), n_cols - 1)
            trial[ci].append(pit)
        collided = any(len(bucket) >= 2 for bucket in trial)
        seeded = any(
            "#g" in str(it.get("item_index", "")) or "#h" in str(it.get("item_index", ""))
            for it in date_items
        )
        if collided or (seeded and len(date_items) == 2):
            _assign_dates_sequential(date_items)
        else:
            for ci, bucket in enumerate(trial):
                col_items[ci].extend(bucket)
        _assign_metrics()
    else:
        for pit in period_items:
            text = str(pit.get("text", "")).strip()
            x0 = float(pit.get("x0", 0))
            x1 = float(pit.get("x1", 0))
            ci = col_index_by_anchor(x0, x1, text, col_ranges)
            if ci <= 0 and n_cols > 1:
                ci = 1
            if 0 <= ci < n_cols:
                col_items[ci].append(pit)

    assigned_ids = {
        str(it.get("item_index", ""))
        for bucket in col_items
        for it in bucket
        if str(it.get("item_index", ""))
    }
    assigned_texts = {
        str(it.get("text", "")).strip()
        for bucket in col_items
        for it in bucket
        if str(it.get("text", "")).strip()
    }
    # 占比允许两列重复；其余拆段文本若已落格则跳过
    allow_dup = frozenset({"占比", "比重", "比例", "12 月 31 日"})
    for it in ordered:
        iid = str(it.get("item_index", ""))
        text = str(it.get("text", "")).strip()
        if not text:
            continue
        if iid and iid in assigned_ids:
            continue
        if text in assigned_texts and text not in allow_dup:
            continue
        x0 = float(it.get("x0", 0))
        x1 = float(it.get("x1", 0))
        ci = col_index_by_anchor(x0, x1, text, col_ranges)
        if "原因" in text and n_cols >= 4:
            ci = n_cols - 1
        elif ("增减" in text or text.endswith("幅度")) and n_cols >= 5:
            ci = n_cols - 2
        if 0 <= ci < n_cols:
            col_items[ci].append(it)
            if iid:
                assigned_ids.add(iid)
            assigned_texts.add(text)
    return True


def assign_rows_to_columns(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
    layout_id: str,
    page: int,
    *,
    layout_roles: Sequence[str] | None = None,
) -> List[List[Optional[Cell]]]:
    n_cols = len(col_ranges)
    matrix: List[List[Optional[Cell]]] = []

    pillar_mode = uses_pillar_row_assignment(layout_id)
    anchors = estimate_layout_anchors(rows) if pillar_mode else None
    mid_label_x0s = infer_mid_label_column_x0_clusters(rows) if not pillar_mode else []
    label_baseline_x0 = _label_baseline_x0_from_rows(rows) if not pillar_mode else 0.0
    profiles = (
        infer_column_profiles(
            rows, col_ranges, layout_id, anchors, layout_roles=layout_roles,
        )
        if pillar_mode and anchors
        else None
    )

    for row_idx, row in enumerate(rows):
        row_items = row.get("items") or []

        value_cols: Optional[List[int]] = None
        if pillar_mode and profiles:
            value_cols = [p.col_index for p in profiles if p.role == "value"]
            if not value_cols and anchors:
                value_cols = [
                    i for i, (lo, _) in enumerate(col_ranges)
                    if lo >= anchors.value_x_min - 25
                ]

        raw_for_header = list(row_items)
        row_items = decompose_row_items(
            row_items,
            col_ranges,
            row_idx=row_idx,
            row_phase=str(row.get("row_phase") or ""),
            value_cols=value_cols,
            include_merged_numeric=(
                row.get("row_phase") == "body"
                or (
                    pillar_mode
                    and anchors
                    and row_uses_pillar_assignment(row, anchors)
                )
            ),
        )

        col_cells: List[Optional[Cell]] = [None] * n_cols

        if pillar_mode and row.get("row_phase") == "header" and layout_id == "pillar_ccrf":
            col_items = [[] for _ in range(n_cols)]
            for it in sorted(row_items, key=lambda d: float(d.get("x0", 0))):
                text = str(it.get("text", "")).strip()
                x0 = float(it.get("x0", 0))
                x1 = float(it.get("x1", 0))
                ci = layout_col_index(x0, x1, text, col_ranges, layout_id)
                if 0 <= ci < n_cols:
                    col_items[ci].append(it)
        elif layout_id == "pillar_dsib":
            col_items = [[] for _ in range(n_cols)]
            for it in sorted(row_items, key=lambda d: float(d.get("x0", 0))):
                text = str(it.get("text", "")).strip()
                if not text:
                    continue
                x0 = float(it.get("x0", 0))
                x1 = float(it.get("x1", 0))
                ci = layout_col_index(x0, x1, text, col_ranges, layout_id)
                if 0 <= ci < n_cols:
                    col_items[ci].append(it)
        elif pillar_mode and anchors and profiles and row_uses_pillar_assignment(row, anchors):
            col_items = assign_pillar_row_to_columns(
                row_items, col_ranges, anchors, layout_id, profiles,
            )
        else:
            col_items = [[] for _ in range(n_cols)]
            if not _try_assign_annual_header_band_row(
                raw_for_header, col_items, n_cols, col_ranges,
            ):
                for it in sorted(row_items, key=lambda d: float(d.get("x0", 0))):
                    _assign_item_to_columns(
                        it, col_ranges, col_items, n_cols, layout_id,
                        mid_label_x0s=mid_label_x0s,
                    )

        is_body_row = (
            row.get("row_phase") == "body"
            or (
                pillar_mode
                and anchors
                and row_uses_pillar_assignment(row, anchors)
            )
        )
        if is_body_row:
            col_items = reconcile_col_items_by_anchor(
                col_items,
                col_ranges,
                layout_id=layout_id,
                value_cols=value_cols,
            )

        col_items, col_cells, _repaired = repair_row_if_needed(
            row,
            row_idx,
            col_items,
            col_ranges,
            n_cols,
            layout_id=layout_id,
            value_cols=value_cols,
            cell_text_fn=_cell_text_from_items,
            assign_label_fn=lambda it, cr, ci, nc, lid="": _assign_item_to_columns(
                it, cr, ci, nc, lid, mid_label_x0s=mid_label_x0s,
            ),
        )

        if not _repaired:
            col_cells = [None] * n_cols
            for ci, items in enumerate(col_items):
                if not items:
                    continue
                if ci == 0 and not pillar_mode:
                    text = _format_label_cell_text(items, label_baseline_x0)
                else:
                    text = _cell_text_from_items(items)
                x0 = min(float(it.get("x0", 0)) for it in items)
                y0 = min(float(it.get("y0", 0)) for it in items)
                x1 = max(float(it.get("x1", 0)) for it in items)
                y1 = max(float(it.get("y1", 0)) for it in items)
                src = _source_ids_from_row_items(items)
                col_cells[ci] = Cell(
                    text=text,
                    bbox=BBox(x0, y0, x1, y1),
                    row=row_idx,
                    col=ci,
                    source_items=src,
                )

        matrix.append(col_cells)

    return matrix


def build_structured_table(
    page: int,
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
    layout_id: str,
    *,
    layout_roles: Sequence[str] | None = None,
) -> StructuredTable:
    matrix = assign_rows_to_columns(
        rows, col_ranges, layout_id, page, layout_roles=layout_roles,
    )
    grid_ranges = [
        ColumnRange(x0=a, x1=b, col_index=i, role="")
        for i, (a, b) in enumerate(col_ranges)
    ]
    all_cells = [c for row in matrix for c in row if c is not None]
    if all_cells:
        y0 = min(c.bbox.y0 for c in all_cells)
        y1 = max(c.bbox.y1 for c in all_cells)
        x0 = min(c.bbox.x0 for c in all_cells)
        x1 = max(c.bbox.x1 for c in all_cells)
    else:
        y0 = y1 = x0 = x1 = 0.0

    return StructuredTable(
        page=page,
        pages=[page],
        y0=y0,
        y1=y1,
        x0=x0,
        x1=x1,
        rows=matrix,
        grid=ColumnGrid(ranges=grid_ranges, layout_id=layout_id, confidence=1.0),
        layout_id=layout_id,
    )
