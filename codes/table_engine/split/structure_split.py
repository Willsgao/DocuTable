# -*- coding: utf-8
"""表内结构分裂（重复表头带 / 连续空白数据行 / 表题）。"""

from __future__ import annotations

import copy
import re
from typing import List, Optional, Tuple

from codes.table_engine.conservation.item_conservation import (
    apply_table_transform_guard,
    assign_split_scope_source_items,
    expand_scope_source_items,
    refresh_scope_source_items,
    table_source_item_ids,
)
from codes.table_engine.geometry.grid_infer import (
    reinfer_table_column_grid,
    repair_compound_header_items_from_template,
)
from codes.table_engine.geometry.numeric import is_month_day_cell, is_numeric_data_cell, is_report_date_cell, is_year_cell
from codes.table_engine.models import DocumentEntry, PageSource, StructuredTable, TextBlock
from codes.table_engine.scope.header_scope import (
    has_letter_column_header_row,
    is_annual_report_column_header_row,
    is_annual_report_unit_row,
    is_rmb_unit_lead_row,
    row_has_pillar_table_caption,
)
from codes.table_engine.split.row_classify import (
    find_body_structure_break,
    is_entity_scope_header_row,
    is_inter_table_narrative_row,
    is_likely_next_table_header_row,
    is_prependable_header_band_row,
    row_has_reporting_period_date_in_values,
    row_has_value_data,
    row_has_body_value_data,
    row_is_intra_table_label_row,
    row_is_intra_table_period_footer_row,
    row_is_annual_subsection_caption_row,
    row_is_note_section_caption_row,
    row_is_table_tail_section_caption_row,
    row_values_all_empty,
)
from codes.table_engine.split.boundary_overlap import (
    _NO_NARRATIVE_MAX_GAP_PT,
    count_lower_leading_duplicate_rows,
    count_trailing_duplicate_suffix_block,
    detect_table_boundary_overlap,
    iter_overlap_candidate_table_pairs,
    rows_share_duplicate_values,
    should_merge_label_suffix_pair,
    should_merge_wrapped_label_head_into_next,
    should_merge_reason_column_wrap_pair,
    address_wrap_column_index,
    should_merge_address_column_wrap_pair,
    tables_x_overlap,
    tables_y_overlap,
)
from codes.table_engine.split.table_text_split import (
    find_first_serial_block_end,
    find_last_serial_data_row,
    slice_structured_table,
    split_structured_table,
    _text_block_to_entry,
)
from codes.table_engine.table_access import dense_rows

_YEAR_IN_TEXT_RE = re.compile(r"(?:19|20)\d{2}年?")
_FOOTNOTE_MARKER_RE = re.compile(r"^[（(]?\d+[)）\.．、]")
_DASH_VALUES = frozenset(("-", "－", "—", "–"))


def _row_cells(row: List[str]) -> List[str]:
    return [str(c).strip() for c in row if str(c).strip()]


def _row_all_cells(row: List[str]) -> List[str]:
    return [str(c).strip() for c in row]


def _column_header_fingerprint(cells: List[str]) -> Optional[frozenset]:
    labels = {
        c.lower()
        for c in cells
        if c and len(c) <= 8 and not is_numeric_data_cell(c) and not is_year_cell(c)
    }
    labels = {x for x in labels if not _YEAR_IN_TEXT_RE.search(x)}
    return frozenset(labels) if len(labels) >= 2 else None


def _header_fingerprints_match(a: frozenset, b: frozenset) -> bool:
    if not a or not b:
        return False
    inter = len(a & b)
    return inter >= 2 and inter >= min(len(a), len(b)) * 0.6


def _is_reporting_period_date_row(row: List[str]) -> bool:
    cells = _row_cells(row)
    if not cells:
        return False
    if any(is_report_date_cell(c) or is_year_cell(c) or is_month_day_cell(c) for c in cells):
        return True
    joined = "".join(cells)
    return bool(_YEAR_IN_TEXT_RE.search(joined)) and not any(
        is_numeric_data_cell(c) for c in cells
    )


def _is_main_data_row(row: List[str]) -> bool:
    cells = _row_cells(row)
    if len(cells) < 2:
        return False
    if cells[0] and re.match(r"^\d+[a-z]?$", cells[0], re.I):
        vals = sum(
            1 for c in cells[1:]
            if is_numeric_data_cell(c) or c in _DASH_VALUES
        )
        return vals >= 1
    return sum(1 for c in cells if is_numeric_data_cell(c)) >= 2


def _row_has_blank_value_columns(row: List[str]) -> bool:
    cells = _row_all_cells(row)
    if not cells:
        return True
    vals = cells[1:] if len(cells) > 1 else []
    if not vals:
        return True
    return not any(
        is_numeric_data_cell(v) or v in _DASH_VALUES
        for v in vals
        if v
    )


def _is_annotation_like_row(row: List[str]) -> bool:
    cells = _row_cells(row)
    if not cells:
        return True
    first = cells[0]
    if _FOOTNOTE_MARKER_RE.match(first):
        return True
    joined = "".join(cells)
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if cn >= 10 and _row_has_blank_value_columns(row):
        return True
    if "下表列示" in joined or row_has_pillar_table_caption(cells):
        return True
    return False


def _reporting_period_label_col_index(row: List[str]) -> int:
    """报告期日期所在列下标；-1 表示无。"""
    cells = _row_all_cells(row)
    for i, c in enumerate(cells):
        t = str(c).strip()
        if not t:
            continue
        if is_year_cell(t) or (is_report_date_cell(t) and "年" in t):
            return i
    return -1


def _first_column_header_fingerprint(rows: List[List[str]]) -> Optional[frozenset]:
    for row in rows:
        fp = _column_header_fingerprint(_row_cells(row))
        if fp:
            return fp
    return None


def _is_same_table_period_continuation(
    rows: List[List[str]],
    period_idx: int,
    first_fp: Optional[frozenset],
) -> bool:
    """居中报告期（2023年）后复用相同列标带 → 同表下一期，不拆。"""
    if first_fp is None or period_idx < 0 or period_idx >= len(rows):
        return False
    if not _is_centered_table_period_header_row(rows[period_idx]):
        return False
    for j in range(period_idx + 1, min(period_idx + 12, len(rows))):
        cells = _row_cells(rows[j])
        if not cells:
            continue
        if row_has_pillar_table_caption(cells):
            return False
        fp = _column_header_fingerprint(cells)
        if fp and _header_fingerprints_match(first_fp, fp):
            return True
        if _is_main_data_row(rows[j]):
            break
    return False


def _has_recent_period_section(rows: List[List[str]], idx: int, *, lookback: int = 10) -> bool:
    """前几行出现独立年份报告期行（2023年/2024年）。"""
    for j in range(max(0, idx - lookback), idx):
        if _is_centered_table_period_header_row(rows[j]):
            return True
        cells = _row_cells(rows[j])
        if len(cells) == 1 and is_year_cell(cells[0]):
            return True
    return False


def _is_centered_table_period_header_row(row: List[str]) -> bool:
    """居中表头报告期：日期在标签列右侧、无表体金额；含完整日期或独立年份格。"""
    if not _is_reporting_period_header_row(row):
        return False
    cells = _row_all_cells(row)
    for c in cells:
        t = str(c).strip()
        if not t:
            continue
        if is_year_cell(t):
            return True
        if is_report_date_cell(t) and ("月" in t or "日" in t):
            return True
    return False


def _value_column_numeric_count(row: List[str]) -> int:
    """值列（col1+）含数值或带符号金额的格子数。"""
    cells = _row_all_cells(row)
    vals = cells[1:] if len(cells) > 1 else []
    count = 0
    for v in vals:
        t = str(v).strip()
        if not t or t in _DASH_VALUES:
            continue
        if is_report_date_cell(t) or is_year_cell(t) or is_month_day_cell(t):
            continue
        if is_numeric_data_cell(t):
            count += 1
            continue
        if re.search(r"[\d,，]", t) and re.search(r"\d", t):
            count += 1
    return count


def _is_reporting_period_header_row(row: List[str]) -> bool:
    """表头报告期：日期在列标上方（常居中），值列无数值。"""
    if not _is_reporting_period_date_row(row):
        return False
    if _value_column_numeric_count(row) > 0:
        return False
    label_col = _reporting_period_label_col_index(row)
    if label_col <= 0:
        return False
    return True


def _is_reporting_period_body_row(row: List[str]) -> bool:
    """表体报告期：日期在首列（左对齐）且值列含金额。"""
    if not _is_reporting_period_date_row(row):
        return False
    label_col = _reporting_period_label_col_index(row)
    if label_col != 0:
        return False
    return _value_column_numeric_count(row) >= 1 or _is_main_data_row(row)


def _split_at_repeated_header_band(rows: List[List[str]], header_idx: int) -> int:
    """重复列标带：仅当上一行是「表头报告期」时从报告期拆；含数值的报告期留上一段。"""
    if header_idx <= 0:
        return header_idx
    prev = rows[header_idx - 1]
    if _is_reporting_period_header_row(prev):
        return header_idx - 1
    return header_idx


def _is_note_section_caption_row(row: List[str]) -> bool:
    return row_is_note_section_caption_row(row)


def find_note_section_caption_break(rows: List[List[str]]) -> int:
    """同 scope 内下一张附注表节标题行 → 拆分起点。"""
    for i, row in enumerate(rows):
        if i < 2:
            continue
        if not _is_note_section_caption_row(row):
            continue
        if any(_is_main_data_row(rows[j]) for j in range(i)):
            return i
    return -1


def _row_is_next_table_section_caption(rows: List[List[str]], i: int) -> bool:
    """小节标题行：值列空、非叙述，且次行像表头带。"""
    if i < 0 or i >= len(rows) - 1:
        return False
    cells = _row_cells(rows[i])
    if not cells or not row_values_all_empty(rows[i]):
        return False
    if is_inter_table_narrative_row(rows[i]):
        return False
    label = cells[0]
    if not label or not re.search(r"[\u4e00-\u9fff]", label):
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", label))
    if cn < 6 or cn > 32:
        return False
    nxt = rows[i + 1]
    if row_has_body_value_data(nxt):
        return False
    return (
        row_has_reporting_period_date_in_values(nxt)
        or is_likely_next_table_header_row(nxt)
        or is_prependable_header_band_row(nxt)
    )


def find_next_table_section_caption_break(rows: List[List[str]]) -> int:
    """表体结束后、下一张表表头前的小节标题（如「资产负债表日重大交易的余额」）。"""
    for i in range(2, len(rows) - 1):
        if not _row_is_next_table_section_caption(rows, i):
            continue
        if any(_is_main_data_row(rows[j]) for j in range(max(0, i - 14), i)):
            return i
    return -1


def find_mid_table_caption_break(rows: List[List[str]]) -> int:
    for i, row in enumerate(rows):
        if i < 2:
            continue
        cells = _row_cells(row)
        if not row_has_pillar_table_caption(cells):
            continue
        if any(_is_main_data_row(rows[j]) for j in range(i)):
            return i
    return -1


def find_repeated_header_band_break(rows: List[List[str]]) -> int:
    """表体进行中出现第二次单位行 / 列标行 → 新表表头带。"""
    first_unit = -1
    first_letters = -1
    data_rows = 0

    for i, row in enumerate(rows):
        cells = _row_cells(row)
        if _is_main_data_row(row):
            data_rows += 1
            continue
        if data_rows < 3:
            if is_rmb_unit_lead_row(cells):
                first_unit = i
            if has_letter_column_header_row(cells):
                first_letters = i
            continue

        if is_rmb_unit_lead_row(cells) and first_unit >= 0 and i > first_unit + 1:
            return i
        if has_letter_column_header_row(cells) and first_letters >= 0 and i > first_letters + 2:
            return _split_at_repeated_header_band(rows, i)

        fp = _column_header_fingerprint(cells)
        if fp and first_letters >= 0:
            first_fp = _column_header_fingerprint(_row_cells(rows[first_letters]))
            if first_fp and _header_fingerprints_match(fp, first_fp) and i > first_letters + 2:
                return _split_at_repeated_header_band(rows, i)
    return -1


def find_repeated_annual_column_header_break(rows: List[List[str]]) -> int:
    """同 scope 内第二次「项目 + 报告期列」表头带 → 新表起点（如补充指标双表）。

    若首段无表头、直接是数据行，其后出现表头带也拆（续表/缺表头回补失败时兜底）。
    """
    body_rows = 0
    for i, row in enumerate(rows):
        cells = _row_cells(row)
        if is_annual_report_column_header_row(cells):
            if body_rows >= 1:
                return i
            body_rows = 0
            continue
        if _is_main_data_row(row) or row_has_body_value_data(row):
            body_rows += 1
    return -1


def _tail_has_new_table_header(rows: List[List[str]], start: int, *, scan: int = 10) -> bool:
    for row in rows[start: min(start + scan, len(rows))]:
        cells = _row_cells(row)
        if row_has_pillar_table_caption(cells):
            return True
        if is_rmb_unit_lead_row(cells):
            return True
        if has_letter_column_header_row(cells) and _is_reporting_period_date_row(
            rows[min(start + scan, len(rows)) - 1] if start > 0 else row
        ):
            return True
    return False


def find_trailing_next_table_header_break(rows: List[List[str]]) -> int:
    """表体末行之后出现下一张表表头带（本集团/单位/日期列等）→ 拆分起点。"""
    from codes.table_engine.split.row_classify import find_last_body_value_row

    last_body = find_last_body_value_row(rows)
    if last_body < 0 or last_body >= len(rows) - 1:
        return -1

    tail_start = last_body + 1
    tail = rows[tail_start:]
    if not tail or any(row_has_value_data(r) for r in tail):
        return -1

    i = tail_start
    while i < len(rows) and is_inter_table_narrative_row(rows[i]):
        i += 1
    if i >= len(rows) or not is_likely_next_table_header_row(rows[i]):
        return -1

    for j in range(i, len(rows)):
        row = rows[j]
        if not _row_cells(row):
            continue
        if is_inter_table_narrative_row(row):
            return -1
        if not (
            is_likely_next_table_header_row(row)
            or is_prependable_header_band_row(row)
        ):
            return -1
    return i


def find_consecutive_blank_value_break(
    rows: List[List[str]],
    *,
    min_run: int = 2,
) -> int:
    """序号表体结束后，连续多行无数值列，且其后出现新表头/表题 → 拆分。"""
    last_serial = find_last_serial_data_row(rows)
    if last_serial < 0:
        return -1

    run = 0
    for i in range(last_serial + 1, len(rows)):
        row = rows[i]
        if _is_main_data_row(row):
            run = 0
            continue
        if _row_has_blank_value_columns(row) and (
            _is_annotation_like_row(row) or len(_row_cells(row)) <= 2
        ):
            run += 1
            if run >= min_run and _tail_has_new_table_header(rows, i - run + 1):
                return i - run + 1
        else:
            run = 0
    return -1


def find_post_serial_annotation_break(rows: List[List[str]]) -> int:
    """仅当脚注块之后还有第二张表表头时，在脚注块起点拆分。"""
    last_serial = find_last_serial_data_row(rows)
    if last_serial < 0 or last_serial >= len(rows) - 1:
        return -1
    foot_start = -1
    for i in range(last_serial + 1, len(rows)):
        cells = _row_cells(rows[i])
        if row_has_pillar_table_caption(cells) or is_rmb_unit_lead_row(cells):
            return i
        if cells and _FOOTNOTE_MARKER_RE.match(cells[0]):
            if foot_start < 0:
                foot_start = i
    if foot_start >= 0 and _tail_has_new_table_header(rows, foot_start):
        return foot_start
    return -1


def find_mid_table_period_break(
    rows: List[List[str]],
    *,
    region_continuation_merged: bool = False,
) -> int:
    if len(rows) < 8:
        return -1
    first_fp: Optional[frozenset] = None
    body_rows = 0
    for i, row in enumerate(rows):
        fp = _column_header_fingerprint(_row_cells(row))
        if first_fp is None:
            if fp:
                first_fp = fp
            continue
        if _is_main_data_row(row):
            body_rows += 1
            continue
        if body_rows < 2:
            continue
        if row_is_intra_table_period_footer_row(row):
            continue
        if _is_centered_table_period_header_row(row):
            if region_continuation_merged and _is_same_table_period_continuation(
                rows, i, first_fp
            ):
                continue
            return i
        if _is_reporting_period_date_row(row):
            if i + 1 < len(rows):
                fp_next = _column_header_fingerprint(_row_cells(rows[i + 1]))
                if fp_next and first_fp and _header_fingerprints_match(first_fp, fp_next):
                    if _is_reporting_period_header_row(row):
                        if region_continuation_merged and _is_same_table_period_continuation(
                            rows, i, first_fp
                        ):
                            continue
                        return i
                    return i + 1
            continue
        if fp and first_fp and _header_fingerprints_match(first_fp, fp):
            if region_continuation_merged and _has_recent_period_section(rows, i):
                continue
            return _split_at_repeated_header_band(rows, i)
    return -1


def _is_intra_table_section_label(row: List[str]) -> bool:
    """表内小节标题（吸收存款/发放贷款和垫款/资产/负债），非新表起点。"""
    cells = _row_cells(row)
    if not cells:
        return False
    if len(cells) == 1 and cells[0] in ("资产", "负债"):
        return True
    return row_is_intra_table_label_row(row)


def _row_continues_existing_grid(rows: List[List[str]], i: int) -> bool:
    """值列有数据且与上方表体同结构（中间可有空行/节标题）→ 续表，不拆。"""
    if i <= 0 or not row_has_value_data(rows[i]):
        return False
    for j in range(i - 1, -1, -1):
        if _is_intra_table_section_label(rows[j]):
            return True
        if row_has_value_data(rows[j]):
            return True
        cells = _row_cells(rows[j])
        if not cells:
            continue  # 整行空白：跳过，继续向上找表体
        return False
    return False


def _body_structure_break_allowed(rows: List[List[str]], break_at: int) -> bool:
    """a/b/c 序号披露表：仅当拆分点处有明显新表头时才允许 body_structure 拆分。"""
    from codes.table_engine.split.table_text_split import _has_pillar_grid_header

    if break_at < 0 or not _has_pillar_grid_header(rows):
        return True
    cells = _row_cells(rows[break_at])
    if has_letter_column_header_row(cells):
        return True
    if is_rmb_unit_lead_row(cells):
        return True
    if row_has_pillar_table_caption(cells):
        return True
    if is_annual_report_column_header_row(cells):
        return True
    return False


def _rows_have_new_table_signal_after(rows: List[List[str]], caption_i: int) -> bool:
    """小节标题之后是否像新表开始（单位/报告期表头/并表列标等）。"""
    for j in range(caption_i + 1, min(caption_i + 8, len(rows))):
        cells = _row_cells(rows[j])
        if not cells:
            continue
        joined = " ".join(cells)
        if is_rmb_unit_lead_row(cells) or is_annual_report_unit_row(cells):
            return True
        if is_annual_report_column_header_row(cells):
            return True
        if "项目" in joined and (
            "12 月 31 日" in joined
            or "12月31日" in joined
            or "年" in joined
        ):
            return True
        if "并表" in joined or "非并表" in joined:
            return True
        if row_is_annual_subsection_caption_row(rows[j]):
            break
    # 标题后仍有内容行 → 即使表头识别弱也允许拆（避免两表粘死）
    return caption_i + 1 < len(rows)


def find_annual_subsection_caption_break(rows: List[List[str]]) -> int:
    """年报（四）（五）类小节标题：表体后、下一张表前 → 拆分起点。

    注意：两表被误并时，整表 last_body 可能落在后表，不能要求
    caption_row > last_body；只要上方已有表体、下方还有内容即切开。
    """
    for i, row in enumerate(rows):
        if i < 2:
            continue
        if not row_is_annual_subsection_caption_row(row):
            continue
        if not any(_is_main_data_row(rows[j]) for j in range(i)):
            continue
        if not _rows_have_new_table_signal_after(rows, i):
            continue
        return i
    return -1


def find_structure_break_row(
    rows: List[List[str]],
    *,
    region_continuation_merged: bool = False,
) -> int:
    if len(rows) < 4:
        return -1

    subsec_break = find_annual_subsection_caption_break(rows)
    # 硬规则：年报（四）（五）等小节标题强制拆分，不受「后表也有数值」影响，
    # 也不受 region_continuation_merged / 相似报告期表头过滤抑制。
    if subsec_break >= 0 and subsec_break < len(rows) - 1:
        return subsec_break

    # 硬规则：表体后再出现「项目+报告期」同类表头 → 新表，禁止当续表压掉
    repeated_hdr = find_repeated_annual_column_header_break(rows)
    if repeated_hdr >= 2 and repeated_hdr < len(rows) - 1:
        return repeated_hdr

    repeated_band = find_repeated_header_band_break(rows)
    if repeated_band >= 2 and repeated_band < len(rows) - 1:
        return repeated_band

    last_serial = find_last_serial_data_row(rows)
    first_block_end = find_first_serial_block_end(rows)
    min_break = 2
    if first_block_end >= 0:
        min_break = max(min_break, first_block_end + 1)
    elif last_serial >= 0:
        min_break = max(min_break, last_serial + 1)

    body_break = find_body_structure_break(rows)
    candidates = [
        body_break if _body_structure_break_allowed(rows, body_break) else -1,
        find_repeated_annual_column_header_break(rows),
        find_note_section_caption_break(rows),
        find_next_table_section_caption_break(rows),
        find_mid_table_caption_break(rows),
        find_repeated_header_band_break(rows),
        find_post_serial_annotation_break(rows),
        find_consecutive_blank_value_break(rows),
        find_mid_table_period_break(
            rows, region_continuation_merged=region_continuation_merged
        ),
        find_trailing_next_table_header_break(rows),
    ]

    for i in range(3, len(rows) - 2):
        row = _row_cells(rows[i])
        if len(row) < 2:
            if _is_reporting_period_date_row(rows[i]) and i + 1 < len(rows):
                if _column_header_fingerprint(_row_cells(rows[i + 1])):
                    if any(_is_main_data_row(rows[j]) for j in range(i)):
                        if _is_reporting_period_header_row(rows[i]):
                            candidates.append(i)
                        elif _is_reporting_period_body_row(rows[i]):
                            candidates.append(i + 1)
            continue
        widths = [len(_row_cells(r)) for r in rows[max(0, i - 3):i]]
        if widths and len(row) >= max(widths) + 2:
            if any(_is_main_data_row(rows[j]) for j in range(i)):
                if _is_main_data_row(rows[i]) or _row_continues_existing_grid(rows, i):
                    continue
                if not _tail_has_new_table_header(rows, i):
                    continue
                candidates.append(i)

    valid = [c for c in candidates if min_break <= c < len(rows) - 1]
    if region_continuation_merged:
        first_fp = _first_column_header_fingerprint(rows)
        if first_fp is not None:
            valid = [
                c
                for c in valid
                if not _is_same_table_period_continuation(rows, c, first_fp)
                and not (
                    _has_recent_period_section(rows, c)
                    and _column_header_fingerprint(_row_cells(rows[c]))
                    and _header_fingerprints_match(
                        first_fp, _column_header_fingerprint(_row_cells(rows[c]))
                    )
                )
            ]
    filtered: List[int] = []
    for c in valid:
        if is_inter_table_narrative_row(rows[c]):
            if any(
                _row_is_next_table_section_caption(rows, j)
                for j in range(c + 1, min(c + 3, len(rows)))
            ):
                continue
        filtered.append(c)
    if filtered:
        valid = filtered
    return min(valid) if valid else -1


def _peel_leading_narrative_rows(
    table: StructuredTable,
) -> Tuple[StructuredTable, Optional[TextBlock]]:
    """拆分后子表首行若为表间叙述，剥为 TextBlock。

    连续 ≥3 行通栏无金额一并剥（与 word_segment 表顶剥离一致）。
    """
    from codes.table_engine.split.row_classify import leading_spanning_prose_run_end

    rows = dense_rows(table)
    peel = 0
    while peel < len(rows) and is_inter_table_narrative_row(rows[peel]):
        peel += 1
    run_end = leading_spanning_prose_run_end(rows, start=peel, min_run=3)
    if run_end > peel:
        peel = run_end
        while peel < len(rows) and is_inter_table_narrative_row(rows[peel]):
            peel += 1
    if peel <= 0:
        return table, None
    peeled = slice_structured_table(table, 0, peel)
    trimmed = slice_structured_table(table, peel)
    lines = [
        " ".join(str(c).strip() for c in row if str(c).strip()).strip()
        for row in dense_rows(peeled)
    ]
    text = "\n".join(line for line in lines if line)
    if not text.strip():
        return table, None
    block = TextBlock(
        page=table.page,
        y0=peeled.y0,
        y1=peeled.y1,
        text=text,
        source_items=sorted(table_source_item_ids(peeled)),
    )
    return trimmed, block


def _copy_row_values_to_matrix_row(
    matrix_row: List,
    value_row: List[str],
) -> None:
    for ci in range(1, min(len(matrix_row), len(value_row))):
        cell = matrix_row[ci]
        if cell is None:
            continue
        val = str(value_row[ci] or "").strip()
        if val:
            cell.text = val


def _absorb_matrix_row_into_prev(prev_row: List, cur_row: List) -> None:
    """合并行时把 cur 的 source_items 并入 prev，避免守恒补挂孤儿 item。"""
    for ci in range(max(len(prev_row), len(cur_row))):
        src_cell = cur_row[ci] if ci < len(cur_row) else None
        if src_cell is None:
            continue
        if ci >= len(prev_row) or prev_row[ci] is None:
            prev_row[ci] = copy.deepcopy(src_cell)
            continue
        dst = prev_row[ci]
        ids = list(dst.source_items or [])
        for sid in src_cell.source_items or []:
            if sid not in ids:
                ids.append(sid)
        dst.source_items = ids


def _merge_label_suffix_from_split_tail(
    t1: StructuredTable,
    t2: StructuredTable,
) -> Tuple[StructuredTable, StructuredTable]:
    """拆表后下一段首行若为标签尾片，并回上一段末行，去掉重复数值行。"""
    rows1 = dense_rows(t1)
    rows2 = dense_rows(t2)
    if not rows1 or not rows2 or not should_merge_label_suffix_pair(rows1[-1], rows2[0]):
        return t1, t2

    last_ri = len(t1.rows) - 1
    suffix = _row_cells(rows2[0])[0]
    last_row = t1.rows[last_ri]
    if last_row and last_row[0] is not None:
        base = str(last_row[0].text or "").strip()
        last_row[0].text = f"{base}{suffix}" if base else suffix

    tail_has_vals = row_has_value_data(rows2[0])
    head_has_vals = row_has_value_data(rows1[-1])
    if tail_has_vals and not head_has_vals:
        _copy_row_values_to_matrix_row(last_row, rows2[0])

    cur_matrix_row = t2.rows[0] if t2.rows else []
    _absorb_matrix_row_into_prev(last_row, cur_matrix_row)

    if len(t2.rows) > 1:
        t2 = slice_structured_table(t2, 1)
    else:
        return t1, slice_structured_table(t2, 1, 1)
    return t1, t2


def repair_duplicate_row_blocks(table: StructuredTable) -> StructuredTable:
    """表末或表内连续重复行块 → 删掉后出现的副本。"""
    rows = dense_rows(table)
    block = count_trailing_duplicate_suffix_block(rows)
    if block <= 0:
        return table
    trimmed = slice_structured_table(table, 0, len(rows) - block)
    refresh_scope_source_items(trimmed)
    return trimmed


def _reason_wrap_target_cell(row: list) -> Optional[object]:
    """变化原因表：折行尾片并入末列说明格（非百分比/金额列）。"""
    best = None
    best_col = -1
    for cell in row:
        if cell is None:
            continue
        t = str(cell.text or "").strip()
        if not t:
            continue
        col = int(getattr(cell, "col", 0))
        if col <= 0:
            continue
        if t.endswith("%") and re.match(r"^-?[\d,，.]+\%$", t.replace(" ", "")):
            continue
        if re.match(r"^-?[\d,，.]+\%$", t.replace(" ", "")):
            continue
        if col > best_col:
            best_col = col
            best = cell
    if best is not None:
        return best
    if row:
        return row[-1]
    return None


def repair_glued_percent_reason_cells(table: StructuredTable) -> StructuredTable:
    """矩阵格内多字段粘连 → 统一分解（兼容旧名）。"""
    from codes.table_engine.geometry.cell_decomposition import decompose_table

    return decompose_table(table)


def apply_change_table_mixed_cell_repair(
    entries: List[DocumentEntry],
) -> List[DocumentEntry]:
    """守恒/重分列后：统一矩阵分解（兼容旧名）。"""
    return apply_cell_decomposition(entries)


def apply_cell_decomposition(entries: List[DocumentEntry]) -> List[DocumentEntry]:
    """对仍含数值+文本粘连或主要原因错列的表强制拆列/纠列。"""
    from codes.table_engine.geometry.cell_decomposition import (
        _table_has_merged_cells,
        _table_has_misplaced_reason_labels,
        _table_is_change_reason_table,
        decompose_table,
        relocate_misplaced_reason_labels,
    )

    for entry in entries:
        if entry.kind != "table" or entry.table is None:
            continue
        needs_decompose = _table_has_merged_cells(entry.table)
        needs_relocate = _table_has_misplaced_reason_labels(entry.table)
        if not needs_decompose and not needs_relocate:
            continue
        if needs_decompose:
            entry.table = repair_wrapped_label_suffix_rows(entry.table)
        elif _table_is_change_reason_table(entry.table):
            entry.table = relocate_misplaced_reason_labels(decompose_table(entry.table))
        refresh_scope_source_items(entry.table)
    return entries


def repair_wrapped_label_suffix_rows(
    table: StructuredTable,
    *,
    _depth: int = 0,
) -> StructuredTable:
    """表内相邻行：上行标签 + 下行「净额」等同值尾片 → 合并为一行。"""
    table = repair_glued_percent_reason_cells(table)
    rows = dense_rows(table)
    if len(rows) < 2:
        return table

    remove_at: List[int] = []
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if should_merge_wrapped_label_head_into_next(prev, cur):
            cur_row = table.rows[i]
            prefix = str(prev[0] or "").strip()
            if cur_row and cur_row[0] is not None:
                base = str(cur_row[0].text or "").strip()
                cur_row[0].text = f"{prefix}{base}" if base else prefix
            remove_at.append(i - 1)
            continue
        if should_merge_reason_column_wrap_pair(prev, cur):
            prev_row = table.rows[i - 1]
            cur_cells = [str(c).strip() for c in cur if str(c).strip()]
            suffix = cur_cells[0] if cur_cells else ""
            target = _reason_wrap_target_cell(prev_row)
            if target is not None:
                base = str(target.text or "").strip()
                target.text = f"{base}{suffix}" if base else suffix
            remove_at.append(i)
            continue
        if should_merge_address_column_wrap_pair(prev, cur):
            prev_row = table.rows[i - 1]
            ci = address_wrap_column_index(cur)
            suffix = str(cur[ci] or "").strip() if ci is not None else ""
            if (
                ci is not None
                and prev_row
                and ci < len(prev_row)
                and prev_row[ci] is not None
                and suffix
            ):
                base = str(prev_row[ci].text or "").strip()
                prev_row[ci].text = f"{base}{suffix}" if base else suffix
            remove_at.append(i)
            continue
        if not should_merge_label_suffix_pair(prev, cur):
            continue

        prev_row = table.rows[i - 1]
        suffix = _row_cells(cur)[0]
        if prev_row and prev_row[0] is not None:
            base = str(prev_row[0].text or "").strip()
            prev_row[0].text = f"{base}{suffix}" if base else suffix
        if row_has_value_data(cur) and not row_has_value_data(prev):
            _copy_row_values_to_matrix_row(prev_row, cur)
        _absorb_matrix_row_into_prev(prev_row, table.rows[i])
        remove_at.append(i)

    if not remove_at:
        return repair_duplicate_row_blocks(table)

    keep = [ri for ri in range(len(table.rows)) if ri not in set(remove_at)]
    out = copy.copy(table)
    out.rows = copy.deepcopy([table.rows[ri] for ri in keep])
    for ri, row in enumerate(out.rows):
        for cell in row:
            if cell is not None:
                cell.row = ri
    if out.rows:
        out.y0 = min(
            c.bbox.y0 for row in out.rows for c in row if c is not None
        )
        out.y1 = max(
            c.bbox.y1 for row in out.rows for c in row if c is not None
        )
    out = repair_duplicate_row_blocks(out)
    if _depth < 8:
        return repair_wrapped_label_suffix_rows(out, _depth=_depth + 1)
    return out


def _repair_duplicate_boundary_row(
    t1: StructuredTable,
    t2: StructuredTable,
) -> Tuple[StructuredTable, StructuredTable]:
    """下表首部与上表尾部重复的行 → 逐行从下表删除（折行尾片走 merge 逻辑）。"""
    rows1, rows2 = dense_rows(t1), dense_rows(t2)
    if not rows1 or not rows2:
        return t1, t2
    trim = count_lower_leading_duplicate_rows(rows1, rows2)
    if trim <= 0:
        if rows_share_duplicate_values(rows1[-1], rows2[0]):
            trim = 1
        else:
            return t1, t2
    if trim >= len(t2.rows):
        return t1, slice_structured_table(t2, len(t2.rows), len(t2.rows))
    t2 = slice_structured_table(t2, trim)
    refresh_scope_source_items(t2)
    return t1, t2


def _repair_boundary_overlap_pair(
    upper: DocumentEntry,
    lower: DocumentEntry,
) -> Tuple[Optional[DocumentEntry], Optional[DocumentEntry]]:
    if upper.table is None or lower.table is None:
        return upper, lower
    overlap = detect_table_boundary_overlap(
        upper.table, lower.table, max_gap=_NO_NARRATIVE_MAX_GAP_PT,
    )
    if overlap is None:
        return upper, lower

    t1, t2 = upper.table, lower.table
    if overlap.kind == "label_suffix":
        t1, t2 = _merge_label_suffix_from_split_tail(t1, t2)
    elif overlap.kind == "duplicate_row":
        t1, t2 = _repair_duplicate_boundary_row(t1, t2)
    elif overlap.kind == "y_overlap":
        t1, t2 = _merge_label_suffix_from_split_tail(t1, t2)
        if t2 is lower.table:
            t1, t2 = _repair_duplicate_boundary_row(t1, t2)

    if t1 is upper.table and t2 is lower.table:
        return upper, lower

    new_upper = DocumentEntry(
        kind="table",
        page=upper.page,
        y0=t1.y0,
        y1=max(t1.y1, upper.y1),
        table=t1,
        entry_id=upper.entry_id,
    )
    if not dense_rows(t2):
        return new_upper, None
    new_lower = DocumentEntry(
        kind="table",
        page=lower.page,
        y0=t2.y0,
        y1=t2.y1,
        table=t2,
        entry_id=lower.entry_id,
    )
    refresh_scope_source_items(t1)
    refresh_scope_source_items(t2)
    return new_upper, new_lower


def _row_fingerprint(row: List[str]) -> Tuple[str, ...]:
    return tuple(str(c or "").strip() for c in row)


def _table_rows_are_prefix(short_rows: List[List[str]], long_rows: List[List[str]]) -> bool:
    """短表行是否为长表行前缀（去空白后逐行相等）。"""
    if not short_rows or len(short_rows) > len(long_rows):
        return False
    for a, b in zip(short_rows, long_rows):
        if _row_fingerprint(a) != _row_fingerprint(b):
            return False
    return True


def _y_range_nested_or_heavy_overlap(
    a: StructuredTable,
    b: StructuredTable,
    *,
    min_overlap_ratio: float = 0.85,
) -> bool:
    """短表 Y 范围被长表覆盖，或两者高度重叠。"""
    lo = max(float(a.y0), float(b.y0))
    hi = min(float(a.y1), float(b.y1))
    overlap = max(0.0, hi - lo)
    if overlap <= 0:
        return False
    ha = max(1.0, float(a.y1) - float(a.y0))
    hb = max(1.0, float(b.y1) - float(b.y0))
    # 嵌套：短边几乎完全落在长边内
    if overlap / min(ha, hb) >= min_overlap_ratio:
        return True
    return False


def dedupe_subset_overlapping_tables(
    entries: List[DocumentEntry],
) -> List[DocumentEntry]:
    """同页去重：短表内容是长表前缀且 Y 重叠 → 丢弃短表（避免同一段进两张表）。

    典型：结构分裂/补挂后留下「仅表头+首行」碎片，与完整 2023 年表表并存。
    """
    if not entries:
        return entries

    tables = [
        (i, e)
        for i, e in enumerate(entries)
        if e.kind == "table" and e.table is not None
    ]
    drop: set[int] = set()
    for ai, (i, ea) in enumerate(tables):
        if i in drop:
            continue
        ta = ea.table
        assert ta is not None
        rows_a = dense_rows(ta)
        if not rows_a:
            continue
        for j, eb in tables[ai + 1 :]:
            if j in drop:
                continue
            tb = eb.table
            assert tb is not None
            if int(ea.page) != int(eb.page):
                continue
            if not tables_x_overlap(ta, tb):
                continue
            if not (
                tables_y_overlap(ta, tb)
                or _y_range_nested_or_heavy_overlap(ta, tb)
            ):
                continue
            rows_b = dense_rows(tb)
            if not rows_b:
                continue
            # 内容前缀子集 → 丢短的
            if _table_rows_are_prefix(rows_a, rows_b) and len(rows_a) < len(rows_b):
                drop.add(i)
                break
            if _table_rows_are_prefix(rows_b, rows_a) and len(rows_b) < len(rows_a):
                drop.add(j)
                continue
            # 同源 items：短表 scope 被长表完全覆盖
            sa = set(str(x) for x in (ta.metadata.get("scope_source_items") or []) if x)
            sb = set(str(x) for x in (tb.metadata.get("scope_source_items") or []) if x)
            if sa and sb:
                if sa < sb and _y_range_nested_or_heavy_overlap(ta, tb):
                    drop.add(i)
                    break
                if sb < sa and _y_range_nested_or_heavy_overlap(tb, ta):
                    drop.add(j)

    if not drop:
        return entries
    return [e for i, e in enumerate(entries) if i not in drop]


def apply_adjacent_table_boundary_repair(
    entries: List[DocumentEntry],
) -> List[DocumentEntry]:
    """同页相邻表且中间无叙述文本：检测边界重叠并去重/折行合并。"""
    if not entries:
        return entries

    out = list(entries)
    removed_idx: set[int] = set()

    for upper, lower in iter_overlap_candidate_table_pairs(out):
        idx_upper = idx_lower = None
        for i, e in enumerate(out):
            if i in removed_idx:
                continue
            if e is upper:
                idx_upper = i
            if e is lower:
                idx_lower = i
        if idx_upper is None or idx_lower is None:
            continue
        if idx_upper in removed_idx or idx_lower in removed_idx:
            continue

        # 嵌套/子集重叠：直接丢掉短表，避免同一段内容进两张表
        tu, tl = out[idx_upper].table, out[idx_lower].table
        if tu is not None and tl is not None:
            ru, rl = dense_rows(tu), dense_rows(tl)
            if (
                tables_y_overlap(tu, tl) or _y_range_nested_or_heavy_overlap(tu, tl)
            ) and tables_x_overlap(tu, tl):
                if _table_rows_are_prefix(ru, rl) and len(ru) < len(rl):
                    removed_idx.add(idx_upper)
                    continue
                if _table_rows_are_prefix(rl, ru) and len(rl) < len(ru):
                    removed_idx.add(idx_lower)
                    continue

        new_u, new_l = _repair_boundary_overlap_pair(out[idx_upper], out[idx_lower])
        if new_u is not None:
            out[idx_upper] = new_u
        if new_l is None:
            removed_idx.add(idx_lower)
        elif new_l is not out[idx_lower]:
            out[idx_lower] = new_l

    return [e for i, e in enumerate(out) if i not in removed_idx]


def apply_sibling_label_suffix_merge(
    entries: List[DocumentEntry],
) -> List[DocumentEntry]:
    """兼容别名 → apply_adjacent_table_boundary_repair。"""
    return apply_adjacent_table_boundary_repair(entries)


def split_table_by_structure(
    table: StructuredTable,
    item_lookup: Optional[dict] = None,
) -> List[StructuredTable]:
    parts: List[StructuredTable] = [table]
    changed = True
    while changed:
        changed = False
        next_parts: List[StructuredTable] = []
        for part in parts:
            rows = dense_rows(part)
            region_merged = bool(part.metadata.get("region_continuation_merged"))
            split_at = find_structure_break_row(
                rows, region_continuation_merged=region_merged
            )
            if split_at < 0 or split_at < 2:
                next_parts.append(part)
                continue
            if (
                split_at == len(rows) - 1
                and row_is_table_tail_section_caption_row(rows[split_at])
            ):
                trimmed = slice_structured_table(part, 0, split_at)
                trimmed.metadata = copy.copy(part.metadata)
                next_parts.append(trimmed)
                continue
            if split_at >= len(rows) - 1:
                next_parts.append(part)
                continue
            t1 = slice_structured_table(part, 0, split_at)
            t2 = slice_structured_table(part, split_at)
            t1, t2 = _merge_label_suffix_from_split_tail(t1, t2)
            t2, peeled = _peel_leading_narrative_rows(t2)
            t2_before = t2
            t2 = repair_compound_header_items_from_template(t2, t1)
            t2 = apply_table_transform_guard(t2_before, t2, require_source_ids=True)
            suffix = len(next_parts)
            parent_scope = list(part.metadata.get("scope_source_items") or [])
            t1.metadata = copy.copy(part.metadata)
            t2.metadata = copy.copy(part.metadata)
            t1.metadata["split_suffix"] = f"A{suffix}"
            t2.metadata["split_suffix"] = f"B{suffix}"
            assign_split_scope_source_items(t1, parent_scope, item_lookup)
            assign_split_scope_source_items(t2, parent_scope, item_lookup)
            if peeled is not None:
                t2.metadata["_split_leading_narrative"] = peeled
            next_parts.extend([t1, t2])
            changed = True
        parts = next_parts
    guarded: List[StructuredTable] = []
    for p in parts:
        if not p.metadata.get("split_suffix"):
            expand_scope_source_items(p)
            guarded.append(p)
            continue
        reinferred = reinfer_table_column_grid(p)
        reinferred = apply_table_transform_guard(p, reinferred, require_source_ids=False)
        reinferred = repair_wrapped_label_suffix_rows(reinferred)
        refresh_scope_source_items(reinferred)
        parent_scope = list(p.metadata.get("scope_source_items") or [])
        if item_lookup and parent_scope:
            assign_split_scope_source_items(reinferred, parent_scope, item_lookup)
        else:
            expand_scope_source_items(reinferred)
        guarded.append(reinferred)
    return guarded


def apply_sibling_compound_header_repair(entries: List[DocumentEntry]) -> List[DocumentEntry]:
    """同页前一张子表作模板，修复守恒补挂后再次被 OCR 合并项覆盖的列表头。"""
    prev_table_by_page: dict[int, StructuredTable] = {}
    for entry in entries:
        if entry.kind != "table" or entry.table is None:
            continue
        page_num = int(entry.table.page)
        prev = prev_table_by_page.get(page_num)
        if prev is not None:
            prev_n = len(prev.grid.ranges) if prev.grid else 0
            cur_n = len(entry.table.grid.ranges) if entry.table.grid else 0
            if prev_n and cur_n and cur_n != prev_n:
                prev_table_by_page[page_num] = entry.table
                continue
            before = entry.table
            fixed = repair_compound_header_items_from_template(entry.table, prev)
            fixed = apply_table_transform_guard(before, fixed, require_source_ids=True)
            if fixed is not before:
                entry.table = fixed
                expand_scope_source_items(entry.table)
        prev_table_by_page[page_num] = entry.table
    return entries


def apply_structure_split(
    entries: List[DocumentEntry],
    page: Optional[PageSource] = None,
) -> List[DocumentEntry]:
    """结构分裂 + 表文剥离（序号行后脚注、表顶说明）。"""
    item_lookup = None
    if page is not None:
        item_lookup = {it.item_index: it for it in page.items}
    out: List[DocumentEntry] = []
    eid = 0
    for entry in entries:
        if entry.kind != "table" or entry.table is None:
            out.append(entry)
            eid = max(eid, entry.entry_id + 1)
            continue
        trimmed, intro = _peel_leading_narrative_rows(entry.table)
        if intro is not None:
            out.append(_text_block_to_entry(intro, eid))
            eid += 1
        repaired = repair_wrapped_label_suffix_rows(trimmed)
        refresh_scope_source_items(repaired)
        parts = split_table_by_structure(repaired, item_lookup=item_lookup)
        for part in parts:
            peeled = part.metadata.pop("_split_leading_narrative", None)
            if peeled is not None:
                out.append(_text_block_to_entry(peeled, eid))
                eid += 1
            for sub in split_structured_table(part):
                sub.entry_id = eid
                out.append(sub)
                eid += 1
    return out
