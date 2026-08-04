# -*- coding: utf-8 -*-
"""表结构不变量：数据区锁定、底层表头对齐、同列同型。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from codes.table_repair.column_roles import ColumnRoles, infer_column_roles
from codes.table_repair.validator import amounts_invented, normalize_grid

_AMOUNT_RE = re.compile(r"[\d,]{3,}|\d+\.\d+|\(\s*[\d,]+\s*\)")
_TITLE_RE = re.compile(
    r"(年度报告|半年度报告|信息披露|资本管理|第三支柱|报告$|股份有限公司)"
)
_UNIT_RE = re.compile(r"人民币|百万元|千元|单位\s*[:：]|亿元")
_DATE_RE = re.compile(r"20\d{2}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日")


@dataclass
class DataZone:
    start_row: int = 0
    end_row: int = 0
    n_cols: int = 0
    value_cols: List[int] = field(default_factory=list)
    label_col: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _cell(row: Sequence[Any], j: int) -> str:
    if j < 0 or j >= len(row):
        return ""
    return str(row[j] or "").strip()


def _row_amount_count(row: Sequence[Any]) -> int:
    return sum(1 for c in row if _AMOUNT_RE.search(str(c or "")))


def locate_data_zone(
    data: Sequence[Sequence[Any]],
    roles: Optional[ColumnRoles] = None,
) -> DataZone:
    """锁定数据区：从首个「稳定金额行」到末个金额行。"""
    grid = normalize_grid(data)
    roles = roles or infer_column_roles(grid)
    zone = DataZone(
        n_cols=roles.n_cols or max((len(r) for r in grid), default=0),
        value_cols=list(roles.value_cols),
        label_col=int(roles.primary_label_col or 0),
    )
    if not grid:
        zone.notes.append("empty")
        return zone

    starts = [
        i for i, row in enumerate(grid)
        if _row_amount_count(row) >= max(1, len(zone.value_cols) or 1)
    ]
    if not starts:
        # 放宽：至少 1 个金额
        starts = [i for i, row in enumerate(grid) if _row_amount_count(row) >= 1]
    if not starts:
        zone.notes.append("no_amount_rows")
        zone.start_row = min(3, max(len(grid) - 1, 0))
        zone.end_row = len(grid)
        return zone

    zone.start_row = starts[0]
    zone.end_row = starts[-1] + 1
    zone.notes.append(f"amount_rows={len(starts)}")
    return zone


def bottom_header_rows(
    data: Sequence[Sequence[Any]],
    zone: DataZone,
    *,
    max_header_rows: int = 6,
) -> List[List[str]]:
    """数据区之上视为表头带。"""
    grid = normalize_grid(data)
    start = max(0, zone.start_row)
    head = grid[:start]
    if len(head) > max_header_rows:
        head = head[-max_header_rows:]
    return head


def check_bottom_header_aligns_data(
    data: Sequence[Sequence[Any]],
    zone: Optional[DataZone] = None,
) -> Tuple[bool, str]:
    """最底层非空表头行的有效列布局应能覆盖数据列数。"""
    grid = normalize_grid(data)
    zone = zone or locate_data_zone(grid)
    if zone.end_row <= zone.start_row:
        return True, "无明确数据区，跳过"
    headers = grid[: zone.start_row]
    if not headers:
        return False, "无表头带"
    # 取数据区起始前最后一行非空作为「底层表头」候选
    bottom = None
    for row in reversed(headers):
        if any(str(c).strip() for c in row):
            bottom = row
            break
    if bottom is None:
        return False, "表头带全空"
    # 允许底层表头比数据短（左侧标签），但不能短太多且不能错位明显
    data_cols = zone.n_cols
    bottom_cols = len(bottom)
    if bottom_cols < max(1, data_cols - 1):
        return False, f"底层表头列数 {bottom_cols} 远小于数据列 {data_cols}"
    # 数据区众数列宽
    body = grid[zone.start_row: zone.end_row]
    if body:
        lengths = [len(r) for r in body]
        mode = max(set(lengths), key=lengths.count)
        if abs(bottom_cols - mode) >= 2:
            return False, f"底层表头列数 {bottom_cols} 与数据众数列 {mode} 不一致"
    return True, "底层表头与数据列基本对齐"


def check_column_homogeneity(
    data: Sequence[Sequence[Any]],
    zone: Optional[DataZone] = None,
    *,
    min_rows: int = 3,
) -> Tuple[bool, str]:
    """数值列在数据区内应以金额为主。"""
    grid = normalize_grid(data)
    zone = zone or locate_data_zone(grid)
    body = grid[zone.start_row: zone.end_row]
    if len(body) < min_rows:
        return True, "数据行不足，跳过"
    value_cols = zone.value_cols or list(range(max(zone.n_cols - 2, 1), zone.n_cols))
    for j in value_cols:
        amt = 0
        text = 0
        for row in body:
            t = _cell(row, j)
            if not t:
                continue
            if _AMOUNT_RE.search(t) and not re.search(r"[\u4e00-\u9fff]{3,}", t):
                amt += 1
            elif re.search(r"[\u4e00-\u9fff]{2,}", t):
                text += 1
        if amt + text >= min_rows and text > amt:
            return False, f"数值列 {j} 文本多于金额（同列同型破坏）"
    return True, "同列同型基本成立"


def find_title_row_indices(data: Sequence[Sequence[Any]], zone: Optional[DataZone] = None) -> List[int]:
    """数据区之上疑似标题行。

    注意：标题中常含年份（如 2024），会被宽松金额正则命中，故标题关键词优先于金额计数。
    """
    grid = normalize_grid(data)
    zone = zone or locate_data_zone(grid)
    hits: List[int] = []
    for i, row in enumerate(grid[: zone.start_row]):
        joined = "".join(str(c or "") for c in row)
        nonempty = [str(c).strip() for c in row if str(c).strip()]
        if not nonempty:
            continue
        # 关键词命中：即使格内有年份数字也算标题
        if _TITLE_RE.search(joined) and len(nonempty) <= 2:
            hits.append(i)
            continue
        if len(nonempty) == 1 and len(nonempty[0]) >= 8 and _TITLE_RE.search(nonempty[0]):
            hits.append(i)
            continue
        # 无关键词时，多金额行不当标题
        if _row_amount_count(row) > 0:
            continue
    return hits


def strip_title_rows(data: Sequence[Sequence[Any]]) -> Tuple[List[List[str]], List[str], List[str]]:
    """规则剔除标题行。返回 (新表, 剔除文本, notes)。"""
    grid = normalize_grid(data)
    zone = locate_data_zone(grid)
    title_idx = set(find_title_row_indices(grid, zone))
    if not title_idx:
        return grid, [], []
    kept: List[List[str]] = []
    removed: List[str] = []
    for i, row in enumerate(grid):
        if i in title_idx:
            removed.append(" ".join(c for c in row if c).strip())
        else:
            kept.append(row)
    notes = [f"剔除标题行 {sorted(title_idx)}: {r[:40]}" for r in removed]
    return kept, removed, notes


def validate_structure_invariants(
    before: Sequence[Sequence[Any]],
    after: Sequence[Sequence[Any]],
) -> Tuple[bool, List[str]]:
    """修复后结构验收 + 禁补数。"""
    reasons: List[str] = []
    after_g = normalize_grid(after)
    if not after_g:
        return False, ["修复结果为空"]

    inv = amounts_invented(before, after_g)
    if inv:
        reasons.append("疑似空造金额: " + ", ".join(inv[:6]))

    zone = locate_data_zone(after_g)
    ok_align, msg_a = check_bottom_header_aligns_data(after_g, zone)
    if not ok_align:
        reasons.append("表头对齐: " + msg_a)
    ok_homo, msg_h = check_column_homogeneity(after_g, zone)
    if not ok_homo:
        reasons.append("同列同型: " + msg_h)

    return (len(reasons) == 0), reasons
