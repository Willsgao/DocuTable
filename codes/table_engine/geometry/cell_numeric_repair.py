# -*- coding: utf-8 -*-
"""表体数值格校验与修复：检测非法合并格并按 anchor 重分列。"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from codes.table_engine.geometry.data_column_assign import (
    assign_data_value_column,
    is_data_value_item,
    is_pillar_serial_item,
    reconcile_col_items_by_anchor,
)
from codes.table_engine.geometry.column_anchors import is_item_in_label_column_zone
from codes.table_engine.geometry.cell_decomposition import (
    decompose_row_items,
    is_merged_multi_field_cell,
)
from codes.table_engine.geometry.numeric import (
    is_merged_numeric_cell,
    looks_like_change_reason_description_not_label,
)
from codes.table_engine.models import BBox, Cell


def _infer_value_col_indices(
    col_ranges: List[Tuple[float, float]],
    value_cols: Optional[List[int]] = None,
) -> List[int]:
    if value_cols:
        return list(value_cols)
    if len(col_ranges) <= 2:
        return [len(col_ranges) - 1]
    return [i for i, (lo, _) in enumerate(col_ranges) if lo >= 150]


def expand_merged_numeric_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
    *,
    value_cols: Optional[List[int]] = None,
) -> List[dict]:
    """将 liteparse 粘连的多值 item（如 '209,952 498,673'）拆成独立 item 并估 x。"""
    from codes.table_engine.geometry.column_anchors import col_index_by_x0
    from codes.table_engine.geometry.numeric import split_numeric_tokens

    if not row_items:
        return []

    vcols = _infer_value_col_indices(col_ranges, value_cols)
    out: List[dict] = []

    for it in row_items:
        t = str(it.get("text", "")).strip()
        if not is_merged_numeric_cell(t):
            out.append(it)
            continue
        tokens = split_numeric_tokens(t)
        if len(tokens) < 2:
            out.append(it)
            continue
        if _is_face_value_and_rate_pair(tokens):
            out.append(it)
            continue

        x0_base = float(it.get("x0", 0))
        start_ci = col_index_by_x0(x0_base, col_ranges)
        if start_ci not in vcols:
            start_ci = next((c for c in vcols if c >= start_ci), vcols[0])
        seq = [c for c in vcols if c >= start_ci]
        if len(seq) < len(tokens):
            seq = vcols[-len(tokens) :]
        targets = seq[: len(tokens)]

        for j, tok in enumerate(tokens):
            ci = targets[min(j, len(targets) - 1)]
            lo, hi = col_ranges[ci]
            mid = (lo + hi) / 2.0
            span = min(28.0, max(12.0, (hi - lo) * 0.42))
            new_it = dict(it)
            new_it["text"] = tok
            new_it["x0"] = mid - span
            new_it["x1"] = mid + span
            out.append(new_it)

    return sorted(
        out,
        key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
    )


def expand_label_numeric_glued_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
    *,
    value_cols: Optional[List[int]] = None,
) -> List[dict]:
    """标签列 item 末尾粘连首列金额时拆成标签 + 数值两个 item。"""
    from codes.table_engine.geometry.numeric import split_label_trailing_amount

    if not row_items or len(col_ranges) < 2:
        return list(row_items)

    vcols = _infer_value_col_indices(col_ranges, value_cols)
    first_val = vcols[0] if vcols else 1
    val_lo, val_hi = col_ranges[first_val]
    val_mid = (val_lo + val_hi) / 2.0
    val_span = min(28.0, max(12.0, (val_hi - val_lo) * 0.42))
    label_hi = col_ranges[0][1] if col_ranges else val_lo - 4

    out: List[dict] = []
    for it in row_items:
        t = str(it.get("text", "")).strip()
        x0 = float(it.get("x0", 0))
        split = split_label_trailing_amount(t)
        if (
            split is None
            or x0 >= val_lo - 20
        ):
            out.append(it)
            continue
        label, amount = split
        base = dict(it)
        label_it = dict(base)
        label_it["text"] = label
        label_it["x0"] = x0
        label_it["x1"] = min(float(it.get("x1", label_hi)), label_hi)
        num_it = dict(base)
        num_it["text"] = amount
        num_it["x0"] = val_mid - val_span
        num_it["x1"] = val_mid + val_span
        out.extend([label_it, num_it])

    return sorted(
        out,
        key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
    )


def expand_compound_quarter_header_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
) -> List[dict]:
    """OCR 将「一季度…四季度 项目」粘成一格 → 按列界拆成独立 item。"""
    from codes.table_engine.geometry.numeric import (
        is_quarter_column_header_text,
        split_quarter_header_compound_text,
    )

    if not row_items or len(col_ranges) < 3:
        return list(row_items)

    vcols = _infer_value_col_indices(col_ranges, None)
    if len(vcols) < 2:
        vcols = list(range(1, len(col_ranges)))

    out: List[dict] = []
    for it in row_items:
        t = str(it.get("text", "")).strip()
        parts = split_quarter_header_compound_text(t)
        if len(parts) < 2:
            out.append(it)
            continue

        quarter_parts = [p for p in parts if is_quarter_column_header_text(p)]
        if len(quarter_parts) < 2:
            out.append(it)
            continue

        assignments: List[Tuple[str, int]] = []
        if "项目" in parts:
            assignments.append(("项目", 0))
        for i, qp in enumerate(quarter_parts):
            ci = vcols[i] if i < len(vcols) else vcols[-1]
            assignments.append((qp, ci))

        for text, ci in assignments:
            lo, hi = col_ranges[ci]
            mid = (lo + hi) / 2.0
            span = min(42.0, max(14.0, (hi - lo) * 0.42))
            new_it = dict(it)
            new_it["text"] = text
            new_it["x0"] = mid - span
            new_it["x1"] = mid + span
            out.append(new_it)

    return sorted(
        out,
        key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
    )


def _infer_pct_reason_col_indices(
    col_ranges: List[Tuple[float, float]],
) -> Tuple[int, int]:
    """变化原因表：倒数第二列=增减幅度，末列=主要原因/变化原因。"""
    n = len(col_ranges)
    if n < 4:
        return max(0, n - 1), max(0, n - 1)
    return n - 2, n - 1


def seed_expand_glued_items_for_grid(rows: List[dict]) -> List[dict]:
    """建表推列前：不依赖列界，按 bbox 切开粘连表头/百分比+原因，暴露真实列锚点。

    解决：liteparse 将「2024…2023…增减幅度」或「一季度…四季度」粘成一项时列数被低估，
    后续 expand_compound_* 把多段表头挤进同一列（如三/四季度粘在末列）。
    """
    from codes.table_engine.geometry.numeric import (
        is_quarter_column_header_text,
        split_percent_point_change_text,
        split_percent_trailing_text,
        split_quarter_header_compound_text,
        split_report_date_header_compound_text,
    )

    if not rows:
        return rows

    out_rows: List[dict] = []
    for row in rows:
        items = list(row.get("items") or [])
        if not items:
            out_rows.append(row)
            continue
        new_items: List[dict] = []
        changed = False
        for it in items:
            t = str(it.get("text", "")).strip()
            x0 = float(it.get("x0", 0))
            x1 = float(it.get("x1", x0))
            parts: List[str] = []
            header_parts = split_report_date_header_compound_text(t)
            if len(header_parts) >= 3:
                parts = header_parts
            else:
                quarter_parts = [
                    p
                    for p in split_quarter_header_compound_text(t)
                    if is_quarter_column_header_text(p)
                ]
                if len(quarter_parts) >= 2:
                    # 「项目」若粘在同格则保留；独立「项目」item 仍留在原处
                    if "项目" in t and "项目" not in quarter_parts:
                        parts = ["项目"] + quarter_parts
                    else:
                        parts = quarter_parts
                else:
                    ppc = split_percent_point_change_text(t)
                    if ppc:
                        parts = [ppc[0], ppc[1]]
                    else:
                        pr = split_percent_trailing_text(t)
                        if pr:
                            parts = [pr[0], pr[1]]
            if len(parts) < 2 or x1 <= x0 + 8:
                new_items.append(it)
                continue
            changed = True
            width = x1 - x0
            n = len(parts)
            base_id = str(it.get("item_index", "") or "")
            for i, text in enumerate(parts):
                seg0 = x0 + width * i / n
                segi = x0 + width * (i + 1) / n
                mid = (seg0 + segi) / 2.0
                new_it = dict(it)
                new_it["text"] = text
                new_it["x0"] = mid - 4.0
                new_it["x1"] = mid + 4.0
                if base_id:
                    new_it["item_index"] = f"{base_id}#g{i}"
                new_items.append(new_it)
        if not changed:
            out_rows.append(row)
            continue
        new_row = dict(row)
        new_row["items"] = sorted(
            new_items,
            key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
        )
        out_rows.append(new_row)
    return out_rows


def expand_compound_report_date_header_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
) -> List[dict]:
    """OCR 将「2024…2023…增减幅度」或「2023年 增减幅度 变化原因」粘成一格 → 按坐标拆开。"""
    from codes.table_engine.geometry.numeric import (
        split_report_date_header_compound_text,
    )

    if not row_items or len(col_ranges) < 3:
        return list(row_items)

    n_cols = len(col_ranges)
    out: List[dict] = []
    for it in row_items:
        t = str(it.get("text", "")).strip()
        parts = split_report_date_header_compound_text(t)
        if len(parts) < 3:
            out.append(it)
            continue
        if "项目" in t and "项目" not in parts:
            parts = ["项目"] + parts

        x0 = float(it.get("x0", 0))
        x1 = float(it.get("x1", 0))
        if "项目" in parts and x0 > 80:
            parts = [p for p in parts if p != "项目"]
            if len(parts) < 3:
                out.append(it)
                continue

        # 优先按列角色落位（5 列变化原因表：日期→1/2，增减→3，原因→4）
        role_targets = _header_part_col_targets(parts, n_cols)
        if role_targets is not None:
            for i, (text, ci) in enumerate(zip(parts, role_targets)):
                lo, hi = col_ranges[ci]
                mid = (lo + hi) / 2.0
                span = min(42.0, max(12.0, (hi - lo) * 0.4))
                new_it = dict(it)
                new_it["text"] = text
                new_it["x0"] = mid - span
                new_it["x1"] = mid + span
                base_id = str(it.get("item_index", "") or "")
                if base_id:
                    new_it["item_index"] = f"{base_id}#h{i}"
                out.append(new_it)
            continue

        if x1 <= x0 + 8:
            vcols = list(range(1, n_cols))
            n = len(parts)
            start = max(0, len(vcols) - n)
            for i, text in enumerate(parts):
                ci = vcols[min(start + i, len(vcols) - 1)]
                lo, hi = col_ranges[ci]
                mid = (lo + hi) / 2.0
                span = min(42.0, max(14.0, (hi - lo) * 0.42))
                new_it = dict(it)
                new_it["text"] = text
                new_it["x0"] = mid - span
                new_it["x1"] = mid + span
                out.append(new_it)
            continue

        width = x1 - x0
        n = len(parts)
        for i, text in enumerate(parts):
            seg0 = x0 + width * i / n
            segi = x0 + width * (i + 1) / n
            mid = (seg0 + segi) / 2.0
            new_it = dict(it)
            new_it["text"] = text
            new_it["x0"] = mid - 4.0
            new_it["x1"] = mid + 4.0
            base_id = str(it.get("item_index", "") or "")
            if base_id:
                new_it["item_index"] = f"{base_id}#h{i}"
            out.append(new_it)

    return sorted(
        out,
        key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
    )


def _header_part_col_targets(
    parts: Sequence[str],
    n_cols: int,
) -> Optional[List[int]]:
    """报告期+增减/原因粘连片段 → 目标列号；无法可靠映射时返回 None。"""
    from codes.table_engine.geometry.column_anchors import is_report_period_cell
    from codes.table_engine.geometry.numeric import is_report_date_header_part_text

    if n_cols < 4 or len(parts) < 3:
        return None
    if any(p == "项目" or str(p).startswith("项目") for p in parts):
        return None

    dates: List[int] = []
    metrics: List[int] = []
    for i, p in enumerate(parts):
        t = str(p).strip()
        # 增减/原因须先于 is_report_date_header_part_text（后者会误收「增减幅度」）
        if (
            t in ("增减幅度", "增减", "变化幅度", "变化原因", "主要原因")
            or "增减" in t
            or "原因" in t
        ):
            metrics.append(i)
        elif is_report_period_cell(t) or is_report_date_header_part_text(t):
            dates.append(i)
        else:
            return None
    if len(dates) < 2 or not metrics:
        return None

    targets = [1] * len(parts)
    # 日期从 col1 起顺序落
    for k, di in enumerate(dates):
        targets[di] = min(1 + k, n_cols - 2)
    # 增减 → 倒数第二（有原因列时）或日期后一列；原因 → 末列
    next_ci = max(targets) + 1
    for mi in metrics:
        t = str(parts[mi]).strip()
        if "原因" in t:
            targets[mi] = n_cols - 1
        elif "增减" in t or "幅度" in t:
            targets[mi] = n_cols - 2 if n_cols >= 5 else min(next_ci, n_cols - 1)
            next_ci = max(next_ci, targets[mi] + 1)
        else:
            targets[mi] = min(next_ci, n_cols - 1)
            next_ci += 1
    # 避免两日期挤同一列
    used = {}
    for i, ci in enumerate(targets):
        if i not in dates:
            continue
        while ci in used and used[ci] in dates and ci + 1 < n_cols - (1 if n_cols >= 5 else 0):
            ci += 1
        targets[i] = ci
        used[ci] = i
    return targets


def expand_change_table_mixed_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
    *,
    value_cols: Optional[List[int]] = None,
) -> List[dict]:
    """金额 + 百分比 + 原因粘在同一 item → 拆成三个 item 按列落位。"""
    from codes.table_engine.geometry.numeric import (
        split_amount_percent_reason_text,
        split_percent_amount_reason_text,
        split_percent_trailing_text,
    )

    if not row_items or len(col_ranges) < 4:
        return list(row_items)

    n = len(col_ranges)
    pct_ci, reason_ci = _infer_pct_reason_col_indices(col_ranges)
    vcols = _infer_value_col_indices(col_ranges, value_cols)
    amt_ci = vcols[1] if len(vcols) >= 2 and n >= 5 else 2

    def _item_at_col(text: str, ci: int, base: dict) -> dict:
        lo, hi = col_ranges[ci]
        mid = (lo + hi) / 2.0
        span = min(36.0, max(12.0, (hi - lo) * 0.4))
        new_it = dict(base)
        new_it["text"] = text
        new_it["x0"] = mid - span
        new_it["x1"] = mid + span
        return new_it

    def _item_at_col_frac(text: str, ci: int, frac: float, base: dict) -> dict:
        lo, hi = col_ranges[ci]
        pos = lo + (hi - lo) * frac
        span = min(36.0, max(12.0, (hi - lo) * 0.32))
        new_it = dict(base)
        new_it["text"] = text
        new_it["x0"] = pos - span
        new_it["x1"] = pos + span
        return new_it

    out: List[dict] = []
    for it in row_items:
        t = str(it.get("text", "")).strip()
        triple = split_amount_percent_reason_text(t)
        if triple:
            amt, pct, reason = triple
            if n >= 5:
                out.extend([
                    _item_at_col(amt, amt_ci, it),
                    _item_at_col(pct, pct_ci, it),
                    _item_at_col(reason, reason_ci, it),
                ])
            else:
                out.extend([
                    _item_at_col_frac(amt, amt_ci, 0.28, it),
                    _item_at_col_frac(pct, amt_ci, 0.72, it),
                    _item_at_col_frac(reason, reason_ci, 0.35, it),
                ])
            continue
        par = split_percent_amount_reason_text(t)
        if par:
            pct, amt, reason = par
            if n >= 5:
                out.extend([
                    _item_at_col(amt, amt_ci, it),
                    _item_at_col(pct, pct_ci, it),
                    _item_at_col(reason, reason_ci, it),
                ])
            else:
                out.extend([
                    _item_at_col_frac(amt, amt_ci, 0.28, it),
                    _item_at_col_frac(pct, amt_ci, 0.72, it),
                    _item_at_col_frac(reason, reason_ci, 0.35, it),
                ])
            continue
        split = split_percent_trailing_text(t)
        if split and re.search(r"[\d,，]{3,}", t) and re.search(r"[\u4e00-\u9fff]", t):
            pct, reason = split
            if n >= 5:
                out.extend([
                    _item_at_col(pct, pct_ci, it),
                    _item_at_col(reason, reason_ci, it),
                ])
            else:
                out.extend([
                    _item_at_col_frac(pct, amt_ci, 0.72, it),
                    _item_at_col_frac(reason, reason_ci, 0.35, it),
                ])
            continue
        out.append(it)

    return sorted(
        out,
        key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
    )


def expand_value_text_glued_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
) -> List[dict]:
    """末列数值+文本标签粘连（如「5,780 交易性金融资产」）→ 倒数第二列数值 + 末列文本。"""
    from codes.table_engine.geometry.numeric import split_value_trailing_text_label

    if not row_items or len(col_ranges) < 3:
        return list(row_items)

    n = len(col_ranges)
    val_ci, text_ci = n - 2, n - 1
    val_lo, val_hi = col_ranges[val_ci]
    text_lo, text_hi = col_ranges[text_ci]
    val_mid = (val_lo + val_hi) / 2.0
    text_mid = (text_lo + text_hi) / 2.0
    val_span = min(28.0, max(10.0, (val_hi - val_lo) * 0.4))
    text_span = min(48.0, max(14.0, (text_hi - text_lo) * 0.45))
    label_hi = col_ranges[0][1] if col_ranges else 150.0

    out: List[dict] = []
    for it in row_items:
        t = str(it.get("text", "")).strip()
        split = split_value_trailing_text_label(t)
        if split is None:
            out.append(it)
            continue
        x0 = float(it.get("x0", 0))
        if x0 < col_ranges[max(0, n - 3)][0] - 12.0:
            out.append(it)
            continue
        val, label = split
        base = dict(it)
        val_it = dict(base)
        val_it["text"] = val
        val_it["x0"] = val_mid - val_span
        val_it["x1"] = val_mid + val_span
        text_it = dict(base)
        text_it["text"] = label
        text_it["x0"] = text_mid - text_span
        text_it["x1"] = text_mid + text_span
        out.extend([val_it, text_it])

    return sorted(
        out,
        key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
    )


def expand_percent_point_change_glued_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
) -> List[dict]:
    """「18.78% 下降 0.97 个百分点」跨列粘连 → 数值列 + 增减列（不走变化原因落列）。"""
    from codes.table_engine.geometry.column_anchors import col_index_by_x0, col_index_by_x1
    from codes.table_engine.geometry.numeric import split_percent_point_change_text

    if not row_items or len(col_ranges) < 4:
        return list(row_items)

    n = len(col_ranges)
    label_hi = col_ranges[0][1] if col_ranges else 150.0
    out: List[dict] = []
    for it in row_items:
        t = str(it.get("text", "")).strip()
        split = split_percent_point_change_text(t)
        if split is None:
            out.append(it)
            continue
        x0 = float(it.get("x0", 0))
        x1 = float(it.get("x1", x0))
        if x0 < label_hi - 8.0:
            out.append(it)
            continue
        pct, change = split
        val_ci = col_index_by_x0(x0, col_ranges)
        if val_ci <= 0:
            val_ci = 1
        # 增减列：宽框右缘 / 中点，且须在数值列右侧；5 列年报表避免落到末年列
        mid_x = x0 + max(8.0, (x1 - x0) * 0.62)
        chg_ci = col_index_by_x0(mid_x, col_ranges)
        if chg_ci <= val_ci:
            chg_by_x1 = col_index_by_x1(x1, col_ranges)
            chg_ci = chg_by_x1 if chg_by_x1 > val_ci else val_ci + 1
        if n >= 5 and chg_ci >= n - 1 and val_ci <= n - 3:
            chg_ci = n - 2
        chg_ci = min(max(chg_ci, val_ci + 1), n - 1)
        if chg_ci == val_ci:
            chg_ci = min(val_ci + 1, n - 1)

        def _place(text: str, ci: int, base: dict) -> dict:
            lo, hi = col_ranges[ci]
            mid = (lo + hi) / 2.0
            span = min(36.0, max(10.0, (hi - lo) * 0.4))
            new_it = dict(base)
            new_it["text"] = text
            new_it["x0"] = mid - span
            new_it["x1"] = mid + span
            base_id = str(base.get("item_index", "") or "")
            if base_id:
                tag = "ppc0" if text == pct else "ppc1"
                new_it["item_index"] = f"{base_id}#{tag}"
            return new_it

        out.extend([_place(pct, val_ci, it), _place(change, chg_ci, it)])

    return sorted(
        out,
        key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
    )


def expand_percent_reason_glued_row_items(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
    *,
    value_cols: Optional[List[int]] = None,
) -> List[dict]:
    """百分比与变化原因粘连 → 分列到「增减幅度」与「变化原因」。"""
    from codes.table_engine.geometry.numeric import (
        split_percent_point_change_text,
        split_percent_trailing_text,
    )

    if not row_items or len(col_ranges) < 4:
        return list(row_items)

    pct_ci, reason_ci = _infer_pct_reason_col_indices(col_ranges)
    pct_lo, pct_hi = col_ranges[pct_ci]
    reason_lo, reason_hi = col_ranges[reason_ci]
    pct_mid = (pct_lo + pct_hi) / 2.0
    reason_mid = (reason_lo + reason_hi) / 2.0
    pct_span = min(24.0, max(10.0, (pct_hi - pct_lo) * 0.4))
    reason_span = min(48.0, max(14.0, (reason_hi - reason_lo) * 0.45))
    label_hi = col_ranges[0][1] if col_ranges else 150.0

    out: List[dict] = []
    for it in row_items:
        t = str(it.get("text", "")).strip()
        # 百分点跨列粘连已由 expand_percent_point_change_glued_row_items 处理
        if split_percent_point_change_text(t) is not None:
            out.append(it)
            continue
        split = split_percent_trailing_text(t)
        if split is None:
            out.append(it)
            continue
        x0 = float(it.get("x0", 0))
        if x0 < label_hi - 8.0:
            out.append(it)
            continue
        pct, reason = split
        base = dict(it)
        pct_it = dict(base)
        pct_it["text"] = pct
        pct_it["x0"] = pct_mid - pct_span
        pct_it["x1"] = pct_mid + pct_span
        reason_it = dict(base)
        reason_it["text"] = reason
        reason_it["x0"] = reason_mid - reason_span
        reason_it["x1"] = reason_mid + reason_span
        out.extend([pct_it, reason_it])

    return sorted(
        out,
        key=lambda d: (float(d.get("y0", 0)), float(d.get("x0", 0))),
    )


def is_illegal_value_cell(text: str) -> bool:
    """单格疑似误并：多数值、短横+数、千分位非法、中英文数值粘连、百分比+说明。"""
    from codes.table_engine.geometry.numeric import (
        is_percent_text_merged_cell,
        split_numeric_tokens,
    )

    t = str(text or "").strip()
    if not t:
        return False
    if is_merged_multi_field_cell(t):
        return True
    if is_percent_text_merged_cell(t):
        return True
    if is_merged_numeric_cell(t):
        tokens = split_numeric_tokens(t)
        if _is_face_value_and_rate_pair(tokens):
            return False
        return True
    return False


def _is_face_value_and_rate_pair(tokens: List[str]) -> bool:
    """债券面值 + 票面利率同格（如 17,440 3.75）为合法组合。"""
    if len(tokens) != 2:
        return False

    def _rate_like(tok: str) -> bool:
        s = tok.replace("，", ",").replace(",", "")
        if "." not in tok:
            return False
        try:
            return float(s) < 50.0
        except ValueError:
            return False

    def _amount_like(tok: str) -> bool:
        if "," in tok.replace("，", ","):
            return True
        s = tok.replace("，", ",").replace(",", "")
        try:
            return float(s) >= 100.0
        except ValueError:
            return False

    a, b = tokens[0], tokens[1]
    return (_amount_like(a) and _rate_like(b)) or (_amount_like(b) and _rate_like(a))


def _row_has_illegal_value_cells(col_cells: Sequence[Optional[Cell]]) -> bool:
    return any(
        c is not None and is_illegal_value_cell(c.text)
        for c in col_cells
    )


def rebuild_row_col_items_by_anchor(
    row_items: Sequence[dict],
    col_ranges: List[Tuple[float, float]],
    n_cols: int,
    *,
    layout_id: str = "",
    value_cols: Optional[List[int]] = None,
    serial_col: Optional[int] = None,
    assign_label_fn,
) -> List[List[dict]]:
    """按 anchor 重落一整行：数值权威分列，标签走原标签规则。"""
    col_items: List[List[dict]] = [[] for _ in range(n_cols)]
    for it in sorted(row_items, key=lambda x: float(x.get("x0", 0))):
        text = str(it.get("text", "")).strip()
        if not text:
            continue
        if is_pillar_serial_item(it, col_ranges, serial_col=serial_col):
            ci = serial_col
        elif is_data_value_item(text):
            ci = assign_data_value_column(
                it, col_ranges, layout_id=layout_id, value_cols=value_cols,
            )
        elif (
            n_cols >= 4
            and looks_like_change_reason_description_not_label(text)
            and not is_item_in_label_column_zone(it, col_ranges)
        ):
            ci = n_cols - 1
            col_items[ci].append(it)
            continue
        else:
            assign_label_fn(it, col_ranges, col_items, n_cols, layout_id)
            continue
        if 0 <= ci < n_cols:
            col_items[ci].append(it)
    return col_items


def col_items_to_cells(
    col_items: List[List[dict]],
    row_idx: int,
    cell_text_fn,
) -> List[Optional[Cell]]:
    col_cells: List[Optional[Cell]] = [None] * len(col_items)
    for ci, items in enumerate(col_items):
        if not items:
            continue
        text = cell_text_fn(items)
        x0 = min(float(it.get("x0", 0)) for it in items)
        y0 = min(float(it.get("y0", 0)) for it in items)
        x1 = max(float(it.get("x1", 0)) for it in items)
        y1 = max(float(it.get("y1", 0)) for it in items)
        src = [str(it.get("item_index", "")) for it in items if it.get("item_index")]
        col_cells[ci] = Cell(
            text=text,
            bbox=BBox(x0, y0, x1, y1),
            row=row_idx,
            col=ci,
            source_items=src,
        )
    return col_cells


def repair_row_if_needed(
    row: dict,
    row_idx: int,
    col_items: List[List[dict]],
    col_ranges: List[Tuple[float, float]],
    n_cols: int,
    *,
    layout_id: str = "",
    value_cols: Optional[List[int]] = None,
    serial_col: Optional[int] = None,
    cell_text_fn,
    assign_label_fn,
) -> Tuple[List[List[dict]], List[Optional[Cell]], bool]:
    """若行内存在非法数值格，按 anchor 重建该行。"""
    preview = col_items_to_cells(col_items, row_idx, cell_text_fn)
    if not _row_has_illegal_value_cells(preview):
        return col_items, preview, False

    row_items = row.get("items") or []
    repaired = rebuild_row_col_items_by_anchor(
        decompose_row_items(
            row_items,
            col_ranges,
            row_idx=row_idx,
            row_phase=str(row.get("row_phase") or ""),
            value_cols=value_cols,
            include_merged_numeric=True,
        ),
        col_ranges,
        n_cols,
        layout_id=layout_id,
        value_cols=value_cols,
        serial_col=serial_col,
        assign_label_fn=assign_label_fn,
    )
    repaired = reconcile_col_items_by_anchor(
        repaired,
        col_ranges,
        layout_id=layout_id,
        value_cols=value_cols,
        serial_col=serial_col,
    )
    cells = col_items_to_cells(repaired, row_idx, cell_text_fn)
    return repaired, cells, True
