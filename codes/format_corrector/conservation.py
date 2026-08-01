# -*- coding: utf-8 -*-
"""守恒校验：顺序不乱、非空内容不丢。"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Optional, Sequence, Tuple


_WS_RE = re.compile(r"\s+")


def normalize_cell(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def cell_key(val) -> str:
    """比较用：去空白后的文本。空串不参与多重集合。"""
    s = normalize_cell(val)
    if not s:
        return ""
    return _WS_RE.sub("", s)


def nonempty_multiset(data: Sequence[Sequence]) -> Counter:
    c: Counter = Counter()
    for row in data or []:
        for cell in row or []:
            k = cell_key(cell)
            if k:
                c[k] += 1
    return c


def row_multiset(row: Sequence) -> Counter:
    c: Counter = Counter()
    for cell in row or []:
        k = cell_key(cell)
        if k:
            c[k] += 1
    return c


def assert_no_content_loss(
    before: Sequence[Sequence],
    after: Sequence[Sequence],
    *,
    allowed_remove: Optional[Counter] = None,
) -> Tuple[bool, str]:
    """校验 after 相对 before 不丢非空内容。

    allowed_remove: 允许减少的文本多重集合（仅用于「跨页重复表头行」白名单）。
    """
    b = nonempty_multiset(before)
    a = nonempty_multiset(after)
    if allowed_remove:
        for k, n in allowed_remove.items():
            b[k] = max(0, b.get(k, 0) - n)
        b = Counter({k: v for k, v in b.items() if v > 0})

    missing = a.copy()
    missing.subtract(b)
    # missing 中负数 = after 比 before 少
    lost = {k: -v for k, v in missing.items() if v < 0}
    if lost:
        sample = list(lost.items())[:8]
        return False, f"内容丢失: {sample}"
    return True, "ok"


def headers_equal(rows_a: Sequence[Sequence], rows_b: Sequence[Sequence]) -> bool:
    """判断两段表头行是否同源（按去空白单元格多重集合逐行比）。"""
    if not rows_a or not rows_b:
        return False
    if len(rows_a) != len(rows_b):
        # 允许后表只有一行表头对应前表多行中的某一行
        if len(rows_b) == 1:
            rb = row_multiset(rows_b[0])
            return any(row_multiset(r) == rb and rb for r in rows_a)
        return False
    for ra, rb in zip(rows_a, rows_b):
        ca, cb = row_multiset(ra), row_multiset(rb)
        if not ca or ca != cb:
            return False
    return True


def detect_duplicate_header_prefix(
    prev_data: Sequence[Sequence],
    next_data: Sequence[Sequence],
    max_header_rows: int = 3,
) -> Tuple[int, Counter]:
    """检测 next 表开头有多少行是与 prev 表头重复的表头。

    Returns:
        (skip_rows, allowed_remove_multiset)
        skip_rows=0 表示不能安全跳过。
    """
    if not prev_data or not next_data:
        return 0, Counter()

    # 从前表取候选表头：前 max_header_rows 行中「非纯数字主导」的行
    prev_header_candidates: List[List] = []
    for i, row in enumerate(prev_data[:max_header_rows]):
        prev_header_candidates.append(list(row))

    best_skip = 0
    best_remove: Counter = Counter()
    for n in range(1, min(max_header_rows, len(next_data)) + 1):
        prefix = [list(r) for r in next_data[:n]]
        # 与前表前 n 行比，或与前表任意连续表头候选比
        matched = False
        if headers_equal(prev_header_candidates[:n], prefix):
            matched = True
        elif n == 1 and any(
            row_multiset(r) == row_multiset(prefix[0]) and row_multiset(prefix[0])
            for r in prev_header_candidates
        ):
            matched = True
        if matched:
            remove = Counter()
            for row in prefix:
                remove.update(row_multiset(row))
            best_skip = n
            best_remove = remove
    return best_skip, best_remove


def pad_row(row: Sequence, n_cols: int) -> List:
    cells = [normalize_cell(c) if c is not None else "" for c in (row or [])]
    if len(cells) < n_cols:
        cells = cells + [""] * (n_cols - len(cells))
    # 多出的列必须保留（不截断）
    return cells


def merge_tables_preserve(
    prev_data: Sequence[Sequence],
    next_data: Sequence[Sequence],
) -> Tuple[List[List], Counter, int, str]:
    """按顺序拼接两表，仅在可证明重复表头时跳过 next 表头行。

    Returns:
        (merged_data, allowed_remove, skipped_header_rows, note)
    """
    skip, allowed = detect_duplicate_header_prefix(prev_data, next_data)
    next_body = list(next_data[skip:]) if skip else list(next_data)

    n_cols = 0
    for row in list(prev_data) + next_body:
        n_cols = max(n_cols, len(row or []))

    merged: List[List] = []
    for row in prev_data:
        merged.append(pad_row(row, n_cols))
    for row in next_body:
        merged.append(pad_row(row, n_cols))

    note = (
        f"跳过后表重复表头 {skip} 行"
        if skip
        else "未跳过表头（无法证明重复，完整保留）"
    )
    return merged, allowed, skip, note


def count_consecutive_empty_rows(data: Sequence[Sequence]) -> List[Tuple[int, int]]:
    """返回连续空行区间 [(start, end), ...]（含 end）。"""
    ranges = []
    start = None
    for i, row in enumerate(data or []):
        empty = all(not normalize_cell(c) for c in (row or []))
        if empty:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= 2:
                ranges.append((start, i - 1))
            start = None
    if start is not None and len(data) - start >= 2:
        ranges.append((start, len(data) - 1))
    return ranges


def count_empty_columns(data: Sequence[Sequence]) -> List[int]:
    """返回整列为空的列索引。"""
    if not data:
        return []
    n_cols = max((len(r) for r in data), default=0)
    empty_cols = []
    for c in range(n_cols):
        if all(not normalize_cell((row[c] if c < len(row) else "")) for row in data):
            empty_cols.append(c)
    return empty_cols


def _cell_is_numeric_like(text: str) -> bool:
    s = normalize_cell(text)
    if not s:
        return False
    t = (
        s.replace(",", "")
        .replace("%", "")
        .replace("(", "")
        .replace(")", "")
        .replace("（", "")
        .replace("）", "")
        .replace("−", "-")
        .strip()
    )
    if t in ("-", "–", "—", "－"):
        return True
    try:
        float(t)
        return True
    except ValueError:
        return False


def looks_like_header_row(row: Sequence) -> bool:
    """粗判表头行：非空单元格多、纯数字少。"""
    cells = [normalize_cell(c) for c in (row or []) if normalize_cell(c)]
    if not cells:
        return False
    num_like = sum(1 for s in cells if _cell_is_numeric_like(s))
    return num_like / len(cells) < 0.5 and len(cells) >= 2


def _row_is_stage_column_header(cells: Sequence[str]) -> bool:
    """阶段一/二/三（可含合计）列表头行。"""
    try:
        from codes.table_engine.geometry.column_anchors import is_stage_column_header_text
    except Exception:
        is_stage_column_header_text = None  # type: ignore
    if is_stage_column_header_text is None:
        markers = ("阶段一", "阶段二", "阶段三")
        return sum(1 for c in cells if any(m in c for m in markers)) >= 2
    stages = sum(1 for c in cells if is_stage_column_header_text(c))
    return stages >= 2


def _row_is_report_period_header(cells: Sequence[str]) -> bool:
    """报告期行：如「2023年12月31日」（可仅一格跨列）。"""
    if not cells:
        return False
    try:
        from codes.table_engine.geometry.numeric import is_report_date_cell, is_year_cell
    except Exception:
        is_report_date_cell = is_year_cell = None  # type: ignore
    ok = 0
    for c in cells:
        if is_report_date_cell and is_report_date_cell(c):
            ok += 1
        elif is_year_cell and is_year_cell(c):
            ok += 1
        elif re.search(r"(?:19|20)\d{2}\s*年", c) and ("月" in c or "日" in c or len(c) <= 12):
            ok += 1
    return ok >= 1 and ok == len(cells)


def _row_is_unit_header(cells: Sequence[str]) -> bool:
    if len(cells) != 1:
        return False
    t = cells[0]
    return t.startswith("单位") or t.startswith("（人民币") or t.startswith("(人民币")


def _row_is_data_body_row(row: Sequence) -> bool:
    """数据行：含足够数值锚点，且不是报告期/阶段列表头。"""
    cells = [normalize_cell(c) for c in (row or []) if normalize_cell(c)]
    if not cells:
        return False
    if _row_is_report_period_header(cells) or _row_is_stage_column_header(cells):
        return False
    if _row_is_unit_header(cells):
        return False
    num_like = sum(1 for s in cells if _cell_is_numeric_like(s))
    if num_like >= 2:
        return True
    # 标签 + 至少 1 个金额（常见指标行）；排除纯列标行
    if num_like >= 1 and any(re.search(r"[\u4e00-\u9fff]{2,}", c) for c in cells):
        if looks_like_header_row(row) and num_like / len(cells) < 0.25:
            return False
        return True
    return False


def find_first_data_row_index(data: Sequence[Sequence], *, scan: int = 12) -> int:
    """第一行数据行下标；找不到返回 -1。"""
    if not data:
        return -1
    for i, row in enumerate(list(data)[:scan]):
        if isinstance(row, list) and _row_is_data_body_row(row):
            return i
    return -1


def _row_is_header_content(row: Sequence) -> bool:
    """位于数据行上方时，是否像表头内容（报告期/列标/单位等）。"""
    cells = [normalize_cell(c) for c in (row or []) if normalize_cell(c)]
    if not cells:
        return False
    if _row_is_report_period_header(cells):
        return True
    if _row_is_stage_column_header(cells):
        return True
    if _row_is_unit_header(cells):
        return True
    try:
        from codes.table_engine.scope.header_scope import is_annual_report_column_header_row

        if is_annual_report_column_header_row(cells):
            return True
    except Exception:
        pass
    if looks_like_header_row(row):
        return True
    # 单格短中文列组说明（如仅「公允价值」）
    joined = "".join(cells)
    if len(cells) == 1 and 2 <= len(joined) <= 16 and re.search(r"[\u4e00-\u9fff]", joined):
        if not _cell_is_numeric_like(joined) and "。" not in joined:
            return True
    return False


def table_has_own_column_header(table: dict, *, scan: int = 12) -> bool:
    """是否自带表头：先找数据行，再看其上方是否有表头带。

    比「扫前几行关键词」稳——报告期单独一行、阶段一/二/三 等都算表头。
    """
    data = table.get("data") or []
    if not isinstance(data, list) or not data:
        return False

    data_start = find_first_data_row_index(data, scan=scan)
    if data_start < 0:
        # 找不到数据行时，退回：前几行是否像列头/报告期
        for row in data[: min(scan, len(data))]:
            if isinstance(row, list) and _row_is_header_content(row):
                return True
        return False
    if data_start == 0:
        return False  # 表体顶格开始 → 无上方表头

    header_band = [r for r in data[:data_start] if isinstance(r, list)]
    if any(_row_is_header_content(r) for r in header_band):
        return True
    # 上方有非空、非数值噪声行，也视为自带表头结构
    for row in header_band:
        cells = [normalize_cell(c) for c in (row or []) if normalize_cell(c)]
        if cells and not all(_cell_is_numeric_like(c) for c in cells):
            return True
    return False


def table_starts_with_subsection_caption(table: dict) -> bool:
    """首行是否为（四）（五）类新表小节——新表起点，绝非前表续表。"""
    data = table.get("data") or []
    if not data:
        return False
    try:
        from codes.table_engine.split.row_classify import row_is_annual_subsection_caption_row

        return row_is_annual_subsection_caption_row(list(data[0]))
    except Exception:
        cells = [normalize_cell(c) for c in (data[0] or []) if normalize_cell(c)]
        if not cells:
            return False
        import re

        return bool(
            re.match(
                r"^[（(][一二三四五六七八九十\d]+[)）]\s*[\u4e00-\u9fff]",
                cells[0],
            )
        )


def table_missing_header(table: dict) -> bool:
    """是否缺表头（用于跨页续表召回）。

    主信号：先定位数据行；若数据行上方已有表头带 → 不缺表头。
    避免被 anomaly/类别上的旧「缺表头」标签误导。
    """
    if table_has_own_column_header(table):
        return False
    if table_starts_with_subsection_caption(table):
        # 小节后通常紧跟单位/列头；即使 anomaly 标了缺表头也不当续表
        return False

    data = table.get("data") or []
    if isinstance(data, list) and data:
        data_start = find_first_data_row_index(data)
        # 表体顶格、上方无任何表头内容 → 结构上缺表头
        if data_start == 0:
            return True

    anomaly = table.get("_anomaly") or {}
    if anomaly.get("header_missing"):
        return True
    if table.get("quality_decision") == "header_missing":
        return True
    cat = str(table.get("table_category") or "")
    if "缺表头" in cat:
        return True
    if not data:
        return False
    return not looks_like_header_row(data[0])
