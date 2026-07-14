# -*- coding: utf-8
"""layout / grid 共用的行带筛选。"""

from __future__ import annotations

from typing import List

from codes.table_engine.geometry.numeric import is_numeric_data_cell

_MAX_ITEM_WIDTH_FOR_COL_CLUSTER = 160.0


def is_poison_row_for_layout(row: dict) -> bool:
    """脚注/长说明行：跨列极宽，不可参与列界聚类。"""
    items = row.get("items") or []
    w = 0.0
    for it in items:
        w = max(w, float(it.get("x1", 0)) - float(it.get("x0", 0)))
    if w > _MAX_ITEM_WIDTH_FOR_COL_CLUSTER:
        return True
    # 表体数据行（左侧序号/标签 + 右侧金额列）不以「行首 1/2 + 长文本」判为 poison
    if any(
        is_numeric_data_cell(str(it.get("text", "")).strip())
        and float(it.get("x0", 0)) > 200
        for it in items
    ):
        return False
    text = " ".join(
        str(it.get("text", "")).strip() for it in items
    ).strip()
    if len(text) > 35 and text[:1] in "12（(":
        return True
    if text.startswith("注") and len(text) > 10:
        return True
    return False


def body_rows_for_layout(rows: List[dict]) -> List[dict]:
    tagged = [
        r for r in rows
        if r.get("row_phase") == "body" and not is_poison_row_for_layout(r)
    ]
    if tagged:
        return tagged
    out: List[dict] = []
    for row in rows:
        if is_poison_row_for_layout(row):
            continue
        items = row.get("items") or []
        if any(
            is_numeric_data_cell(str(it.get("text", "")).strip())
            and float(it.get("x0", 0)) > 200
            for it in items
        ):
            out.append(row)
    return out if out else [r for r in rows if not is_poison_row_for_layout(r)]
