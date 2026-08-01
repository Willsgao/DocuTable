# -*- coding: utf-8 -*-
"""表格质检：正常表契约校验 —— 不满足契约即异常表。

正常表契约：
  C1  表头区 = 第一行数据之上的所有行；列上有任一格非空文本（含报告期）即算有表头
  C2  数据区每行须为：数据行 / 合法折行 / 小节行
  C3  数值列性质一致；纯文本列仅长度差且邻列无空白 → 不检查

预处理：全空白行、全空白列先删除再质检。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

# ---- 结构类违规（错位/粘连/丢失）----
# R01  orphan_extension
# R02  merged_in_short_col
# R03  stacked_long_text
# R04  merged_numeric
# R05  text_in_numeric
# R06  ghost_column
# R07  word_crosses_columns
# R08  header_data_misalign
# R09  interior_singleton
# R10  numeric_text_glue  （金额+文本同格，重点严格审核）

# ---- 契约类违规 ----
# C01  missing_header / no_header_band  → 单独归类 missing_header，非质量异常
# C02  unrecognized_data_row
# C03  column_type_violation
# C04  incomplete_data_row

HEADER_MISSING_RULE_IDS = frozenset({"C01_missing_header", "C01_no_header_band"})
ANOMALY_CLASS_NONE = "none"
ANOMALY_CLASS_MISSING_HEADER = "missing_header"
ANOMALY_CLASS_QUALITY = "quality"

_PLACEHOLDERS = frozenset({
    "-", "--", "---", "\u2014", "\u2014\u2014", "/", "\\",
    "\u2026", "...", "N/A", "n/a", "NA", "na",
})
_NUM_TOKEN_RE = re.compile(r"^[\d,.\-()（）%％]+$")
# 金额型：千分位，或 ≥4 位数字（含可选小数/百分号/括号负号）
_AMOUNT_TOKEN_RE = re.compile(
    r"^[\(（\-]?"
    r"(?:"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d{4,}(?:\.\d+)?"
    r")"
    r"[%％]?"
    r"[\)）]?$"
)
# 无空格粘连：金额紧贴汉字/字母
_AMOUNT_GLUED_TEXT_RE = re.compile(
    r"^[\(（\-]?[\d,]{4,}(?:\.\d+)?[%％]?[\)）]?"
    r"[\u4e00-\u9fffA-Za-z].+"
    r"|"
    r"^[\u4e00-\u9fffA-Za-z].+?"
    r"[\(（\-]?[\d,]{4,}(?:\.\d+)?[%％]?[\)）]?$"
)
_CJK_OR_ALPHA_RE = re.compile(r"[\u4e00-\u9fffA-Za-z]")
_INDEX_PREFIX_RE = re.compile(r"^\s*\d{1,4}\s+\S")
_SHORT_INDEX_RE = re.compile(r"^\d{1,4}$")
_SHORT_CODE_MAX_LEN = 3
_NUMERIC_COL_SHORT_TEXT_MAX_RATIO = 0.12
_COL_NUMERIC_DOMINANT = 0.6
_STRICT_REVIEW_RULE_IDS = frozenset({"R10_numeric_text_glue"})
_COL_TEXT_DOMINANT = 0.6
_VALUE_COL_FILL_EXPECT = 0.65
_PERIOD_YEAR_RE = re.compile(r"(?:19|20)\d{2}\s*年")
_REPORT_PERIOD_MARKERS = (
    "报告期", "报告期末", "报告期内", "本报告期", "比上年同期", "末增减",
)


def _column_has_data(
    table: Sequence[Sequence[str]], ctx: TableContext, c: int,
) -> bool:
    """数据列：数据区至少有一格非空。"""
    return any(
        _cell_text(table, r, c)
        for r in range(ctx.data_start, len(table))
    )


def _column_has_header(
    table: Sequence[Sequence[str]], ctx: TableContext, c: int,
) -> bool:
    """有表头：表头区（第一行数据之上）该列至少有一格非空文本（含报告期/年份）。"""
    for r in range(ctx.data_start):
        if _cell_text(table, r, c):
            return True
    return False


def strip_blank_rows_cols(table_data: List[List[str]]) -> List[List[str]]:
    """删除全空白行、全空白列。"""
    if not table_data:
        return table_data

    n_cols = max((len(r) for r in table_data), default=0)
    norm = [
        [str(row[c] or "").strip() if c < len(row) else "" for c in range(n_cols)]
        for row in table_data
    ]
    kept_rows = [row for row in norm if any(cell for cell in row)]
    if not kept_rows:
        return []

    n_cols = max(len(r) for r in kept_rows)
    kept_cols = [
        c for c in range(n_cols)
        if any(kept_rows[r][c] for r in range(len(kept_rows)))
    ]
    if not kept_cols:
        return []

    return [[row[c] for c in kept_cols] for row in kept_rows]


def _is_pure_text_column(ctx: TableContext, c: int) -> bool:
    """纯文本列：非数值列，且列主导类型为 text。"""
    if c in ctx.value_cols:
        return False
    return ctx.col_dominant.get(c) == "text"


def _row_neighbors_have_blank(
    table: Sequence[Sequence[str]], r: int, c: int, n_cols: int,
) -> bool:
    """同行左或右邻列存在空白（列存在但无内容）。"""
    if c > 0 and not _cell_text(table, r, c - 1):
        return True
    if c + 1 < n_cols and not _cell_text(table, r, c + 1):
        return True
    return False


def _skip_text_length_check(
    table: Sequence[Sequence[str]], ctx: TableContext, r: int, c: int,
) -> bool:
    """纯文本列仅长度差异、邻列无空白 → 不检查。"""
    if not _is_pure_text_column(ctx, c):
        return False
    return not _row_neighbors_have_blank(table, r, c, ctx.n_cols)

@dataclass
class ColumnStats:
    col: int
    numeric_ratio: float = 0.0
    text_ratio: float = 0.0
    median_len: float = 0.0
    non_empty: int = 0
    index_ratio: float = 0.0


@dataclass
class TableContext:
    header_band: int = 1
    data_start: int = 1
    n_cols: int = 0
    serial_col: Optional[int] = None
    short_text_col: Optional[int] = None
    long_text_col: Optional[int] = None
    label_cols: List[int] = field(default_factory=list)
    value_cols: List[int] = field(default_factory=list)
    data_cols: List[int] = field(default_factory=list)
    header_exempt_cols: Set[int] = field(default_factory=set)
    stats: List[ColumnStats] = field(default_factory=list)
    col_dominant: Dict[int, str] = field(default_factory=dict)
    value_col_fill: Dict[int, float] = field(default_factory=dict)
    wrap_rows: Set[int] = field(default_factory=set)
    structure_rows: Set[int] = field(default_factory=set)
    report_period_rows: Set[int] = field(default_factory=set)


@dataclass
class TableIssue:
    rule_id: str
    row: int
    col: int
    message: str
    snippet: str = ""


def _is_report_period_text(text: str) -> bool:
    """报告期/年份列标等，视为合法表头内容。"""
    t = str(text or "").strip()
    if not t:
        return False
    if any(m in t for m in _REPORT_PERIOD_MARKERS):
        return True
    if _PERIOD_YEAR_RE.search(t):
        return True
    try:
        from codes.table_engine.geometry.numeric import is_report_date_cell
        return is_report_date_cell(t)
    except ImportError:
        return False


def _is_report_period_row(
    table: Sequence[Sequence[str]], ctx: TableContext, r: int,
) -> bool:
    """整行均为报告期/表头说明（如 2024年、报告期末…）。"""
    cells = [_cell_text(table, r, c) for c in range(ctx.n_cols) if _cell_text(table, r, c)]
    if not cells:
        return False
    if all(_is_report_period_text(c) for c in cells):
        return True
    # 单行说明：仅一格非空且含报告期/报告期末
    if len(cells) == 1 and _is_report_period_text(cells[0]):
        return True
    return False



def _cell_text(table: Sequence[Sequence[str]], r: int, c: int) -> str:
    if r < 0 or r >= len(table):
        return ""
    row = table[r]
    if c >= len(row):
        return ""
    return str(row[c] or "").strip()


def _cell_type(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return "empty"
    if t in _PLACEHOLDERS:
        return "placeholder"
    cleaned = t.replace(",", "").replace("(", "-").replace(")", "")
    digit_count = sum(1 for ch in cleaned if ch.isdigit())
    alpha_count = sum(1 for ch in t if ch.isalpha() or "\u4e00" <= ch <= "\u9fff")
    total = len(t)
    if digit_count / total >= 0.5 and alpha_count == 0:
        return "numeric"
    if alpha_count / total >= 0.3:
        return "text"
    if digit_count > 0 and alpha_count > 0:
        return "mixed"
    return "text"


def _row_has_data_anchor_signal(
    table: Sequence[Sequence[str]], r: int, n_cols: int,
) -> bool:
    """行是否呈现「数据行」特征（序号 + 实数数据；报告期/年份列标不算）。"""
    serial0 = _cell_text(table, r, 0)
    if serial0 and _SHORT_INDEX_RE.match(serial0):
        return True
    data_numeric = texts = 0
    for c in range(n_cols):
        t = _cell_text(table, r, c)
        if not t:
            continue
        if _is_report_period_text(t):
            continue
        ct = _cell_type(t)
        if ct == "numeric" and (len(t) >= 3 or "," in t):
            data_numeric += 1
        if ct == "text" and c > 0 and len(t) > 4:
            texts += 1
    if texts >= 1 and data_numeric >= 1:
        return True
    if data_numeric >= 2:
        return True
    return False


def _detect_data_start(table: Sequence[Sequence[str]]) -> int:
    """第一行数据行索引；其上方全部视为表头区。"""
    if len(table) < 2:
        return 0
    n_cols = len(table[0])
    for r in range(len(table)):
        if _row_has_data_anchor_signal(table, r, n_cols):
            return r
    return 1


def _column_stats(table: Sequence[Sequence[str]], c: int, data_start: int) -> ColumnStats:
    lens: List[int] = []
    numeric = text = index_hits = 0
    non_empty = 0
    for r in range(data_start, len(table)):
        t = _cell_text(table, r, c)
        if not t:
            continue
        non_empty += 1
        lens.append(len(t))
        ct = _cell_type(t)
        if ct == "numeric":
            numeric += 1
        if ct in ("text", "mixed"):
            text += 1
        if _SHORT_INDEX_RE.match(t):
            index_hits += 1
    st = ColumnStats(col=c, non_empty=non_empty)
    if non_empty:
        st.numeric_ratio = numeric / non_empty
        st.text_ratio = text / non_empty
        st.index_ratio = index_hits / non_empty
        st.median_len = float(statistics.median(lens))
    return st


def _build_context(table: Sequence[Sequence[str]]) -> TableContext:
    ctx = TableContext()
    if len(table) < 2 or not table[0]:
        return ctx

    ctx.n_cols = len(table[0])
    ctx.data_start = _detect_data_start(table)
    ctx.header_band = ctx.data_start
    ctx.stats = [_column_stats(table, c, ctx.data_start) for c in range(ctx.n_cols)]

    for st in ctx.stats:
        if st.non_empty >= 2 and st.index_ratio >= 0.45 and st.median_len <= 6:
            ctx.serial_col = st.col
            break

    ctx.value_cols = [
        st.col for st in ctx.stats
        if st.col != ctx.serial_col
        and st.non_empty >= 2
        and st.numeric_ratio >= 0.7
    ]
    value_set = set(ctx.value_cols)

    text_stats = [
        st for st in ctx.stats
        if st.col not in value_set
        and st.col != ctx.serial_col
        and st.non_empty >= 2
        and st.text_ratio >= 0.5
        and st.numeric_ratio < 0.5
    ]
    text_stats.sort(key=lambda s: s.median_len)
    if len(text_stats) >= 2:
        ctx.short_text_col = text_stats[0].col
        ctx.long_text_col = text_stats[-1].col
        if ctx.short_text_col == ctx.long_text_col:
            ctx.long_text_col = None
    elif len(text_stats) == 1:
        ctx.short_text_col = text_stats[0].col

    ctx.label_cols = [
        c for c in (
            [ctx.serial_col] if ctx.serial_col is not None else []
        ) + [ctx.short_text_col, ctx.long_text_col]
        if c is not None
    ]
    ctx.label_cols = sorted(set(ctx.label_cols))

    ctx.data_cols = [
        c for c in range(ctx.n_cols)
        if c not in ctx.label_cols or c in ctx.value_cols
    ]
    if not ctx.data_cols:
        ctx.data_cols = [c for c in range(ctx.n_cols) if c not in {ctx.serial_col}]

    ctx.header_exempt_cols = set()
    if ctx.serial_col == 0:
        ctx.header_exempt_cols.add(0)
    if ctx.short_text_col is not None and ctx.short_text_col <= 1:
        ctx.header_exempt_cols.add(ctx.short_text_col)

    _classify_special_rows(table, ctx)
    _finalize_column_profiles(table, ctx)
    return ctx


def _dominant_col_type(st: ColumnStats) -> str:
    if st.non_empty < 2:
        return "sparse"
    if st.numeric_ratio >= _COL_NUMERIC_DOMINANT:
        return "numeric"
    if st.text_ratio >= _COL_TEXT_DOMINANT:
        return "text"
    return "mixed"


def _finalize_column_profiles(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> None:
    data_rows = max(len(table) - ctx.data_start, 1)
    for st in ctx.stats:
        ctx.col_dominant[st.col] = _dominant_col_type(st)
        filled = sum(
            1 for r in range(ctx.data_start, len(table))
            if _cell_text(table, r, st.col)
        )
        ctx.value_col_fill[st.col] = filled / data_rows


def _row_has_value_data(table: Sequence[Sequence[str]], ctx: TableContext, r: int) -> bool:
    for c in ctx.value_cols:
        if _cell_text(table, r, c):
            return True
    return False


def _row_label_text(table: Sequence[Sequence[str]], ctx: TableContext, r: int) -> bool:
    for c in ctx.label_cols:
        if _cell_text(table, r, c):
            return True
    return False


def _classify_special_rows(table: Sequence[Sequence[str]], ctx: TableContext) -> None:
    """标记折行续行、小节行、报告期/表头说明行。"""
    for r in range(ctx.data_start, len(table)):
        if _is_report_period_row(table, ctx, r):
            ctx.report_period_rows.add(r)
            continue
        if _is_structure_row(table, ctx, r):
            ctx.structure_rows.add(r)
            continue
        if _is_label_wrap_row(table, ctx, r):
            ctx.wrap_rows.add(r)


def _is_structure_row(table: Sequence[Sequence[str]], ctx: TableContext, r: int) -> bool:
    """左列有文本、所有数值列空 → 小节/标题行（正常）。"""
    if not ctx.value_cols:
        return False
    if _row_has_value_data(table, ctx, r):
        return False
    if not _row_label_text(table, ctx, r):
        return False
    non_label_filled = any(
        _cell_text(table, r, c)
        for c in range(ctx.n_cols)
        if c not in ctx.label_cols
    )
    return not non_label_filled


def _is_data_anchor_row(
    table: Sequence[Sequence[str]], ctx: TableContext, r: int,
) -> bool:
    """含序号或数值数据的数据行（折行续行的锚点）。"""
    if r < ctx.data_start:
        return False
    if ctx.serial_col is not None:
        serial_t = _cell_text(table, r, ctx.serial_col)
        if serial_t and (
            _SHORT_INDEX_RE.match(serial_t)
            or _INDEX_PREFIX_RE.match(serial_t)
        ):
            return True
    if _row_has_value_data(table, ctx, r):
        return True
    return False


def _find_wrap_anchor(
    table: Sequence[Sequence[str]], ctx: TableContext, r: int,
) -> Optional[int]:
    """从 r 向上找折行锚点（分类阶段不依赖 wrap_rows）。"""
    p = r
    hops = 0
    while p >= ctx.data_start and hops <= 2:
        if p in ctx.structure_rows:
            return None
        if _is_data_anchor_row(table, ctx, p):
            return p
        serial_t = (
            _cell_text(table, p, ctx.serial_col)
            if ctx.serial_col is not None else ""
        )
        if (
            not _row_has_value_data(table, ctx, p)
            and not serial_t
            and _row_label_text(table, ctx, p)
        ):
            hops += 1
            p -= 1
            continue
        return None
    return None


def _is_label_wrap_row(table: Sequence[Sequence[str]], ctx: TableContext, r: int) -> bool:
    """契约 2：左侧标签折行，后续行允许数据列为空。"""
    if r <= ctx.data_start:
        return False
    if _row_has_value_data(table, ctx, r):
        return False

    prev = r - 1
    if prev < ctx.data_start or prev in ctx.structure_rows:
        return False

    if _find_wrap_anchor(table, ctx, prev) is None:
        return False

    serial_t = (
        _cell_text(table, r, ctx.serial_col)
        if ctx.serial_col is not None else ""
    )
    if serial_t:
        return False

    short_t = (
        _cell_text(table, r, ctx.short_text_col)
        if ctx.short_text_col is not None else ""
    )
    long_t = (
        _cell_text(table, r, ctx.long_text_col)
        if ctx.long_text_col is not None else ""
    )

    if long_t and not short_t and len(long_t) >= 4:
        return True
    if short_t and not long_t:
        return True
    return False


def _header_nonempty(table: Sequence[Sequence[str]], ctx: TableContext, c: int) -> bool:
    return _column_has_header(table, ctx, c)


def _skip_row(ctx: TableContext, r: int) -> bool:
    return (
        r in ctx.wrap_rows
        or r in ctx.structure_rows
        or r in ctx.report_period_rows
    )


def _row_is_normal(
    table: Sequence[Sequence[str]], ctx: TableContext, r: int,
) -> bool:
    """数据区行是否符合 C2（数据行 / 折行 / 小节行 / 报告期行）。"""
    if r < ctx.data_start:
        return True
    if (
        r in ctx.wrap_rows
        or r in ctx.structure_rows
        or r in ctx.report_period_rows
    ):
        return True
    return _is_data_anchor_row(table, ctx, r)


def _stat(ctx: TableContext, col: int) -> Optional[ColumnStats]:
    for st in ctx.stats:
        if st.col == col:
            return st
    return None


def _looks_like_two_chunks(text: str, typical_len: float) -> bool:
    words = text.split()
    if len(words) < 4:
        return False
    min_chunk = max(typical_len * 0.55, 10.0)
    for i in range(2, len(words) - 1):
        left = " ".join(words[:i])
        right = " ".join(words[i:])
        if len(left) >= min_chunk and len(right) >= min_chunk:
            return True
    return False


# ---- C1：数据列上方至少有表头文本 ----
def _rule_missing_header(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    if ctx.header_band <= 0 and len(table) >= 2:
        issues.append(TableIssue(
            rule_id="C01_no_header_band",
            row=-1, col=-1,
            message="表格缺少表头行",
            snippet="",
        ))
        return issues

    for c in range(ctx.n_cols):
        if ctx.serial_col == 0 and c == 0:
            continue
        if not _column_has_data(table, ctx, c):
            continue
        if not _column_has_header(table, ctx, c):
            issues.append(TableIssue(
                rule_id="C01_missing_header",
                row=-1, col=c,
                message="数据列上方无表头文本",
                snippet="",
            ))
    return issues


# ---- C2：数据区行须可归入正常类型 ----
def _rule_unrecognized_data_row(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    for r in range(ctx.data_start, len(table)):
        if not any(_cell_text(table, r, c) for c in range(ctx.n_cols)):
            continue
        if _row_is_normal(table, ctx, r):
            continue
        snippet = next(
            (_cell_text(table, r, c) for c in range(ctx.n_cols) if _cell_text(table, r, c)),
            "",
        )
        issues.append(TableIssue(
            rule_id="C02_unrecognized_data_row",
            row=r, col=-1,
            message="数据区行不是数据行/折行续行/小节行，违反正常表契约 C2",
            snippet=snippet[:40],
        ))
    return issues


# ---- C3：列性质一致（覆盖所有主导类型列，不限 value_cols）----
def _rule_column_type_violation(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    seen: Set[Tuple[int, int]] = set()

    for c in range(ctx.n_cols):
        if c in ctx.header_exempt_cols and c == ctx.serial_col:
            continue
        dom = ctx.col_dominant.get(c, "sparse")
        if dom == "sparse":
            continue

        short_allow = max(1, int(
            (ctx.stats[c].non_empty if c < len(ctx.stats) else 0)
            * _NUMERIC_COL_SHORT_TEXT_MAX_RATIO
        ))
        short_used = 0

        for r in range(ctx.data_start, len(table)):
            if _skip_row(ctx, r):
                continue
            t = _cell_text(table, r, c)
            if not t:
                continue
            ct = _cell_type(t)
            key = (r, c)
            if key in seen:
                continue

            if dom == "numeric":
                if ct in ("text", "mixed"):
                    if len(t) <= _SHORT_CODE_MAX_LEN:
                        short_used += 1
                        if short_used <= short_allow:
                            continue
                    issues.append(TableIssue(
                        rule_id="C03_column_type_violation",
                        row=r, col=c,
                        message="数值主导列出现非法文本，违反正常表契约 C3",
                        snippet=t[:30],
                    ))
                    seen.add(key)
                elif ct == "mixed":
                    issues.append(TableIssue(
                        rule_id="C03_column_type_violation",
                        row=r, col=c,
                        message="数值主导列出现混合型数据，违反正常表契约 C3",
                        snippet=t[:30],
                    ))
                    seen.add(key)
            elif dom == "text" and c not in ctx.value_cols:
                if ct == "numeric" and len(t) >= 4:
                    st = _stat(ctx, c)
                    if st and st.median_len >= 6:
                        if _skip_text_length_check(table, ctx, r, c):
                            continue
                        issues.append(TableIssue(
                            rule_id="C03_column_type_violation",
                            row=r, col=c,
                            message="文本主导列出现独立数值 token，违反正常表契约 C3",
                            snippet=t[:30],
                        ))
                        seen.add(key)
    return issues


# ---- C4：数据行缺必填列（高填充率列）----
def _rule_incomplete_data_row(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    if not ctx.value_cols:
        return issues

    expected_cols = [
        c for c in ctx.value_cols
        if ctx.value_col_fill.get(c, 0) >= _VALUE_COL_FILL_EXPECT
    ]
    if not expected_cols:
        return issues

    for r in range(ctx.data_start, len(table)):
        if not _is_data_anchor_row(table, ctx, r):
            continue
        if _skip_row(ctx, r):
            continue
        missing = [c for c in expected_cols if not _cell_text(table, r, c)]
        if not missing:
            continue
        if len(missing) >= len(expected_cols):
            issues.append(TableIssue(
                rule_id="C04_incomplete_data_row",
                row=r, col=missing[0],
                message="数据行缺失高填充率数值列，违反正常表契约",
                snippet="",
            ))
    return issues


# ---- R08：邻列表头与数据错位 ----
def _rule_header_data_misalign(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    if ctx.header_band <= 0:
        return issues

    for a in range(ctx.n_cols - 1):
        b = a + 1
        if a in ctx.header_exempt_cols:
            continue
        if not _header_nonempty(table, ctx, a):
            continue
        if _header_nonempty(table, ctx, b):
            continue
        mismatch = 0
        for r in range(ctx.data_start, len(table)):
            if _skip_row(ctx, r):
                continue
            if not _cell_text(table, r, a) and _cell_text(table, r, b):
                mismatch += 1
        if mismatch >= 2:
            issues.append(TableIssue(
                rule_id="R08_header_data_misalign",
                row=-1, col=a,
                message=(
                    f"列{a}有表头但{mismatch}行无数据，邻列{b}无表头却有数据，"
                    f"判定为列错位"
                ),
                snippet="",
            ))
    return issues


# ---- R01：无法接龙的孤立长文本 ----
def _rule_orphan_extension(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    long_c = ctx.long_text_col
    short_c = ctx.short_text_col
    if long_c is None or long_c == short_c:
        return issues
    if not any(_cell_text(table, r, short_c) for r in range(ctx.data_start, len(table)) if short_c is not None):
        return issues

    for r in range(ctx.data_start, len(table)):
        if _skip_row(ctx, r):
            continue
        long_t = _cell_text(table, r, long_c)
        short_t = _cell_text(table, r, short_c) if short_c is not None else ""
        serial_t = (
            _cell_text(table, r, ctx.serial_col)
            if ctx.serial_col is not None else ""
        )
        if short_t or serial_t or not long_t or len(long_t) < 4:
            continue
        if _cell_type(long_t) == "numeric":
            continue
        if _skip_text_length_check(table, ctx, r, long_c):
            continue
        issues.append(TableIssue(
            rule_id="R01_orphan_extension",
            row=r, col=long_c,
            message="长文本列孤立片段，且不是合法折行续行，判定为数据丢失/错位",
            snippet=long_t[:40],
        ))
    return issues


# ---- R09：中间列孤立单格 ----
def _rule_interior_singleton(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    last_col = ctx.n_cols - 1
    if last_col < 2:
        return issues

    for r in range(ctx.data_start, len(table)):
        if _skip_row(ctx, r):
            continue
        filled = [c for c in range(ctx.n_cols) if _cell_text(table, r, c)]
        if len(filled) != 1:
            continue
        c = filled[0]
        if c <= 0 or c >= last_col:
            continue
        if c in ctx.label_cols:
            continue
        t = _cell_text(table, r, c)
        issues.append(TableIssue(
            rule_id="R09_interior_singleton",
            row=r, col=c,
            message="仅中间列有内容、其余全空，且非折行续行，判定为碎片行",
            snippet=t[:40],
        ))
    return issues


# ---- R02 / R03 / R04 / R05 / R06 / R07 ----
def _rule_merged_in_short_col(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    short_c = ctx.short_text_col
    if short_c is None:
        return issues
    st = _stat(ctx, short_c)
    if not st or st.median_len < 1:
        return issues
    long_c = ctx.long_text_col

    for r in range(ctx.data_start, len(table)):
        if _skip_row(ctx, r):
            continue
        t = _cell_text(table, r, short_c)
        if not t:
            continue
        serial_empty = ctx.serial_col is None or not _cell_text(table, r, ctx.serial_col)
        long_empty = long_c is None or not _cell_text(table, r, long_c)
        has_values = _row_has_value_data(table, ctx, r)

        if (
            serial_empty
            and ctx.serial_col is not None
            and _INDEX_PREFIX_RE.match(t)
            and len(t) > st.median_len * 1.5
        ):
            issues.append(TableIssue(
                rule_id="R02_merged_in_short_col",
                row=r, col=short_c,
                message="短文本列吞并序号及邻列内容",
                snippet=t[:40],
            ))
            continue
        if (
            long_empty
            and has_values
            and len(t) > st.median_len * 2.0
            and not _skip_text_length_check(table, ctx, r, short_c)
        ):
            issues.append(TableIssue(
                rule_id="R02_merged_in_short_col",
                row=r, col=short_c,
                message="短文本列过长且长文本列为空，同行有数值，判定为粘连",
                snippet=t[:40],
            ))
    return issues


def _rule_stacked_long_text(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    long_c = ctx.long_text_col
    if long_c is None:
        return issues
    st = _stat(ctx, long_c)
    if not st or st.median_len < 4:
        return issues

    for r in range(ctx.data_start, len(table)):
        if _skip_row(ctx, r):
            continue
        t = _cell_text(table, r, long_c)
        if not t or _cell_type(t) != "text":
            continue
        if _skip_text_length_check(table, ctx, r, long_c):
            continue
        if len(t) < st.median_len * 2.0:
            continue
        if _looks_like_two_chunks(t, st.median_len):
            issues.append(TableIssue(
                rule_id="R03_stacked_long_text",
                row=r, col=long_c,
                message="长文本列单格含两段独立长文本，判定为相邻行粘连",
                snippet=t[:40],
            ))
    return issues


def _rule_merged_numeric(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    for c in ctx.value_cols:
        for r in range(ctx.data_start, len(table)):
            if _skip_row(ctx, r):
                continue
            t = _cell_text(table, r, c)
            if not t:
                continue
            tokens = t.split()
            numeric_tokens = [tk for tk in tokens if _NUM_TOKEN_RE.match(tk)]
            if len(numeric_tokens) >= 2:
                issues.append(TableIssue(
                    rule_id="R04_merged_numeric",
                    row=r, col=c,
                    message="数值格含多个独立数值 token",
                    snippet=t[:40],
                ))
    return issues


def _is_amount_token(token: str) -> bool:
    t = str(token or "").strip()
    if not t:
        return False
    return bool(_AMOUNT_TOKEN_RE.match(t))


def _is_text_label_token(token: str) -> bool:
    t = str(token or "").strip()
    if not t or _NUM_TOKEN_RE.match(t):
        return False
    if _is_report_period_text(t):
        return False
    return bool(_CJK_OR_ALPHA_RE.search(t))


def _is_glue_label_token(token: str) -> bool:
    """短实体标签（地区名/科目名），排除长地址与门牌碎片。"""
    t = str(token or "").strip()
    if not _is_text_label_token(t):
        return False
    # 「号/层/栋…」门牌续写、E2 类楼号 → 不当事项标签
    if re.match(r"^[号层楼室弄巷路街栋幢座附之单]", t):
        return False
    if re.match(r"^[A-Za-z]\d+[A-Za-z]?$", t):
        return False
    if t in ("单元", "号楼", "附号"):
        return False
    cjk_n = len(re.findall(r"[\u4e00-\u9fff]", t))
    # 长地址句不当粘连标签；短标签如「成都」「其他地区」「合计」
    if cjk_n > 12 or len(t) > 20:
        return False
    if cjk_n >= 1:
        return True
    return bool(re.search(r"[A-Za-z]{2,}", t))


def _looks_like_numeric_text_glue(text: str) -> bool:
    """金额型数值与文本同格（分列失败重点可疑）。"""
    t = str(text or "").strip()
    if not t or _is_report_period_text(t):
        return False
    tokens = t.split()
    if len(tokens) >= 2:
        amounts = [tk for tk in tokens if _is_amount_token(tk)]
        labels = [tk for tk in tokens if _is_glue_label_token(tk)]
        if not amounts or not labels:
            return False
        # 有千分位金额 → 强信号；否则要求短标签且整格不太长（避免地址）
        if any("," in a for a in amounts):
            return True
        return len(t) <= 36 and any(len(lb) <= 12 for lb in labels)
    # 无空格：19,079,642成都 / 成都19,079,642
    if _CJK_OR_ALPHA_RE.search(t) and _AMOUNT_GLUED_TEXT_RE.match(t):
        if "," in t and len(t) <= 40:
            return True
        if re.search(r"\d{4,}[号层楼室栋幢]", t):
            return False
        return len(t) <= 28
    return False


def _rule_numeric_text_glue(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    """R10：金额与文本粘连 → 重点严格审核。"""
    issues: List[TableIssue] = []
    for r in range(ctx.data_start, len(table)):
        if _skip_row(ctx, r):
            continue
        for c in range(ctx.n_cols):
            t = _cell_text(table, r, c)
            if not t or not _looks_like_numeric_text_glue(t):
                continue
            issues.append(TableIssue(
                rule_id="R10_numeric_text_glue",
                row=r, col=c,
                message="金额型数值与文本同格粘连，分列可疑，须严格审核",
                snippet=t[:40],
            ))
    return issues


def _rule_text_in_numeric(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    for c in ctx.value_cols:
        impurities = 0
        total = 0
        impurity_rows: List[Tuple[int, str]] = []
        for r in range(ctx.data_start, len(table)):
            if _skip_row(ctx, r):
                continue
            t = _cell_text(table, r, c)
            if not t:
                continue
            total += 1
            ct = _cell_type(t)
            if ct not in ("text", "mixed"):
                continue
            if len(t) <= _SHORT_CODE_MAX_LEN:
                impurities += 1
                continue
            impurity_rows.append((r, t))

        allow_short = max(1, int(total * _NUMERIC_COL_SHORT_TEXT_MAX_RATIO))
        short_used = sum(
            1 for r in range(ctx.data_start, len(table))
            if not _skip_row(ctx, r)
            and _cell_text(table, r, c)
            and len(_cell_text(table, r, c)) <= _SHORT_CODE_MAX_LEN
            and _cell_type(_cell_text(table, r, c)) in ("text", "mixed")
        )
        for r, t in impurity_rows:
            issues.append(TableIssue(
                rule_id="R05_text_in_numeric",
                row=r, col=c,
                message="数值列出现非法长文本",
                snippet=t[:30],
            ))
        if short_used > allow_short:
            for r in range(ctx.data_start, len(table)):
                if _skip_row(ctx, r):
                    continue
                t = _cell_text(table, r, c)
                if t and len(t) <= _SHORT_CODE_MAX_LEN and _cell_type(t) in ("text", "mixed"):
                    issues.append(TableIssue(
                        rule_id="R05_text_in_numeric",
                        row=r, col=c,
                        message="数值列短文本杂质超出允许比例",
                        snippet=t[:30],
                    ))
    return issues


def _rule_ghost_column(
    table: Sequence[Sequence[str]], ctx: TableContext,
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    for c in range(ctx.n_cols):
        if any(_cell_text(table, r, c) for r in range(ctx.data_start, len(table))):
            continue
        left_ok = (
            c > 0
            and sum(1 for r in range(ctx.data_start, len(table)) if _cell_text(table, r, c - 1)) >= 2
        )
        right_ok = (
            c + 1 < ctx.n_cols
            and sum(1 for r in range(ctx.data_start, len(table)) if _cell_text(table, r, c + 1)) >= 2
        )
        if left_ok or right_ok:
            issues.append(TableIssue(
                rule_id="R06_ghost_column",
                row=-1, col=c,
                message="整列无数据但邻列有内容",
                snippet="",
            ))
    return issues


def _rule_word_crosses_columns(
    words: Sequence[dict],
    col_bounds: Optional[Sequence[float]],
) -> List[TableIssue]:
    issues: List[TableIssue] = []
    if not words or not col_bounds or len(col_bounds) < 3:
        return issues
    for w in words:
        wx0, wx1 = float(w.get("x0", 0)), float(w.get("x1", 0))
        wtext = str(w.get("text", "")).strip()
        if not wtext or len(wtext) < 5 or (wx1 - wx0) < 20:
            continue
        affected: List[int] = []
        for ci in range(len(col_bounds) - 1):
            cleft, cright = col_bounds[ci], col_bounds[ci + 1]
            col_w = cright - cleft
            if col_w <= 0:
                continue
            overlap = max(0.0, min(wx1, cright) - max(wx0, cleft))
            if overlap / col_w > 0.30:
                affected.append(ci)
        if len(affected) >= 2:
            issues.append(TableIssue(
                rule_id="R07_word_crosses_columns",
                row=-1, col=affected[0],
                message=f"文本项横跨列{affected}",
                snippet=wtext[:50],
            ))
    return issues


_RULES: List[Callable[..., List[TableIssue]]] = [
    _rule_missing_header,
    _rule_unrecognized_data_row,
    _rule_column_type_violation,
    _rule_incomplete_data_row,
    _rule_header_data_misalign,
    _rule_orphan_extension,
    _rule_interior_singleton,
    _rule_merged_in_short_col,
    _rule_stacked_long_text,
    _rule_merged_numeric,
    _rule_text_in_numeric,
    _rule_numeric_text_glue,
]


def evaluate_table_issues(
    table_data: List[List[str]],
    words: Optional[List[dict]] = None,
    col_bounds: Optional[List[float]] = None,
) -> Tuple[List[TableIssue], TableContext]:
    table_data = strip_blank_rows_cols([list(row) for row in table_data])
    if len(table_data) < 2 or not table_data[0] or len(table_data[0]) < 2:
        return [], TableContext()

    ctx = _build_context(table_data)
    issues: List[TableIssue] = []
    for fn in _RULES:
        issues.extend(fn(table_data, ctx))
    issues.extend(_rule_word_crosses_columns(words or [], col_bounds))
    return issues, ctx


# 兼容旧名
ColumnRoles = TableContext


def _classify_issue_buckets(
    issues: List[TableIssue],
) -> Tuple[bool, bool, str, List[TableIssue]]:
    """将命中规则分为表头缺失标记 vs 质量异常。"""
    header_missing = any(i.rule_id in HEADER_MISSING_RULE_IDS for i in issues)
    quality_issues = [
        i for i in issues if i.rule_id not in HEADER_MISSING_RULE_IDS
    ]
    has_quality = bool(quality_issues)
    if has_quality:
        anomaly_class = ANOMALY_CLASS_QUALITY
    elif header_missing:
        anomaly_class = ANOMALY_CLASS_MISSING_HEADER
    else:
        anomaly_class = ANOMALY_CLASS_NONE
    return header_missing, has_quality, anomaly_class, quality_issues


def issues_to_report(
    issues: List[TableIssue],
    table_data: List[List[str]],
) -> Dict[str, Any]:
    header_missing, has_quality, anomaly_class, quality_issues = _classify_issue_buckets(
        issues,
    )
    strict_review = any(i.rule_id in _STRICT_REVIEW_RULE_IDS for i in quality_issues)
    report: Dict[str, Any] = {
        "is_normal_table": not has_quality,
        "has_anomalies": has_quality,
        "header_missing": header_missing,
        "anomaly_class": anomaly_class,
        "anomaly_score": 0.0,
        "needs_review": has_quality,
        "strict_review": strict_review,
        "empty_cols": [],
        "empty_rows": [],
        "length_outliers": [],
        "mixed_type_cells": [],
        "merged_values": [],
        "cross_boundary_words": [],
        "reasons": [],
        "issues": [],
        "rule_ids": [],
    }
    seen: set[str] = set()
    for issue in issues:
        report["issues"].append({
            "rule_id": issue.rule_id,
            "row": issue.row,
            "col": issue.col,
            "message": issue.message,
            "snippet": issue.snippet,
        })
        if issue.rule_id not in report["rule_ids"]:
            report["rule_ids"].append(issue.rule_id)
        summary = f"[{issue.rule_id}] {issue.message}"
        if issue.row >= 0:
            summary += f"（行{issue.row}列{issue.col}）"
        if summary not in seen:
            seen.add(summary)
            report["reasons"].append(summary)

        rid = issue.rule_id
        if rid == "R01_orphan_extension" and issue.row >= 0:
            report["empty_rows"].append((issue.row, 0.17))
        elif rid in ("R02_merged_in_short_col", "R05_text_in_numeric"):
            dom = "numeric" if rid == "R05_text_in_numeric" else "text"
            ct = "text" if rid == "R05_text_in_numeric" else "mixed"
            report["mixed_type_cells"].append(
                (issue.row, issue.col, issue.snippet[:30], dom, ct)
            )
        elif rid == "R03_stacked_long_text":
            report["length_outliers"].append(
                (issue.row, issue.col, issue.snippet[:40], 0, len(issue.snippet))
            )
        elif rid == "R04_merged_numeric":
            report["merged_values"].append(
                (issue.row, issue.col, issue.snippet[:40], len(issue.snippet.split()))
            )
        elif rid == "R10_numeric_text_glue":
            report["mixed_type_cells"].append(
                (issue.row, issue.col, issue.snippet[:30], "numeric", "mixed")
            )
            report["merged_values"].append(
                (issue.row, issue.col, issue.snippet[:40], len(issue.snippet.split()))
            )
        elif rid in ("R06_ghost_column", "C01_missing_header", "C01_no_header_band", "R08_header_data_misalign"):
            if issue.col >= 0:
                report["empty_cols"].append((issue.col, 1.0))
        elif rid in ("C02_unrecognized_data_row", "C04_incomplete_data_row"):
            if issue.row >= 0:
                report["empty_rows"].append((issue.row, 0.17))
        elif rid == "C03_column_type_violation":
            report["mixed_type_cells"].append(
                (issue.row, issue.col, issue.snippet[:30], "typed", "violation")
            )
        elif rid == "R07_word_crosses_columns":
            report["cross_boundary_words"].append(
                (issue.snippet[:50], 0, 0, [issue.col])
            )
        elif rid == "R09_interior_singleton":
            report["empty_rows"].append((issue.row, 0.17))

    report["anomaly_score"] = min(len(quality_issues) / 10.0, 1.0)
    if strict_review:
        report["anomaly_score"] = max(float(report["anomaly_score"]), 0.85)
    return report
