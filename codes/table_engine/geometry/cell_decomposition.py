# -*- coding: utf-8 -*-
"""统一单元格分解：检测「多语义字段粘在同一格/item」并按列角色拆开。

原则（适用于所有类似问题）：
1. 单格只允许一种字段语义（标签 / 金额 / 百分比 / 说明 / 表头片段 / …）
2. 检测 → 拆段 → 按列角色落位（数值看 x1，文本看 x0）
3. 列数不足时先扩网格，再落位
4. 守恒阶段不得把已拆开的片段重新粘回同一格
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Literal, Optional, Sequence, Tuple

from codes.table_engine.geometry.numeric import (
    has_percent_glued_to_chinese_text,
    is_change_table_mixed_cell,
    is_merged_numeric_cell,
    is_percent_glued_to_reason_text,
    looks_like_change_reason_description_not_label,
    peel_trailing_percent_reason,
    split_amount_percent_reason_text,
    split_amount_percent_text,
    split_label_trailing_amount,
    split_numeric_tokens,
    split_percent_amount_reason_text,
    split_percent_trailing_text,
    split_quarter_header_compound_text,
    split_report_date_header_compound_text,
    split_value_trailing_text_label,
)
from codes.table_engine.models import BBox, Cell, ColumnGrid, ColumnRange, StructuredTable

RowPhase = Literal["header", "body", "any"]


@dataclass(frozen=True)
class CellFragments:
    """单格拆出的字段片段 → 列角色（非列号，便于不同列数映射）。"""

    label: str = ""
    amount_prior: str = ""
    amount: str = ""
    percent: str = ""
    reason: str = ""
    numeric_tokens: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 统一检测：任何规则命中即视为「多字段粘连」
# ---------------------------------------------------------------------------

def is_merged_multi_field_cell(text: str) -> bool:
    """单格/item 是否混入多种应分列的字段语义。"""
    t = str(text or "").strip()
    if not t:
        return False
    if split_amount_percent_reason_text(t):
        return True
    if split_percent_amount_reason_text(t):
        return True
    if split_value_trailing_text_label(t):
        return True
    if split_amount_percent_text(t):
        return True
    if is_percent_glued_to_reason_text(t) or has_percent_glued_to_chinese_text(t):
        return True
    if split_label_trailing_amount(t):
        return True
    if len(split_quarter_header_compound_text(t)) >= 2:
        return True
    if len(split_report_date_header_compound_text(t)) >= 2:
        return True
    if is_change_table_mixed_cell(t):
        return True
    if is_merged_numeric_cell(t):
        tokens = split_numeric_tokens(t)
        if len(tokens) >= 2 and not _is_face_value_rate_pair(tokens):
            return True
    return False


def _is_face_value_rate_pair(tokens: Sequence[str]) -> bool:
    if len(tokens) != 2:
        return False

    def _rate_like(tok: str) -> bool:
        if "." not in tok:
            return False
        try:
            return float(tok.replace("，", ",").replace(",", "")) < 50.0
        except ValueError:
            return False

    def _amount_like(tok: str) -> bool:
        if "," in tok.replace("，", ","):
            return True
        try:
            return float(tok.replace("，", ",").replace(",", "")) >= 100.0
        except ValueError:
            return False

    a, b = tokens[0], tokens[1]
    return (_amount_like(a) and _rate_like(b)) or (_amount_like(b) and _rate_like(a))


def extract_cell_fragments(text: str) -> Optional[CellFragments]:
    """将粘连文本拆成语义片段（不涉及列号）。"""
    from codes.table_engine.geometry.numeric import _percent_token_looks_like_table_change_metric

    t = str(text or "").strip()
    if not t:
        return None

    triple = split_amount_percent_reason_text(t)
    if triple:
        amt, pct, reason = triple
        return CellFragments(amount=amt, percent=pct, reason=reason)

    par = split_percent_amount_reason_text(t)
    if par:
        pct, amt, reason = par
        return CellFragments(amount=amt, percent=pct, reason=reason)

    pair = split_amount_percent_text(t)
    if pair:
        amt, pct = pair
        return CellFragments(amount=amt, percent=pct)

    m = re.match(r"^(-?[\d,，]+\.?\d*%)\s+([\d,，]+)$", t)
    if m and _percent_token_looks_like_table_change_metric(m.group(1).strip()):
        return CellFragments(percent=m.group(1).strip(), amount=m.group(2).strip())

    peel = peel_trailing_percent_reason(t)
    if peel:
        prefix, pct, reason = peel
        if prefix:
            if is_merged_numeric_cell(prefix):
                tokens = tuple(split_numeric_tokens(prefix))
                if tokens:
                    return CellFragments(
                        percent=pct,
                        reason=reason,
                        numeric_tokens=tokens,
                    )
            pair = split_amount_percent_text(prefix)
            if pair:
                amt, pct2 = pair
                return CellFragments(amount=amt, percent=pct2, reason=reason)
        return CellFragments(percent=pct, reason=reason)

    la = split_label_trailing_amount(t)
    if la:
        label, amt = la
        return CellFragments(label=label, amount=amt)

    vt = split_value_trailing_text_label(t)
    if vt:
        val, label = vt
        return CellFragments(amount=val, reason=label)

    if is_merged_numeric_cell(t):
        tokens = tuple(split_numeric_tokens(t))
        if len(tokens) >= 2 and not _is_face_value_rate_pair(tokens):
            if len(tokens) == 2 and tokens[0].rstrip().endswith("%"):
                return CellFragments(percent=tokens[0], amount=tokens[1])
            if len(tokens) == 2 and tokens[1].rstrip().endswith("%"):
                return CellFragments(amount=tokens[0], percent=tokens[1])
            return CellFragments(numeric_tokens=tokens)

    return None


# ---------------------------------------------------------------------------
# Item 级分解（建表 / 行重建）
# ---------------------------------------------------------------------------

def decompose_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
    *,
    row_idx: int = 0,
    row_phase: str = "",
    value_cols: Optional[List[int]] = None,
    include_merged_numeric: bool = True,
) -> List[dict]:
    """建表时唯一 item 分解入口（顺序固定，覆盖所有 expand_* 规则）。"""
    from codes.table_engine.geometry.cell_numeric_repair import (
        expand_change_table_mixed_row_items,
        expand_compound_quarter_header_row_items,
        expand_compound_report_date_header_row_items,
        expand_label_numeric_glued_row_items,
        expand_merged_numeric_row_items,
        expand_percent_reason_glued_row_items,
        expand_value_text_glued_row_items,
    )

    items = list(row_items)
    is_header_band = row_phase == "header" or row_idx < 4

    items = expand_label_numeric_glued_row_items(
        items, col_ranges, value_cols=value_cols,
    )
    if is_header_band:
        items = expand_compound_quarter_header_row_items(items, col_ranges)
        items = expand_compound_report_date_header_row_items(items, col_ranges)
    items = expand_change_table_mixed_row_items(
        items, col_ranges, value_cols=value_cols,
    )
    items = expand_value_text_glued_row_items(items, col_ranges)
    items = expand_percent_reason_glued_row_items(
        items, col_ranges, value_cols=value_cols,
    )
    if include_merged_numeric:
        items = expand_merged_numeric_row_items(
            items, col_ranges, value_cols=value_cols,
        )
    return items


def row_items_need_decomposition(
    row_items: Sequence[dict],
    col_cells: Sequence[Optional[Cell]],
) -> bool:
    """预览格或原始 item 是否仍含多字段粘连。"""
    from codes.table_engine.geometry.cell_numeric_repair import is_illegal_value_cell

    if any(c is not None and is_illegal_value_cell(c.text) for c in col_cells):
        return True
    for it in row_items:
        if is_merged_multi_field_cell(str(it.get("text", ""))):
            return True
    return False


# ---------------------------------------------------------------------------
# 矩阵级分解（StructuredTable cell.text）
# ---------------------------------------------------------------------------

def _table_is_change_reason_table(table: StructuredTable) -> bool:
    from codes.table_engine.table_access import dense_rows

    for row in dense_rows(table)[:14]:
        joined = " ".join(str(c or "").strip() for c in row if str(c or "").strip())
        if "项目" in joined and ("增减幅度" in joined or "变化幅度" in joined):
            return True
        if ("主要原因" in joined or "变化原因" in joined) and any(
            kw in joined for kw in ("2024", "2023", "增减")
        ):
            return True
    return False


def _table_has_merged_cells(table: StructuredTable) -> bool:
    for row in table.rows:
        for cell in row:
            if cell is None:
                continue
            if is_merged_multi_field_cell(cell.text):
                return True
            if has_percent_glued_to_chinese_text(cell.text):
                return True
    return False


def _row_has_misplaced_reason_label(cells: Sequence[str], ncols: int) -> bool:
    """首列误放主要原因说明（末列字段跑到第一列）。"""
    from codes.table_engine.split.boundary_overlap import row_has_value_data

    if ncols < 4:
        return False
    label = str(cells[0] or "").strip()
    if not looks_like_change_reason_description_not_label(label):
        return False
    return row_has_value_data(list(cells[1:]))


def _table_has_misplaced_reason_labels(table: StructuredTable) -> bool:
    from codes.table_engine.table_access import dense_rows

    ncols = max(
        table.grid.col_count,
        max((len(r) for r in table.rows), default=0),
    )
    if ncols < 4:
        return False
    for row in dense_rows(table):
        if _row_has_misplaced_reason_label(row, ncols):
            return True
    return False


def relocate_misplaced_reason_labels(table: StructuredTable) -> StructuredTable:
    """主要原因说明误落项目列 → 移到末列（数值列顺序不变）。"""
    if not table.rows:
        return table
    ncols = max(
        table.grid.col_count,
        max((len(r) for r in table.rows), default=0),
    )
    if ncols < 4:
        return table
    reason_ci = ncols - 1
    for ri, row in enumerate(table.rows):
        while len(row) < ncols:
            row.append(None)
        if row[0] is None:
            continue
        label_text = str(row[0].text or "").strip()
        if not label_text:
            continue
        cells = [str(c.text or "") if c else "" for c in row]
        if not _row_has_misplaced_reason_label(cells, ncols):
            continue
        if table.grid.ranges and row[0].bbox.x0 <= table.grid.ranges[0].x1 + 10:
            continue
        reason_cell = row[reason_ci] if reason_ci < len(row) else None
        existing = str(reason_cell.text or "").strip() if reason_cell else ""
        merged = f"{label_text}{existing}" if existing else label_text
        row[0].text = ""
        src = list(row[0].source_items or [])
        if reason_cell is not None and reason_cell.source_items:
            for sid in reason_cell.source_items:
                if sid and sid not in src:
                    src.append(sid)
        _set_matrix_cell(
            row,
            reason_ci,
            merged,
            row_idx=ri,
            table=table,
            source_items=src,
        )
    return table


def rows_look_like_change_reason_body(rows: List[dict]) -> bool:
    """无表头时：标签 + 金额 + 百分比/原因粘连 的表体行（变化原因表续行）。"""
    from codes.table_engine.geometry.numeric import is_numeric_data_cell

    hits = 0
    for row in rows:
        items = row.get("items") or []
        texts = [
            str(it.get("text", "")).strip()
            for it in items
            if str(it.get("text", "")).strip()
        ]
        if len(texts) < 3:
            continue
        if not re.search(r"[\u4e00-\u9fff]", texts[0]):
            continue
        amounts = sum(
            1
            for t in texts[1:]
            if is_numeric_data_cell(t) and not str(t).strip().endswith("%")
        )
        has_glued = any(
            split_amount_percent_reason_text(t)
            or split_amount_percent_text(t)
            or split_percent_trailing_text(t)
            for t in texts[1:]
        )
        if amounts >= 1 and has_glued:
            hits += 1
    return hits >= 2


def _table_body_looks_like_change_reason(table: StructuredTable) -> bool:
    from codes.table_engine.geometry.grid_infer import structured_table_to_row_dicts

    return rows_look_like_change_reason_body(structured_table_to_row_dicts(table))


def ensure_grid_for_decomposition(table: StructuredTable) -> StructuredTable:
    """列数不足以容纳拆段时扩网格（变化原因表 3/4 列 → 5 列）。"""
    ncols = table.grid.col_count or (len(table.rows[0]) if table.rows else 0)
    if ncols >= 5:
        return table
    is_change = _table_is_change_reason_table(table) or _table_body_looks_like_change_reason(table)
    if not is_change:
        return table
    if not _table_has_merged_cells(table) and ncols >= 4:
        return table
    if len(table.grid.ranges) < 3:
        return table

    ranges = list(table.grid.ranges)
    if ncols == 3 and len(ranges) == 3:
        wide = ranges[2]
        wlo, whi = wide.x0, wide.x1
        span = (whi - wlo) / 3.0
        new_ranges = [
            ranges[0],
            ranges[1],
            ColumnRange(wlo, wlo + span, 2),
            ColumnRange(wlo + span, wlo + 2 * span, 3),
            ColumnRange(wlo + 2 * span, whi, 4),
        ]
    elif ncols == 4 and len(ranges) == 4:
        wide = ranges[2]
        mid = wide.x0 + (wide.x1 - wide.x0) * 0.45
        new_ranges = [
            ranges[0],
            ranges[1],
            ColumnRange(wide.x0, mid, 2),
            ColumnRange(mid, wide.x1, 3),
            ColumnRange(ranges[3].x0, ranges[3].x1, 4),
        ]
    else:
        return table

    new_rows: List[List[Optional[Cell]]] = []
    for ri, row in enumerate(table.rows):
        new_row: List[Optional[Cell]] = [None] * 5
        for ci, cell in enumerate(row):
            if cell is None:
                continue
            if ci <= 1:
                target_ci = ci
            elif ncols == 3:
                target_ci = 2
            else:
                target_ci = 2 if ci == 2 else 4
            nc = copy.copy(cell)
            nc.col = target_ci
            nc.row = ri
            new_row[target_ci] = nc
        new_rows.append(new_row)

    out = copy.copy(table)
    out.rows = new_rows
    out.grid = ColumnGrid(
        ranges=new_ranges,
        layout_id=table.grid.layout_id,
        confidence=table.grid.confidence,
    )
    return out


def _column_role_map(ncols: int) -> Dict[str, int]:
    """标准财务表列角色 → 列索引。"""
    if ncols >= 5:
        return {
            "label": 0,
            "amount_current": 1,
            "amount_prior": 2,
            "percent": ncols - 2,
            "reason": ncols - 1,
        }
    if ncols == 4:
        return {
            "label": 0,
            "amount_current": 1,
            "amount_prior": 2,
            "percent": 3,
            "reason": 3,
        }
    # 列数不足 5 时不应落位分解；ensure_grid_for_decomposition 应先扩列
    raise ValueError(f"change-reason decomposition requires >=4 cols, got {ncols}")


def _set_matrix_cell(
    row: List[Optional[Cell]],
    ci: int,
    text: str,
    *,
    row_idx: int,
    table: StructuredTable,
    source_items: Optional[List[str]] = None,
) -> None:
    while len(row) <= ci:
        row.append(None)
    src = list(source_items or [])
    if row[ci] is None:
        if ci < len(table.grid.ranges):
            cr = table.grid.ranges[ci]
            lo, hi = cr.x0, cr.x1
        else:
            lo, hi = table.x0, table.x1
        row[ci] = Cell(
            text=text,
            bbox=BBox(lo, table.y0, hi, table.y1),
            row=row_idx,
            col=ci,
            source_items=src,
        )
    else:
        row[ci].text = text
        if src:
            row[ci].source_items = src


def _apply_fragments_to_row(
    row: List[Optional[Cell]],
    *,
    row_idx: int,
    table: StructuredTable,
    source_ci: int,
    source_cell: Cell,
    frags: CellFragments,
    roles: Dict[str, int],
) -> None:
    src_ids = list(source_cell.source_items or [])
    written_cols: set[int] = set()
    orig = str(source_cell.text or "").strip()
    vt_pair = split_value_trailing_text_label(orig)
    if (
        vt_pair
        and frags.amount
        and frags.reason
        and not frags.percent
        and not frags.label
    ):
        ncols = max(len(row), max(roles.values()) + 1, 4)
        ci_val = ncols - 2
        ci_text = ncols - 1
        val, label = vt_pair
        written_cols.update({ci_val, ci_text})
        _set_matrix_cell(
            row, ci_val, val,
            row_idx=row_idx, table=table, source_items=src_ids,
        )
        _set_matrix_cell(
            row, ci_text, label,
            row_idx=row_idx, table=table, source_items=src_ids,
        )
        if source_ci not in written_cols:
            source_cell.text = ""
        return
    if frags.label:
        ci = roles["label"]
        written_cols.add(ci)
        _set_matrix_cell(
            row, ci, frags.label,
            row_idx=row_idx, table=table, source_items=src_ids,
        )
    if frags.amount:
        ci = roles.get("amount_prior", roles.get("amount_current", source_ci))
        written_cols.add(ci)
        _set_matrix_cell(
            row, ci, frags.amount,
            row_idx=row_idx, table=table, source_items=src_ids,
        )
    if frags.percent:
        ci = roles["percent"]
        written_cols.add(ci)
        _set_matrix_cell(
            row, ci, frags.percent,
            row_idx=row_idx, table=table, source_items=src_ids,
        )
    if frags.reason:
        ci = roles["reason"]
        written_cols.add(ci)
        _set_matrix_cell(
            row, ci, frags.reason,
            row_idx=row_idx, table=table, source_items=src_ids,
        )
    if frags.numeric_tokens:
        vcols = [
            roles.get("amount_current", 1),
            roles.get("amount_prior", 2),
        ]
        for i, tok in enumerate(frags.numeric_tokens):
            if i >= len(vcols):
                break
            ci = vcols[i]
            written_cols.add(ci)
            _set_matrix_cell(
                row, ci, tok,
                row_idx=row_idx, table=table, source_items=src_ids,
            )

    if source_ci not in written_cols:
        source_cell.text = ""


def decompose_table(table: StructuredTable) -> StructuredTable:
    """矩阵级唯一分解入口：对所有粘连格拆段并按列角色落位。"""
    if not table.rows:
        return table

    table = ensure_grid_for_decomposition(table)
    ncols = max(
        table.grid.col_count,
        max((len(r) for r in table.rows), default=0),
    )
    if ncols < 4:
        return table

    try:
        roles = _column_role_map(ncols)
    except ValueError:
        return table
    decomposed_ids: set[str] = set()

    for ri, row in enumerate(table.rows):
        while len(row) < ncols:
            row.append(None)
        for ci, cell in enumerate(list(row)):
            if cell is None:
                continue
            t = str(cell.text or "").strip()
            if not t:
                continue
            if not is_merged_multi_field_cell(t) and not has_percent_glued_to_chinese_text(t):
                if not split_value_trailing_text_label(t):
                    continue
            frags = extract_cell_fragments(t)
            if frags is None:
                continue
            _apply_fragments_to_row(
                row,
                row_idx=ri,
                table=table,
                source_ci=ci,
                source_cell=cell,
                frags=frags,
                roles=roles,
            )
            for sid in cell.source_items or []:
                if sid:
                    decomposed_ids.add(str(sid))

    if decomposed_ids:
        existing = set(str(s) for s in table.metadata.get("decomposed_source_ids") or [])
        table.metadata["decomposed_source_ids"] = sorted(existing | decomposed_ids)

    if _table_is_change_reason_table(table) or _table_body_looks_like_change_reason(table):
        table = relocate_misplaced_reason_labels(table)

    return table


# ---------------------------------------------------------------------------
# 网格评分（列界推断）
# ---------------------------------------------------------------------------

def count_decomposition_violations(
    rows: List[dict],
    col_ranges: List[Tuple[float, float]],
) -> int:
    """模拟分列后仍有多字段粘连的 item 数（供 grid_infer 选列界）。"""
    from codes.table_engine.geometry.layout_rows import body_rows_for_layout

    violations = 0
    body = body_rows_for_layout(rows) or rows
    for row in body:
        for it in row.get("items") or []:
            if is_merged_multi_field_cell(str(it.get("text", "")).strip()):
                violations += 1
    if violations and len(col_ranges) < 5:
        violations += 2
    return violations


# ---------------------------------------------------------------------------
# 守恒：已拆段行禁止重新粘回
# ---------------------------------------------------------------------------

def row_covers_source_text(row: Sequence[Optional[Cell]], source_text: str) -> bool:
    """整行文本是否已覆盖某 source item 的全部内容（允许空格差异）。"""
    joined = "".join(str(c.text or "") for c in row if c is not None).replace(" ", "")
    src = str(source_text or "").replace(" ", "")
    if not src or not joined:
        return False
    return src in joined


def cell_should_skip_source_reglue(
    cell: Cell,
    row: Sequence[Optional[Cell]],
    *,
    full_source_text: str,
    decomposed_source_ids: Optional[set[str]] = None,
) -> bool:
    """分解后的片段格：守恒阶段不要用完整 source 重新合并。"""
    existing = str(cell.text or "").strip()
    full = str(full_source_text or "").strip()
    if not existing or not full or existing == full:
        return False
    if existing.replace(" ", "") == full.replace(" ", ""):
        return False

    src_ids = {str(s) for s in (cell.source_items or []) if s}
    if decomposed_source_ids and src_ids & decomposed_source_ids:
        if row_covers_source_text(row, full):
            return True

    ex = existing.replace(" ", "")
    fu = full.replace(" ", "")
    if ex in fu and row_covers_source_text(row, full):
        return True
    return False
