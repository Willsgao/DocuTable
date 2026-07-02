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
    attach_stripped_footnotes,
    _normalize_cell_text,
)

_YEAR_FRAG_RE = re.compile(r"^(19|20)\d{2}$")
_SECTION_II_RE = re.compile(r"[（(]\s*ii\s*[）)]", re.IGNORECASE)
_SENSITIVITY_KW = ("敏感性分析", "提高0.25%", "降低0.25%")
_FOOTNOTE_KW = ("死亡率", "经验生命", "公开统计", "如下：", "如下:")
_TABLE_SECTION_KW = ("计入当期损益", "计入其他综合收益", "其他变动", "年初", "年末", "减：", "加：")


_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$", re.IGNORECASE)
_TABLE_CAPTION_RE = re.compile(
    r"表\s*\d+\s*[（(]\s*[A-Z]{1,4}\d*\s*[）)]",
    re.IGNORECASE,
)


def _has_letter_column_header_row(row: list) -> bool:
    """列标 a/b(/c/d) 行（含 CC1 仅 a、b 两列）。"""
    labels = {
        c.lower()
        for c in _row_cells(row)
        if c and len(c) == 1 and c.isalpha()
    }
    if len(labels) >= 3 and {"a", "b", "c"}.issubset(labels):
        return True
    if len(labels) >= 2 and {"a", "b"}.issubset(labels):
        return True
    return False


def _row_has_reporting_date(row: list) -> bool:
    for c in _row_cells(row):
        if is_year_cell(c) or is_month_day_cell(c):
            return True
        if re.search(r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日", c):
            return True
    return False


def _is_rmb_unit_lead_row(row: list) -> bool:
    """首格为（人民币百万元…）等单位说明行。"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    lead = non_empty[0]
    if not lead.startswith(("（", "(")):
        return False
    if "人民币" not in lead and "百万元" not in lead and "万元" not in lead:
        return False
    return sum(1 for c in non_empty if is_numeric_data_cell(c)) == 0


def _count_period_header_rows(rows: List[list]) -> int:
    n = 0
    for row in rows:
        if is_date_only_header_row_cells(row):
            n += 1
            continue
        if _row_has_reporting_date(row):
            n += 1
            continue
        if sum(
            1 for c in _row_cells(row)
            if is_year_cell(c) or is_month_day_cell(c)
        ) >= 2:
            n += 1
    return n


def _count_numbered_body_rows(data: List[list], start: int = 0) -> int:
    """带行号的数据行数量（结构特征，无科目关键词）。"""
    count = 0
    for row in data[start:]:
        cells = _row_cells(row)
        if not cells:
            continue
        if cells[0] and _ROW_NUMBER_RE.match(cells[0]):
            rest = cells[1:]
            vals = sum(
                1 for c in rest
                if is_numeric_data_cell(c)
                or str(c).strip() in ("-", "－", "—", "–")
            )
            if vals >= 1 or any(rest):
                count += 1
        elif is_main_table_data_row(row):
            count += 1
    return count


def _row_has_pillar_table_caption(row: list) -> bool:
    return bool(_TABLE_CAPTION_RE.search("".join(_row_cells(row))))


def _has_pillar_grid_header(data: List[list], scan: int = 8) -> bool:
    """披露表表头带：列标行或（人民币…）单位行（不用宽泛多列表头单独触发）。"""
    rows = data[: min(scan, len(data))]
    if any(_has_letter_column_header_row(r) for r in rows):
        return True
    return any(
        _is_rmb_unit_lead_row(r) or is_unit_column_header_row(r)
        for r in rows
    )


def _pillar_has_mid_repeat_header_band(data: List[list]) -> bool:
    """表中间重复「报告期+列名」→ 实为多表拼接，交回原结构分裂逻辑。"""
    from codes.table_validator.hybrid_segmenter import _find_mid_table_period_break
    return _find_mid_table_period_break(data) >= 0


def _merge_orphan_label_into_next_numbered_row(data: List[list]) -> List[list]:
    """无行号折行标签行并入下一行号行（如 CC1/行60 标签在编号行上方）。"""
    if not data:
        return data
    out: List[list] = []
    i = 0
    while i < len(data):
        row = data[i]
        cells = _row_cells(row)
        orphan = " ".join(c for c in cells if c).strip()
        if i + 1 < len(data) and orphan:
            nxt = [str(c) for c in data[i + 1]]
            nxt_cells = _row_cells(nxt)
            nxt_label = nxt_cells[1] if len(nxt_cells) > 1 else ""
            # 短续行（如「损益」）应并入上一行号行，不能并入下一行
            if len(orphan) <= 8 and not orphan.startswith(("其中", "满足")):
                out.append([str(c) for c in row])
                i += 1
                continue
            if (
                not str(nxt_label).strip()
                and len(orphan) >= 10
                and nxt_cells[0]
                and _ROW_NUMBER_RE.match(nxt_cells[0])
            ):
                label_col = 1 if len(nxt) > 1 else 0
                nxt[label_col] = orphan
                out.append(nxt)
                i += 2
                continue
            if (
                not (cells[0] and _ROW_NUMBER_RE.match(cells[0]))
                and nxt_cells[0]
                and _ROW_NUMBER_RE.match(nxt_cells[0])
                and (
                    orphan.startswith(("其中", "满足", "加", "资产", "未扣除"))
                    or (
                        not is_in_table_section_label_row(row)
                        and len(orphan) <= 36
                    )
                )
            ):
                label_col = 1 if len(nxt) > 1 else 0
                prev = str(nxt[label_col]).strip()
                nxt[label_col] = f"{orphan} {prev}".strip() if prev else orphan
                out.append(nxt)
                i += 2
                continue
        out.append([str(c) for c in row])
        i += 1
    return out


def _attach_lone_code_to_unit_header_row(data: List[list]) -> List[list]:
    """表头带：单独一行的「代码」并入单位行右列，日期独占下一行。"""
    if len(data) < 2:
        return data
    out = [list(r) for r in data]
    i = 0
    while i < len(out) - 1:
        cur = _row_cells(out[i])
        nxt = _row_cells(out[i + 1])
        nxt_vals = [c for c in nxt if c]
        if len(nxt_vals) == 1 and nxt_vals[0] == "代码":
            joined = "".join(cur)
            if _is_rmb_unit_lead_row(cur) or ("数额" in joined and "人民币" in joined):
                while len(out[i]) < 3:
                    out[i].append("")
                out[i][2] = "代码"
                out.pop(i + 1)
                continue
        i += 1
    return out


def split_clustered_date_code_header_rows(rows: List[dict]) -> List[dict]:
    """Y 聚类误并：同一行内「报告期日期 + 代码/数额」拆成两行。"""
    out: List[dict] = []
    for row in rows:
        items = list(row.get("items") or [])
        if len(items) < 2:
            out.append(row)
            continue
        date_items: List[dict] = []
        code_items: List[dict] = []
        rest: List[dict] = []
        for it in items:
            t = str(it.get("text", "")).strip()
            if t in ("代码", "数额"):
                code_items.append(it)
            elif _row_has_reporting_date([t]) or is_date_only_header_row_cells([t]):
                date_items.append(it)
            else:
                rest.append(it)
        if date_items and code_items:
            for group in (code_items, date_items, rest):
                if not group:
                    continue
                texts = [
                    str(it.get("text", "")).strip()
                    for it in sorted(group, key=lambda x: x.get("x0", 0))
                ]
                out.append({
                    "items": group,
                    "y_min": min(it.get("y0", 0) for it in group),
                    "y_max": max(it.get("y1", 0) for it in group),
                    "texts": texts,
                })
        else:
            out.append(row)
    return out


def _should_stop_label_wrap_merge(current_row: list, next_row: list) -> bool:
    """折行合并停止：下一行是独立小节标题，而非上一行标签续行。"""
    cur = _row_cells(current_row)
    nxt = _row_cells(next_row)
    if not any(nxt):
        return False
    if nxt[0] and _ROW_NUMBER_RE.match(nxt[0]):
        return True
    if sum(1 for c in nxt if is_numeric_data_cell(c)) >= 2:
        return True

    cur_label = cur[1] if len(cur) > 1 else ""
    nxt_text = " ".join(c for c in nxt if c).strip()
    if not nxt_text:
        return False

    # 上一行标签未收束（括号/「附」等）→ 更可能是 PDF 折行
    if cur_label.rstrip().endswith(
        ("（", "(", "附", "和", "及", "或", "的", "银", "负", "工", "权", "加权", "险加")
    ):
        return False

    if is_in_table_section_label_row(next_row):
        return True

    # 上一行已有数值，下一行仅长中文标题 → 新小节
    cur_has_value = any(
        is_numeric_data_cell(c) or str(c).strip() in ("-", "－", "—", "–")
        for c in cur[2:]
    )
    if cur_has_value and len(nxt_text) >= 6 and not nxt[0]:
        cn = len(re.findall(r"[\u4e00-\u9fff]", nxt_text))
        if cn >= 6 and not nxt_text.startswith("其中"):
            return True
    return False


def _merge_wrapped_label_rows(data: List[list]) -> List[list]:
    """合并行号行的折行标签续行（如 CC1 行13「损益」换行）。"""
    if not data:
        return data
    data = _merge_orphan_label_into_next_numbered_row(data)
    out: List[list] = []
    i = 0
    while i < len(data):
        row = [str(c) for c in data[i]]
        cells = _row_cells(row)
        if cells and cells[0] and _ROW_NUMBER_RE.match(cells[0]):
            j = i + 1
            while j < len(data):
                nxt = _row_cells(data[j])
                if not any(nxt):
                    j += 1
                    continue
                if _should_stop_label_wrap_merge(row, data[j]):
                    break
                if nxt[0] and _ROW_NUMBER_RE.match(nxt[0]):
                    break
                if sum(1 for c in nxt if is_numeric_data_cell(c)) >= 2:
                    break
                extra = " ".join(c for c in nxt if c)
                if extra:
                    label_col = 1 if len(row) > 1 else 0
                    if label_col < len(row):
                        prev = str(row[label_col]).strip()
                        row[label_col] = f"{prev} {extra}".strip() if prev else extra
                    else:
                        row.append(extra)
                j += 1
            out.append(row)
            i = j
        else:
            out.append(row)
            i += 1
    return out


def is_pillar_disclosure_table_body(data: List[list]) -> bool:
    """第三支柱披露表表体：列标/单位表头 + 报告期 + 行号数据行（无科目词表）。"""
    if not data or len(data) < 5:
        return False

    if _pillar_has_mid_repeat_header_band(data):
        return False

    if not _has_pillar_grid_header(data):
        return False

    if _count_period_header_rows(data[:8]) < 1:
        return False

    first_num = next(
        (
            i for i, r in enumerate(data)
            if _row_cells(r) and _ROW_NUMBER_RE.match(_row_cells(r)[0])
        ),
        len(data),
    )
    num_rows = _count_numbered_body_rows(data, first_num)
    if num_rows < 3:
        return False

    body_len = max(len(data) - first_num, 1)
    if num_rows < 5 and num_rows / body_len < 0.2:
        return False

    return True


def find_pillar_table_body_start_row(data: List[list]) -> int:
    """混合块中表体起始：表题行之后的首个列标/单位/指标表头行。"""
    caption_idx = -1
    for i, row in enumerate(data):
        if _row_has_pillar_table_caption(row):
            caption_idx = i

    start = caption_idx + 1 if caption_idx >= 0 else 0
    for i in range(start, len(data)):
        row = data[i]
        if _has_letter_column_header_row(row):
            return i
        if _is_rmb_unit_lead_row(row):
            return i
        if is_unit_column_header_row(row):
            return i
        cells = _row_cells(row)
        joined = "".join(cells)
        if ("人民币" in joined or "百万元" in joined) and is_multi_column_header_row(row):
            return i
        if is_multi_column_header_row(row) and len([c for c in cells if c]) >= 2:
            if i + 1 < len(data):
                nxt = data[i + 1]
                if _count_period_header_rows([nxt]) >= 1 or any(
                    is_year_cell(c) for c in _row_cells(nxt)
                ):
                    return i
    return 0


# 兼容旧名
is_regulatory_metrics_summary_table = is_pillar_disclosure_table_body
find_regulatory_table_body_start_row = find_pillar_table_body_start_row


def is_short_table_section_label_row(row: list) -> bool:
    """表内短小节标题（结构：无行号、无数据列、短中文）。"""
    cells = [c for c in _row_cells(row) if c]
    if not cells or len(cells) > 3:
        return False
    if cells[0] and _ROW_NUMBER_RE.match(cells[0]):
        return False
    if sum(
        1 for c in cells
        if is_numeric_data_cell(c) and str(c).strip() not in ("-", "－", "—", "–")
    ) > 0:
        return False
    joined = "".join(cells)
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if cn < 3 or cn > 40:
        return False
    if joined.endswith(("。", "；")) and cn > 12:
        return False
    if is_date_only_header_row_cells(row):
        return False
    if _row_has_reporting_date(row):
        return False
    if _is_rmb_unit_lead_row(row):
        return False
    if is_unit_column_header_row(row):
        return False
    if _has_letter_column_header_row(row):
        return False
    if is_table_header_band_row(row):
        return False
    return True


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
    for row in data[:6]:
        if is_table_header_band_row(row):
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

    result = _attach_lone_code_to_unit_header_row(result)
    result = _place_reporting_date_in_middle_column(result)
    return result


def _place_reporting_date_in_middle_column(data: List[list]) -> List[list]:
    """报告期日期应在中间列（与 a/数额 同列），勿落右列数值区。"""
    if not data:
        return data
    out = [list(r) for r in data]
    ncol = max((len(r) for r in out), default=0)
    if ncol < 3:
        return out
    for i, row in enumerate(out[:8]):
        cells = [str(c).strip() for c in row]
        non_empty = [(j, c) for j, c in enumerate(cells) if c]
        if len(non_empty) != 1:
            continue
        j, val = non_empty[0]
        if not _row_has_reporting_date([val]):
            continue
        if j == 1:
            continue
        fixed = [""] * ncol
        fixed[1] = val
        out[i] = fixed
    return out


def _effective_col_count(row: list) -> int:
    cells = _row_cells(row)
    return len([c for c in cells if c])


def is_main_table_data_row(row: list) -> bool:
    """主表数据行：行号 + 数值列，或左侧行标签 + ≥2 个参数数值。"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if len(non_empty) < 2:
        return False
    if cells and cells[0] and _ROW_NUMBER_RE.match(cells[0]):
        vals = sum(
            1 for c in cells[1:]
            if is_numeric_data_cell(c)
            or str(c).strip() in ("-", "－", "—", "–")
        )
        if vals >= 2:
            return True
        if vals >= 1 and len([c for c in cells[1:] if c]) >= 2:
            return True
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


def is_period_column_header_row(row: list) -> bool:
    """比较期间列标题行：日期列 + 发行/到期/募集等列标题（无数值）。"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if len(non_empty) < 2:
        return False
    if sum(1 for c in non_empty if is_numeric_data_cell(c)) > 0:
        return False
    joined = "".join(non_empty)
    date_like = sum(
        1 for c in non_empty
        if is_year_cell(c) or is_month_day_cell(c) or re.search(r"(?:19|20)\d{2}", c)
    )
    if date_like >= 2:
        return True
    if date_like >= 1 and any(kw in joined for kw in ("发行", "到期", "募集", "兑付")):
        return True
    if date_like >= 1 and joined.count("期数") >= 1 and joined.count("金额") >= 1:
        return True
    return False


def is_unit_column_header_row(row: list) -> bool:
    """单位说明 + 列名行，如（人民币百万元，期数除外）+ 期数/金额…"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if len(non_empty) < 2:
        return False
    if sum(1 for c in non_empty if is_numeric_data_cell(c)) > 0:
        return False
    first = non_empty[0]
    has_unit = (
        ("人民币" in first or "百万元" in first or "万元" in first)
        and first.startswith(("（", "("))
    ) or first.startswith(("（人民币", "(人民币"))
    joined = "".join(non_empty)
    if has_unit and joined.count("期数") >= 1 and joined.count("金额") >= 1:
        return True
    if has_unit and joined.count("占比") >= 2 and joined.count("金额") >= 2:
        return True
    return False


def is_table_header_band_row(row: list) -> bool:
    """表头带内合法行（日期/单位/多列列名），不应拆成独立 text。"""
    return (
        is_date_only_header_row_cells(row)
        or is_multi_column_header_row(row)
        or is_period_column_header_row(row)
        or is_unit_column_header_row(row)
    )


def is_table_section_label_row(row: list) -> bool:
    """表内小节标题行（无数值、短标签），应留在表中。"""
    return is_in_table_section_label_row(row) or is_short_table_section_label_row(row)


def is_note_section_header_row(row: list) -> bool:
    """附注章节行（如 31 / (1) + 标题），应抽出为 text 而非表行。"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    first = non_empty[0].strip()
    rest = "".join(non_empty[1:])
    if re.match(r"^\d{1,2}$", first) and len(rest) >= 3:
        return True
    if re.match(r"^[（(]\d+[)）]$", first) and len(rest) >= 6:
        return True
    return False


def is_embedded_paragraph_row(row: list) -> bool:
    """表内嵌段落行（应抽出为 text）。"""
    cells = _row_cells(row)
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    if is_note_section_header_row(row):
        return True
    if is_date_only_header_row_cells(row):
        return False
    if is_table_section_label_row(row):
        return False
    # 表头带行须在表尾注释判定之前（期数/金额列名易被误判为长中文注释）
    if is_table_header_band_row(row):
        return False
    if is_table_tail_annotation_row(row, max(len(row), 1)):
        return True
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
        if is_short_table_section_label_row(row):
            return False
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


_SECTION_MARKER_RE = re.compile(
    r"^[（(]\s*[一二三四五六七八九十百千\d]+[）)]"
)


def _is_section_marker_row(row: list) -> bool:
    """表内小节编号行，如（一）（二）——结构特征，非科目关键词。"""
    joined = "".join(c for c in _row_cells(row) if c).strip()
    return bool(_SECTION_MARKER_RE.match(joined))


def _has_following_main_data_row(
    data: List[list], start: int, lookahead: int = 8,
) -> bool:
    """后续是否出现主体数据行（用于表头带/小节行之后）。"""
    for j in range(start + 1, min(start + 1 + lookahead, len(data))):
        if is_main_table_data_row(data[j]):
            return True
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
            # 小节编号行（一）（二）后接数据 → 留在表内
            if _is_section_marker_row(row) and _has_following_main_data_row(data, i):
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


def _apply_tail_strip_to_table(t: dict) -> None:
    """表文拆分后再剥离主体数据后的连续注释行（如 合计 后的 (1) 说明）。"""
    data = t.get("data", [])
    if not data:
        return
    col_w = max((len(r) for r in data), default=0) or int(t.get("cols", 0) or 0)
    rows_before = len(data)
    data, fn_texts = strip_tail_annotation_rows_from_data(data, col_w)
    if not fn_texts:
        return
    t["data"] = data
    t["rows"] = len(data)
    t["cols"] = max((len(r) for r in data), default=0)
    attach_stripped_footnotes(t, fn_texts, rows_before_strip=rows_before)


def _build_regulatory_table_only_entry(table: dict, table_data: List[list]) -> dict:
    """输出单张披露表（折行合并 + 表头规范化 + 表尾脚注剥离）。"""
    t = copy.deepcopy(table)
    t["type"] = "table"
    merged = _merge_wrapped_label_rows(table_data)
    t["data"] = normalize_table_header_columns(merged)
    t["rows"] = len(t["data"])
    t["cols"] = max((len(r) for r in t["data"]), default=0)
    t["_header_normalized"] = True
    t["segment_source"] = "pillar_disclosure_table"
    _apply_tail_strip_to_table(t)
    return t


def _split_narrative_and_regulatory_table(
    table: dict,
    body_start: int,
) -> List[dict]:
    """页内说明文字 + 第三支柱披露表：上文下表，表体不再拆分。"""
    data = table.get("data", [])
    page = table.get("page", 0)
    total = len(data)
    results: List[dict] = []

    narrative_lines = [
        _row_to_text(row).strip()
        for row in data[:body_start]
        if _row_to_text(row).strip()
    ]
    narrative = "\n".join(narrative_lines)
    if narrative:
        y0, y1 = _estimate_row_y_range(table, 0, body_start, total)
        results.append({
            "type": "text",
            "page": page,
            "y0": y0,
            "y1": y1,
            "x0": table.get("x0", 0),
            "x1": table.get("x1", 0),
            "context_text": narrative,
            "data": narrative,
            "extractor": table.get("extractor", ""),
            "segment_source": "pillar_disclosure_narrative",
            "confidence": 0.80,
            "_segment_seq": 0,
            "table_category": "文本段落",
            "parse_status": "success",
        })

    tbl = _build_regulatory_table_only_entry(table, data[body_start:])
    y0, y1 = _estimate_row_y_range(table, body_start, total, total)
    tbl["y0"] = y0
    tbl["y1"] = y1
    tbl["_segment_seq"] = 1
    results.append(tbl)

    print(
        f"  [披露表拆分] P{page}: 说明文字 + 1张表 "
        f"({body_start}行说明 + {tbl['rows']}行表体)"
    )
    return results


def split_mixed_table_entry(table: dict) -> List[dict]:
    """
    将单个混合 table 条目拆为多个 table / text 条目。
    若无需拆分，返回 [table]。

    优先：第三支柱披露表（结构识别）→ 说明+单表，不碎拆。
    否则：沿用原 split_mixed_table_data 表文拆分 + 脚注剥离。
    """
    data = table.get("data", [])
    if len(data) < 4:
        return [table]

    body_start = find_pillar_table_body_start_row(data)
    table_body = data[body_start:] if body_start > 0 else data
    if is_pillar_disclosure_table_body(table_body):
        if body_start > 0:
            return _split_narrative_and_regulatory_table(table, body_start)
        return [_build_regulatory_table_only_entry(table, data)]

    if is_pillar_disclosure_table_body(data):
        return [_build_regulatory_table_only_entry(table, data)]

    segments = split_mixed_table_data(data)
    if len(segments) <= 1 and segments and segments[0][0] == "table":
        t = copy.deepcopy(table)
        t["data"] = segments[0][1]
        t["rows"] = len(t["data"])
        t["cols"] = max((len(r) for r in t["data"]), default=0)
        if t["data"] != data:
            t["_header_normalized"] = True
        _apply_tail_strip_to_table(t)
        return [t]

    page = table.get("page", 0)
    total_rows = len(data)
    fn_recs = pop_footnote_bundle(table)
    results: List[dict] = []

    for seg_idx, (kind, payload, start_row, end_row) in enumerate(segments):
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
                "_segment_seq": seg_idx,
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
            t["_segment_seq"] = seg_idx
            _apply_tail_strip_to_table(t)
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
