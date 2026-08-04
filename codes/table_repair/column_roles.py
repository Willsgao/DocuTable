# -*- coding: utf-8 -*-
"""列角色：序号列 / 标签列区 / 数值列区（标签列不一定是第 0 列）。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Sequence

_AMOUNT_RE = re.compile(r"[\d,]{3,}|\d+\.\d+|\(\s*[\d,]+\s*\)")
_SERIAL_RE = re.compile(
    r"^[(（]?\d{1,3}[)）.．、]?$|^[一二三四五六七八九十]{1,3}[、.．]?$"
)
_HEADER_YEAR_RE = re.compile(r"20\d{2}|\d{4}\s*年|12\s*月|金额|余额|比例|比率|%|％")


@dataclass
class ColumnRoles:
    n_cols: int = 0
    serial_cols: List[int] = field(default_factory=list)
    label_cols: List[int] = field(default_factory=list)
    value_cols: List[int] = field(default_factory=list)
    primary_label_col: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _cell(row: Sequence[Any], j: int) -> str:
    if j < 0 or j >= len(row):
        return ""
    return str(row[j] or "").strip()


def _col_stats(data: Sequence[Sequence[Any]], j: int) -> Dict[str, float]:
    texts = 0
    amounts = 0
    serials = 0
    nonempty = 0
    for row in data:
        if not isinstance(row, (list, tuple)):
            continue
        t = _cell(row, j)
        if not t:
            continue
        nonempty += 1
        if _AMOUNT_RE.search(t) and not re.search(r"[\u4e00-\u9fff]{2,}", t):
            amounts += 1
        elif _SERIAL_RE.match(t):
            serials += 1
        else:
            texts += 1
    return {
        "nonempty": float(nonempty),
        "amount_ratio": amounts / max(nonempty, 1),
        "serial_ratio": serials / max(nonempty, 1),
        "text_ratio": texts / max(nonempty, 1),
    }


def infer_column_roles(data: Sequence[Sequence[Any]]) -> ColumnRoles:
    """从网格推断列角色。"""
    roles = ColumnRoles()
    if not data:
        roles.notes.append("empty_table")
        return roles

    n_cols = max((len(r) for r in data if isinstance(r, (list, tuple))), default=0)
    roles.n_cols = n_cols
    if n_cols == 0:
        roles.notes.append("no_cols")
        return roles

    # 跳过可能的表头行做统计（前 1～3 行若含年份词则跳过）
    body = list(data)
    skip = 0
    for i, row in enumerate(data[:3]):
        joined = " ".join(_cell(row, j) for j in range(n_cols))
        if _HEADER_YEAR_RE.search(joined) and not _AMOUNT_RE.search(joined):
            skip = i + 1
    if skip:
        body = data[skip:]
        roles.notes.append(f"stats_skip_header_rows={skip}")

    stats = [_col_stats(body, j) for j in range(n_cols)]

    # 序号列：靠左、serial_ratio 高、列窄内容短
    for j in range(min(2, n_cols)):
        s = stats[j]
        if s["nonempty"] >= 3 and s["serial_ratio"] >= 0.55 and s["amount_ratio"] < 0.2:
            roles.serial_cols.append(j)

    # 数值列：amount_ratio 高
    for j in range(n_cols):
        s = stats[j]
        if s["nonempty"] >= 2 and s["amount_ratio"] >= 0.45:
            roles.value_cols.append(j)

    # 若表头行暗示数值列
    if not roles.value_cols:
        for j in range(n_cols):
            header_hits = 0
            for row in data[: min(4, len(data))]:
                if _HEADER_YEAR_RE.search(_cell(row, j)):
                    header_hits += 1
            if header_hits >= 1 and stats[j]["amount_ratio"] >= 0.25:
                roles.value_cols.append(j)

    # 标签列：非序号、非数值，从左连续文本列
    for j in range(n_cols):
        if j in roles.serial_cols or j in roles.value_cols:
            continue
        s = stats[j]
        if s["text_ratio"] >= 0.35 or (s["nonempty"] >= 2 and s["amount_ratio"] < 0.25):
            roles.label_cols.append(j)

    # 若仍空：默认序号后第一列，或 0
    if not roles.label_cols:
        start = (max(roles.serial_cols) + 1) if roles.serial_cols else 0
        if start < n_cols:
            roles.label_cols = [start]
            roles.notes.append("fallback_label_col")
        else:
            roles.label_cols = [0]
            roles.notes.append("fallback_label_col_0")

    # 主标签列：标签区最左，或文本最多的列
    roles.primary_label_col = roles.label_cols[0]
    best_j = roles.primary_label_col
    best_score = -1.0
    for j in roles.label_cols:
        s = stats[j]
        score = s["text_ratio"] * 10 + s["nonempty"]
        if score > best_score:
            best_score = score
            best_j = j
    roles.primary_label_col = best_j

    # 数值列仍空：右侧列猜
    if not roles.value_cols and n_cols >= 2:
        for j in range(n_cols - 1, -1, -1):
            if j not in roles.label_cols and j not in roles.serial_cols:
                roles.value_cols.append(j)
                break
        roles.notes.append("fallback_value_cols")

    return roles
