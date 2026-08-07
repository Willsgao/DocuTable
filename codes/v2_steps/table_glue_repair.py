# -*- coding: utf-8 -*-
"""数值+文本同格粘连的就地拆列修复（缓存表也能立刻修好）。

典型：`地区 营业收入` / `19,079,642 成都` → 拆成两列。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from codes.v2_steps.table_anomaly_rules import (
    _is_amount_token,
    _is_currency_amount_atomic,
    _is_glue_label_token,
    _looks_like_numeric_text_glue,
)

_DUAL_SHORT_HEADER_RE = re.compile(
    r"^([\u4e00-\u9fff]{2,10})\s+([\u4e00-\u9fff]{2,12})$"
)
# 贷款金额 贷款率%（注）/贷款率%(1) —— liteparse 常粘成一框且 bbox 只盖左列
_DUAL_METRIC_HEADER_RE = re.compile(
    r"^([\u4e00-\u9fff]{2,8}金额)"
    r"\s+"
    r"([\u4e00-\u9fff]{2,8}(?:率|比率)[%％]?(?:[（(][^）)]{0,8}[）)])?)$"
)


def split_glue_cell(text: str) -> Optional[Tuple[str, str]]:
    """拆成 (左=地区/表头1, 右=金额/表头2)。无法拆则 None。

    币种+金额连续体（人民币70,228）不拆——一体凝结核。
    """
    t = str(text or "").strip()
    if not t:
        return None

    if _is_currency_amount_atomic(t):
        return None

    m_metric = _DUAL_METRIC_HEADER_RE.match(t)
    if m_metric:
        return m_metric.group(1).strip(), m_metric.group(2).strip()

    m = _DUAL_SHORT_HEADER_RE.match(t)
    if m and " " in t:
        a, b = m.group(1).strip(), m.group(2).strip()
        # 避免把长叙述当双表头
        if len(a) + len(b) <= 18:
            return a, b

    if not _looks_like_numeric_text_glue(t):
        return None

    tokens = t.split()
    if len(tokens) >= 2:
        amounts = [tk for tk in tokens if _is_amount_token(tk)]
        labels = [tk for tk in tokens if _is_glue_label_token(tk)]
        if amounts and labels:
            # 列语义：地区在左，金额在右（与「地区|营业收入」表头一致）
            return labels[0], amounts[0]

    # 无空格：19,079,642成都
    m2 = re.match(
        r"^([\(\uff08\-]?[\d,]{4,}(?:\.\d+)?[%％]?[\)\uff09]?)"
        r"([\u4e00-\u9fff].+)$",
        t,
    )
    if m2:
        return m2.group(2).strip(), m2.group(1).strip()
    m3 = re.match(
        r"^([\u4e00-\u9fff].+?)"
        r"([\(\uff08\-]?[\d,]{4,}(?:\.\d+)?[%％]?[\)\uff09]?)$",
        t,
    )
    if m3:
        return m3.group(1).strip(), m3.group(2).strip()
    return None


def _col_needs_glue_split(data: Sequence[Sequence[Any]], col: int) -> bool:
    hits = 0
    for row in data:
        if col >= len(row):
            continue
        if split_glue_cell(str(row[col] or "")):
            hits += 1
            if hits >= 1:
                return True
    return False


def _split_column_in_data(
    data: List[List[Any]], col: int,
) -> List[List[Any]]:
    out: List[List[Any]] = []
    for row in data:
        row = list(row)
        while len(row) <= col:
            row.append("")
        cell = str(row[col] or "").strip()
        pair = split_glue_cell(cell)
        if pair:
            left, right = pair
        else:
            left, right = cell, ""
        new_row = row[:col] + [left, right] + row[col + 1 :]
        out.append(new_row)
    return out


def repair_table_percent_point_spill(table: Dict[str, Any]) -> List[str]:
    """数值+「下降…个百分点」粘在一格、右侧格为空 → 拆入下一列（不插新列）。

    典型：`1.28 下降0.09个百分点` | `` → `1.28` | `下降0.09个百分点`
    """
    from codes.table_engine.geometry.numeric import split_percent_point_change_text

    if not table or table.get("type") in ("text", "paragraph"):
        return []
    data = table.get("data")
    if not isinstance(data, list) or len(data) < 2:
        return []

    working = [list(r) if isinstance(r, list) else [] for r in data]
    n_cols = max((len(r) for r in working), default=0)
    if n_cols < 2:
        return []

    n_fixed = 0
    sample = ""
    for row in working:
        while len(row) < n_cols:
            row.append("")
        for ci in range(n_cols - 1):
            cell = str(row[ci] or "").strip()
            if not cell or "百分点" not in cell:
                continue
            pair = split_percent_point_change_text(cell)
            if not pair:
                continue
            nxt = str(row[ci + 1] or "").strip()
            if nxt:
                continue
            val, change = pair
            row[ci] = val
            row[ci + 1] = change
            n_fixed += 1
            if not sample:
                sample = f"{val} | {change}"

    if n_fixed <= 0:
        return []

    table["data"] = working
    table["cols"] = max((len(r) for r in working), default=0)
    table["rows"] = len(working)
    table.pop("_cell_source_items", None)
    table["_glue_repaired"] = True
    table.pop("_anomaly", None)
    note = (
        f"P{table.get('page', '?')} 百分点增减粘连已拆入右侧空列"
        f"（{n_fixed}格"
        + (f"：{sample}" if sample else "")
        + "）"
    )
    return [note]


def repair_table_numeric_text_glue(table: Dict[str, Any]) -> List[str]:
    """就地拆开金额+文本粘连列。返回说明列表（空=未改）。"""
    if not table or table.get("type") in ("text", "paragraph"):
        return []
    data = table.get("data")
    if not isinstance(data, list) or len(data) < 2:
        return []

    working = [list(r) if isinstance(r, list) else [] for r in data]
    notes: List[str] = []
    # 从右往左拆，避免列索引错位；最多拆 3 次防极端循环
    for _ in range(3):
        n_cols = max((len(r) for r in working), default=0)
        target = None
        for c in range(n_cols):
            if _col_needs_glue_split(working, c):
                target = c
                break
        if target is None:
            break
        sample = ""
        for row in working:
            if target < len(row):
                p = split_glue_cell(str(row[target] or ""))
                if p:
                    sample = f"{p[0]} | {p[1]}"
                    break
        working = _split_column_in_data(working, target)
        notes.append(
            f"P{table.get('page', '?')} 列{target} 数值+文本粘连已拆列"
            + (f"（{sample}）" if sample else "")
        )

    if not notes:
        return []

    table["data"] = working
    table["cols"] = max((len(r) for r in working), default=0)
    table["rows"] = len(working)
    # 源格映射失效
    table.pop("_cell_source_items", None)
    table["_glue_repaired"] = True
    # 强制后续质检重跑
    table.pop("_anomaly", None)
    return notes


def repair_tables_numeric_text_glue(
    tables: List[dict],
) -> Tuple[List[dict], List[str]]:
    notes: List[str] = []
    for t in tables or []:
        # 百分点→右侧空列须先于「插列」式金额+地区拆分
        notes.extend(repair_table_percent_point_spill(t))
        notes.extend(repair_table_numeric_text_glue(t))
    return tables, notes
