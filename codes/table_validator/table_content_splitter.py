# -*- coding: utf-8 -*-
"""
表格内容拆分器 — 将误合并的「表 + 段落 + 子表」拆为独立条目。

原则（处理表头时严格遵守）：
  - 不补充数据：空白格保持空白，不把推断值填入空列
  - 不删除空白、不强行靠左/靠右对齐：保留原始列位与列宽
  - 允许：合并 PDF 拆开的同行碎片（2024+年）；清除因列网格错误误填的单元格
"""

from __future__ import annotations

import copy
import re
from typing import List, Optional, Set, Tuple

from codes.table_validator.header_boundary import (
    is_date_only_header_row_items,
    is_in_table_section_label_row,
    is_month_day_cell,
    is_numeric_data_cell,
    is_row_label_cell,
    is_table_tail_annotation_row,
    is_value_like_cell,
    is_year_cell,
    strip_tail_annotation_rows_from_data,
    pop_footnote_bundle,
    assign_footnote_bundle,
    clear_footnote_bundle,
    _normalize_cell_text,
)

_YEAR_FRAG_RE = re.compile(r"^(19|20)\d{2}$")
_SECTION_II_RE = re.compile(r"[（(]\s*ii\s*[）)]", re.IGNORECASE)
_SENSITIVITY_KW = ("敏感性分析", "提高0.25%", "降低0.25%")
_FOOTNOTE_KW = ("死亡率", "经验生命", "公开统计", "如下：", "如下:")
_TABLE_SECTION_KW = ("计入当期损益", "计入其他综合收益", "其他变动", "年初", "年末", "减：", "加：")


def _row_cells(row: list) -> List[str]:
    return [str(c).strip() for c in row]


def is_date_only_header_row_cells(row: list) -> bool:
    texts = [c for c in _row_cells(row) if c]
    if not texts:
        return False
    if any(is_row_label_cell(t) for t in texts):
        return False
    date_like = sum(
        1 for t in texts
        if is_year_cell(t) or is_month_day_cell(t)
        or re.search(r"(?:19|20)\d{2}年?", t)
    )
    numeric = sum(1 for t in texts if is_numeric_data_cell(t))
    return date_like >= 1 and numeric == 0


def _merge_adjacent_fragments_preserve_width(row: list) -> List[str]:
    """合并 PDF 拆开的年/月/日碎片，保持列数与空白位置不变。"""
    out = [str(c).strip() for c in row]
    if not out:
        return out

    i = 0
    while i < len(out) - 1:
        c = out[i]
        nxt = out[i + 1]
        if not c:
            i += 1
            continue

        merged: Optional[str] = None
        ct = _normalize_cell_text(c)
        nt = _normalize_cell_text(nxt)

        if _YEAR_FRAG_RE.match(ct) and nt in ("年", "月", "日"):
            merged = c + nxt
        elif re.match(r"^\d{1,2}$", ct) and nt in ("月", "日"):
            merged = c + nxt
        elif ct in ("月", "日") and re.match(r"^\d{1,2}$", nt):
            merged = c + nxt
        elif re.match(r"^\d{1,2}$", ct) and re.match(r"^\d{1,2}$", nt) and i + 2 < len(out):
            nxt2 = _normalize_cell_text(out[i + 2])
            if nxt2 in ("月", "日"):
                merged = f"{c}{nxt}{out[i + 2]}"
                out[i] = merged
                out[i + 1] = ""
                out[i + 2] = ""
                i += 3
                continue

        if merged:
            out[i] = merged
            out[i + 1] = ""
            i += 2
            continue
        i += 1

    return out


def _reference_value_column_indices(
    data: List[list], header_count: int
) -> List[int]:
    """数据区数值列索引，用于判断月日表头应落在哪些列（不移动内容，只用于清除误填）。"""
    for row in data[header_count:]:
        if not is_main_table_data_row(row):
            continue
        cells = _row_cells(row)
        idx = [i for i, c in enumerate(cells) if is_numeric_data_cell(c)]
        if len(idx) >= 2:
            return idx
    return []


def _clear_spurious_date_header_cells(
    data: List[list], header_count: int
) -> List[list]:
    """清除月日表头行中落在非数值列上的误填内容（置空，不挪动其余列）。"""
    ref = _reference_value_column_indices(data, header_count)
    if len(ref) < 2:
        return data

    ref_set = set(ref)
    result = [list(r) for r in data]

    for hi in range(1, header_count):
        row = result[hi]
        if not is_date_only_header_row_cells(row):
            continue
        if any(is_year_cell(c) for c in _row_cells(row)):
            continue
        for i, c in enumerate(row):
            if c and i not in ref_set:
                row[i] = ""

    return result


_MONTH_DAY_FULL_RE = re.compile(r"\d{1,2}月\d{1,2}日")


def normalize_table_header_columns(data: List[list]) -> List[list]:
    """合并年份/月日碎片；清除误填列。不补充数据、不压缩空白、不强行对齐。"""
    if not data:
        return data

    header_count = 0
    for row in data[:4]:
        if is_date_only_header_row_cells(row):
            header_count += 1
        else:
            break

    result: List[list] = []
    for i, row in enumerate(data):
        if i < header_count:
            result.append(_merge_adjacent_fragments_preserve_width(row))
        else:
            result.append([str(c).strip() for c in row])

    if header_count >= 2:
        result = _clear_spurious_date_header_cells(result, header_count)

    return result


def _effective_col_count(row: list) -> int:
    cells = _row_cells(row)
    return len([c for c in cells if c])


def is_main_table_data_row(row: list) -> bool:
    """主表数据行：左侧行标签 + ≥2 个参数数值。"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if len(non_empty) < 2:
        return False
    num = sum(1 for c in non_empty if is_numeric_data_cell(c))
    if num < 2:
        return False
    if any(is_row_label_cell(c) for c in non_empty):
        return True
    # 无标签但多数值（续表数据）
    return num >= 2 and not is_date_only_header_row_cells(row)


def is_multi_column_header_row(row: list) -> bool:
    """多列表头行（如 义务现值 | 计划资产 | 净资产），无数值。"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if len(non_empty) < 2:
        return False
    num = sum(1 for c in non_empty if is_numeric_data_cell(c))
    if num > 0:
        return False
    labels = [
        c for c in non_empty
        if len(re.findall(r"[\u4e00-\u9fff]", c)) >= 2 and not is_year_cell(c)
    ]
    return len(labels) >= 2


def is_table_section_label_row(row: list) -> bool:
    """表内小节标题行（无数值、短标签），应留在表中。"""
    return is_in_table_section_label_row(row)


def is_embedded_paragraph_row(row: list) -> bool:
    """表内嵌段落行（应抽出为 text）。"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    if is_date_only_header_row_cells(row):
        return False
    if is_table_section_label_row(row):
        return False
    if is_table_tail_annotation_row(row, max(len(row), 1)):
        return True
    if is_multi_column_header_row(row):
        return False
    if is_main_table_data_row(row):
        return False
    if is_sensitivity_data_row(row):
        return False

    joined = "".join(non_empty)
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    num = sum(1 for c in non_empty if is_numeric_data_cell(c))

    # 脚注/说明（行内可含年份数字如 2010-2013）
    if any(kw in joined for kw in _FOOTNOTE_KW) and cn >= 10:
        return True
    if cn >= 18 and num <= 1:
        return True

    if _SECTION_II_RE.search(joined) and cn >= 10:
        return True
    if cn >= 12 and num == 0:
        return True
    if len(non_empty) == 1 and cn <= 10 and num == 0 and not is_year_cell(non_empty[0]):
        return True
    if cn >= 6 and num == 0 and "影响" in joined and "义务" in joined:
        return True
    return False


def is_sensitivity_header_row(row: list) -> bool:
    joined = "".join(_row_cells(row))
    if joined.count("精算假设") >= 2:
        return True
    if "提高0.25%" in joined and "降低0.25%" in joined:
        return True
    return False


def is_sensitivity_data_row(row: list) -> bool:
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if len(non_empty) < 2:
        return False
    num = sum(1 for c in non_empty if is_numeric_data_cell(c))
    if num < 1:
        return False
    # 敏感性表：括号负数 (84) 或单标签+两数值
    if any(is_row_label_cell(c) for c in non_empty) and num >= 1:
        if any("(" in c or "（" in c for c in non_empty if is_numeric_data_cell(c)):
            return True
        if num >= 2 and not is_main_table_data_row(row):
            return True
    return False


def _row_to_text(row: list) -> str:
    return " ".join(c for c in _row_cells(row) if c)


def _value_col_indices(row: list, start_col: int = 1) -> List[int]:
    """非首列中数值/占位格所在的列下标（结构特征，无关键词）。"""
    cells = _row_cells(row)
    return [
        i for i in range(start_col, len(cells))
        if is_value_like_cell(cells[i])
    ]


def _body_col_profile(rows: List[list]) -> Set[int]:
    """已扫描行中，数据区（≥2 个数值列）出现过的列下标集合。"""
    profile: Set[int] = set()
    for row in rows:
        cols = _value_col_indices(row, 1)
        if len(cols) >= 2:
            profile.update(cols)
    return profile


def _body_continuation_overlap_threshold(profile: Set[int]) -> int:
    if not profile:
        return 2
    return max(2, min(3, len(profile)))


def _has_following_body_continuation(
    data: List[list],
    start: int,
    profile: Set[int],
    lookahead: int = 6,
) -> bool:
    """后续行是否延续同一数据列网格。

    表内「仅首列有字、其余空」的行，若下方数据仍落在已建立的数据列上，
    说明表格结构未断，应留在表内而非抽成 text。
    注释段后的嵌套小表（列位偏移、仅部分列重合）不会满足重合阈值。
    """
    if not profile:
        return False
    need = _body_continuation_overlap_threshold(profile)
    scanned = 0
    for j in range(start + 1, min(start + 1 + lookahead, len(data))):
        row = data[j]
        cells = _row_cells(row)
        if not any(cells):
            continue
        value_cols = _value_col_indices(row, 1)
        if len(value_cols) >= 2 and len(profile.intersection(value_cols)) >= need:
            return True
        scanned += 1
        if scanned >= 3:
            break
    return False


def split_mixed_table_data(data: List[list]) -> List[Tuple[str, object, int, int]]:
    """
    将混合 data 拆为 [(kind, payload, start_row, end_row), ...]。
    end_row 为开区间。
    """
    if not data:
        return []

    data = normalize_table_header_columns(data)
    segments: List[Tuple[str, object, int, int]] = []
    buf: List[list] = []
    buf_start = 0
    had_main_data = False
    i = 0

    def flush_table(at: int):
        nonlocal buf, buf_start, had_main_data
        if len(buf) >= 2:
            segments.append(("table", buf, buf_start, at))
        buf = []

    while i < len(data):
        row = data[i]

        if is_embedded_paragraph_row(row):
            # 结构安全网：表尾形态行若下方仍延续同一数据列网格 → 留在表内
            if _has_following_body_continuation(data, i, _body_col_profile(buf)):
                buf.append(row)
                i += 1
                continue

            flush_table(i)
            start = i
            paras = []
            while i < len(data) and is_embedded_paragraph_row(data[i]):
                paras.append(_row_to_text(data[i]))
                i += 1
            segments.append(("text", "\n".join(paras), start, i))
            had_main_data = False
            continue

        if had_main_data and is_sensitivity_header_row(row):
            flush_table(i)

        if not buf:
            buf_start = i

        if is_main_table_data_row(row):
            had_main_data = True
        elif is_sensitivity_data_row(row):
            had_main_data = True

        buf.append(row)
        i += 1

    flush_table(len(data))
    return segments


def _estimate_row_y_range(
    table: dict, start_row: int, end_row: int, total_rows: int
) -> Tuple[float, float]:
    y0 = float(table.get("y0", 0) or 0)
    y1 = float(table.get("y1", 0) or 0)
    if y1 <= y0 or total_rows <= 0:
        return y0, y1
    h = (y1 - y0) / total_rows
    return y0 + start_row * h, y0 + end_row * h


def split_mixed_table_entry(table: dict) -> List[dict]:
    """
    将单个混合 table 条目拆为多个 table / text 条目。
    若无需拆分，返回 [table]。
    """
    data = table.get("data", [])
    if len(data) < 4:
        return [table]

    segments = split_mixed_table_data(data)
    if len(segments) <= 1 and segments and segments[0][0] == "table":
        t = copy.deepcopy(table)
        t["data"] = segments[0][1]
        t["rows"] = len(t["data"])
        t["cols"] = max((len(r) for r in t["data"]), default=0)
        if t["data"] != data:
            t["_header_normalized"] = True
        return [t]

    page = table.get("page", 0)
    total_rows = len(data)
    fn_recs = pop_footnote_bundle(table)
    results: List[dict] = []

    for kind, payload, start_row, end_row in segments:
        y0, y1 = _estimate_row_y_range(table, start_row, end_row, total_rows)
        if kind == "text":
            text = str(payload).strip()
            if not text:
                continue
            results.append({
                "type": "text",
                "page": page,
                "y0": y0,
                "y1": y1,
                "x0": table.get("x0", 0),
                "x1": table.get("x1", 0),
                "context_text": text,
                "data": text,
                "extractor": table.get("extractor", ""),
                "segment_source": "table_content_split",
                "confidence": 0.75,
            })
        else:
            rows = payload
            t = copy.deepcopy(table)
            clear_footnote_bundle(t)
            t["type"] = "table"
            t["data"] = rows
            t["rows"] = len(rows)
            t["cols"] = max((len(r) for r in rows), default=0)
            t["y0"] = y0
            t["y1"] = y1
            t["_split_from_mixed"] = True
            t["segment_source"] = "table_content_split"
            results.append(t)

    # 表尾脚注只转移到拆分后最后一个 table 段（转移，不复制）
    table_parts = [r for r in results if r.get("type") == "table"]
    if fn_recs and table_parts:
        assign_footnote_bundle(table_parts[-1], fn_recs)

    if not results:
        return [table]

    print(
        f"  [表文拆分] P{page}: 1表 → {sum(1 for r in results if r.get('type')!='text')}表 + "
        f"{sum(1 for r in results if r.get('type')=='text')}段"
    )
    return results


def split_mixed_table_entries(tables: List[dict]) -> List[dict]:
    """批量拆分混合表格条目。"""
    out: List[dict] = []
    for t in tables:
        if t.get("type") == "text" or not t.get("data"):
            out.append(t)
            continue
        out.extend(split_mixed_table_entry(t))
    return out
