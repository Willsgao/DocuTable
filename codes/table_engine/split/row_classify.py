# -*- coding: utf-8
"""表行分类：值列类型指纹与表体一致性。"""

from __future__ import annotations

import re
from typing import List, Literal, Optional

from codes.table_engine.geometry.numeric import (
    is_month_day_cell,
    is_merged_numeric_cell,
    is_numeric_data_cell,
    is_report_date_cell,
    is_year_cell,
    contains_numeric_data,
)

DASH_VALUES = frozenset(("-", "－", "—", "–"))
ValueCellKind = Literal["empty", "dash", "numeric", "date", "text"]

_FOOTNOTE_MARKER_RE = re.compile(r"^[（(]?\d+[)）\.．、]")
_NOTE_PREFIX_RE = re.compile(r"^注[：:]")
_INTRA_SECTION_LABELS = frozenset({"资产", "负债", "转移:", "转移："})
_ENTITY_SCOPE_LABELS = frozenset({"本集团", "本行"})
_ROW_SERIAL_PREFIX_RE = re.compile(r"^\d+[a-z]?(?:\s+\d+[a-z]?)*$", re.I)
_ADDRESS_WRAP_MARKERS = (
    "路", "街", "号", "层", "幢", "附", "座", "广场", "大道", "高新区", "开发区",
    "酒店", "建筑", "附属", "花园", "小区", "大厦", "号楼",
)


def text_looks_like_wrapped_address(text: str) -> bool:
    """机构地址等宽文本列折行续文（非列标/表头）。"""
    t = str(text or "").strip()
    if len(t) < 6:
        return False
    marker_hits = sum(1 for m in _ADDRESS_WRAP_MARKERS if m in t)
    if marker_hits >= 2:
        return True
    if marker_hits >= 1 and re.search(r"\d", t):
        return True
    return False


def _pad_row(row: List[str], width: int) -> List[str]:
    cells = [str(c).strip() for c in row]
    if len(cells) < width:
        cells.extend([""] * (width - len(cells)))
    return cells[:width]


def classify_value_cell(text: str) -> ValueCellKind:
    t = str(text or "").strip()
    if not t:
        return "empty"
    if t in DASH_VALUES:
        return "dash"
    if is_report_date_cell(t) or is_year_cell(t) or is_month_day_cell(t):
        return "date"
    if is_merged_numeric_cell(t):
        return "text"
    if is_numeric_data_cell(t):
        return "numeric"
    return "text"


def row_value_kinds(row: List[str], *, value_start: int = 1) -> List[ValueCellKind]:
    vals = row[value_start:] if len(row) > value_start else []
    return [classify_value_cell(c) for c in vals]


def row_values_all_empty(row: List[str], *, value_start: int = 1) -> bool:
    vals = row[value_start:] if len(row) > value_start else []
    if not vals:
        return True
    return all(not str(c).strip() for c in vals)


def cell_has_body_value_data(text: str) -> bool:
    """单格是否含表体数值或短横（纯报告期日期/年份不算表体）。"""
    t = str(text or "").strip()
    if not t:
        return False
    if t in DASH_VALUES:
        return True
    if re.fullmatch(r"0+", t):
        return True
    if is_report_date_cell(t) or is_month_day_cell(t) or is_year_cell(t):
        return False
    if is_numeric_data_cell(t) or contains_numeric_data(t):
        return True
    return False


def row_has_body_value_data(row: List[str], *, value_start: int = 1) -> bool:
    """值列含数值或短横 → 与表体结构一致的数据行（不含日期/文本）。"""
    vals = row[value_start:] if len(row) > value_start else []
    return any(cell_has_body_value_data(c) for c in vals if str(c).strip())


def row_has_date_in_values(row: List[str], *, value_start: int = 1) -> bool:
    kinds = row_value_kinds(row, value_start=value_start)
    return "date" in kinds


def row_has_reporting_period_date_in_values(
    row: List[str],
    *,
    value_start: int = 1,
) -> bool:
    """值列报告期日期（年/完整日期/含月日字样）；不含裸 1–2 位金额误判。"""
    vals = row[value_start:] if len(row) > value_start else []
    for c in vals:
        t = str(c or "").strip()
        if not t:
            continue
        if is_year_cell(t):
            return True
        if is_report_date_cell(t) and ("年" in t or "月" in t or "日" in t):
            return True
        if ("月" in t or "日" in t) and is_month_day_cell(t):
            return True
    return False


def row_is_small_amount_body_row(row: List[str], *, value_start: int = 1) -> bool:
    """表体：「－」缩进标签 + 值列为小额整数或短横（非报告期）。"""
    label = str(row[0] if row else "").strip()
    if not label.startswith(("－", "—", "-")):
        return False
    vals = [str(c).strip() for c in row[value_start:] if str(c).strip()]
    if not vals:
        return False
    return all(
        re.fullmatch(r"-?\d+", v) is not None or v in DASH_VALUES
        for v in vals
    )


def row_is_note_section_caption_row(row: List[str]) -> bool:
    """年报附注节标题（如 45 其他业务收入），值列无表体。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells:
        return False
    joined = " ".join(cells)
    if not re.match(r"^\d{1,2}\s+[\u4e00-\u9fff]", joined):
        return False
    if row_has_body_value_data(row):
        return False
    return row_values_all_empty(row, value_start=1) or len(cells) <= 2


_ANNUAL_SUBSECTION_CAPTION_RE = re.compile(
    r"^[（(][一二三四五六七八九十\d]+[)）]\s*[\u4e00-\u9fff]"
)


def row_is_annual_subsection_caption_row(row: List[str]) -> bool:
    """年报（四）（五）（六）类小节标题，值列无表体。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells:
        return False
    if not _ANNUAL_SUBSECTION_CAPTION_RE.match(cells[0]):
        return False
    if row_has_body_value_data(row):
        return False
    return True


_NUMBERED_SUBSECTION_CAPTION_RE = re.compile(r"^\d+[\.．、]\s*[\u4e00-\u9fff]")


def row_is_numbered_subsection_caption_row(row: List[str]) -> bool:
    """年报 1.手续费… / 2.投资收益… 小节标题，值列无表体。"""
    if row_has_body_value_data(row):
        return False
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells:
        return False
    joined = " ".join(cells)
    if not _NUMBERED_SUBSECTION_CAPTION_RE.match(joined):
        return False
    if any(
        is_numeric_data_cell(c) and i > 0
        for i, c in enumerate(cells)
    ):
        return False
    return True


def row_is_table_tail_section_caption_row(row: List[str]) -> bool:
    """表尾应剥离为 TEXT 的小节标题行。"""
    return (
        row_is_note_section_caption_row(row)
        or row_is_annual_subsection_caption_row(row)
        or row_is_numbered_subsection_caption_row(row)
    )


def row_has_text_in_values(row: List[str], *, value_start: int = 1) -> bool:
    kinds = row_value_kinds(row, value_start=value_start)
    return "text" in kinds


def _value_text_looks_like_column_header(text: str) -> bool:
    """值列短列标/表头文本（非粘连数值串）。"""
    t = str(text or "").strip()
    if not t or is_report_date_cell(t):
        return False
    if text_looks_like_wrapped_address(t):
        return False
    if re.search(r"\d{4,}", t.replace(",", "").replace("，", "")):
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    return 2 <= cn <= 12


def row_is_intra_table_label_row(row: List[str], *, value_start: int = 1) -> bool:
    """表内子节/缩进标签：仅标签列有字，值列无金额；非重复列标带（软件/本集团）。"""
    if row_has_body_value_data(row, value_start=value_start):
        return False
    if is_entity_scope_header_row(row, value_start=value_start):
        return False
    cells = [str(c).strip() for c in row]
    nonempty = [(i, c) for i, c in enumerate(cells) if c]
    if not nonempty:
        return False
    if len(nonempty) == 1 and text_looks_like_wrapped_address(nonempty[0][1]):
        return False
    if any(is_year_cell(c) or is_report_date_cell(c) for _, c in nonempty):
        return False
    header_like = [
        c for i, c in nonempty
        if i >= value_start and _value_text_looks_like_column_header(c)
    ]
    if len(header_like) >= 2:
        return False
    if len(header_like) == 1:
        cn = len(re.findall(r"[\u4e00-\u9fff]", header_like[0]))
        if 2 <= cn <= 24:
            return True
    if len(nonempty) == 1 and nonempty[0][0] <= value_start:
        cn = len(re.findall(r"[\u4e00-\u9fff]", nonempty[0][1]))
        return 2 <= cn <= 24
    return False


def row_has_header_text_in_values(row: List[str], *, value_start: int = 1) -> bool:
    vals = row[value_start:] if len(row) > value_start else []
    return any(_value_text_looks_like_column_header(c) for c in vals if str(c).strip())


def row_has_value_data(row: List[str], *, value_start: int = 1) -> bool:
    """向后兼容别名 → 表体一致的数据行。"""
    return row_has_body_value_data(row, value_start=value_start)


def find_last_body_value_row(rows: List[List[str]], *, value_start: int = 1) -> int:
    """最后一条值列为数值/短横的行。"""
    last = -1
    for i, row in enumerate(rows):
        if row_has_body_value_data(row, value_start=value_start):
            last = i
    return last


def find_last_value_data_row(rows: List[List[str]], *, value_start: int = 1) -> int:
    return find_last_body_value_row(rows, value_start=value_start)


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def is_entity_scope_header_row(row: List[str], *, value_start: int = 1) -> bool:
    """双列主体表头：本集团 / 本行（无表体数值/短横）。"""
    if row_has_body_value_data(row, value_start=value_start):
        return False
    labels = [_normalize_label(str(c)) for c in row if str(c).strip()]
    if not labels:
        return False
    return all(lb in _ENTITY_SCOPE_LABELS for lb in labels)


def _row_nonempty_cells(row: List[str]) -> List[str]:
    return [str(c).strip() for c in row if str(c).strip()]


def is_likely_next_table_header_row(row: List[str], *, value_start: int = 1) -> bool:
    """表底短行是否更像下一张表的表头带（主体/单位/日期/列标），非叙述或表内小节。"""
    if row_has_body_value_data(row, value_start=value_start):
        return False
    if is_inter_table_narrative_row(row, value_start=value_start):
        return False
    if is_intra_table_section_row(row, value_start=value_start):
        return False
    cells = _row_nonempty_cells(row)
    if not cells:
        return False
    if is_entity_scope_header_row(row, value_start=value_start):
        return True

    from codes.table_engine.scope.header_scope import (
        has_letter_column_header_row,
        is_annual_report_column_header_row,
        is_annual_report_unit_row,
        is_date_only_header_row,
        is_rmb_unit_lead_row,
        is_single_year_label_row,
    )

    if is_annual_report_column_header_row(cells):
        return True
    if row_has_date_in_values(row, value_start=value_start):
        return True
    if is_date_only_header_row(cells):
        return True
    if is_rmb_unit_lead_row(cells):
        return True
    if is_annual_report_unit_row(cells):
        return True
    if has_letter_column_header_row(cells):
        return True
    if row_has_header_text_in_values(row, value_start=value_start):
        return True
    if is_single_year_label_row(cells) and row_values_all_empty(row, value_start=value_start):
        return True
    if any(c == "项目" for c in cells) and row_values_all_empty(row, value_start=value_start):
        return True
    return False


def is_prependable_header_band_row(row: List[str], *, value_start: int = 1) -> bool:
    """可并入下一张表的表头带行。"""
    if is_inter_table_narrative_row(row, value_start=value_start):
        return False
    return is_likely_next_table_header_row(row, value_start=value_start)


def trailing_block_is_next_table_header(
    rows: List[List[str]],
    *,
    value_start: int = 1,
) -> bool:
    """表体后的连续非数据块是否整体为下一张表表头带（可含前导叙述行）。"""
    if not rows:
        return False
    if any(row_has_body_value_data(r, value_start=value_start) for r in rows):
        return False
    saw_header = False
    for row in rows:
        if is_inter_table_narrative_row(row, value_start=value_start):
            continue
        cells = _row_nonempty_cells(row)
        if not cells:
            continue
        if is_likely_next_table_header_row(row, value_start=value_start):
            saw_header = True
            continue
        return False
    return saw_header


def is_intra_table_section_row(row: List[str], *, value_start: int = 1) -> bool:
    """表内短子节标题：值列可全空（如 转移：、资产）。"""
    if not row_values_all_empty(row, value_start=value_start):
        return False
    label = str(row[0] or "").strip() if row else ""
    if not label:
        return False
    norm = _normalize_label(label)
    joined = "".join(str(c).strip() for c in row if str(c).strip())
    if row_is_table_intro_caption_row(row, value_start=value_start):
        return False
    if norm in _INTRA_SECTION_LABELS:
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", norm))
    if cn <= 6 and (norm.endswith("：") or norm.endswith(":")):
        return True
    return False


def row_is_table_intro_caption_row(row: List[str], *, value_start: int = 1) -> bool:
    """表前说明行（具体经营网点如下等），应剥为 TEXT 而非表内小节。"""
    if row_has_body_value_data(row, value_start=value_start):
        return False
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells:
        return False
    joined = "".join(cells)
    if any(m in joined for m in ("经营网点如下", "具体如下", "下表列示", "下表列出")):
        return True
    if "如下" in joined and len(re.findall(r"[\u4e00-\u9fff]", joined)) >= 8:
        return True
    if joined.endswith(("如下：", "如下:")) and len(joined) >= 10:
        return True
    return False


def is_inter_table_narrative_row(row: List[str], *, value_start: int = 1) -> bool:
    """表间叙述/节标题：值列全空且非表内子节。"""
    from codes.table_engine.scope.header_scope import row_is_annual_header_wrap_fragment_row

    cells = [str(c).strip() for c in row if str(c).strip()]
    if row_is_annual_header_wrap_fragment_row(cells):
        return False
    if row_is_table_intro_caption_row(row, value_start=value_start):
        return True
    if not row_values_all_empty(row, value_start=value_start):
        return False
    if is_intra_table_section_row(row, value_start=value_start):
        return False
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells:
        return False
    joined = "".join(cells)
    if "下表列出" in joined or "下表列" in joined:
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if joined.endswith(("。", "；")) and cn >= 8:
        return True
    if cn >= 14 and re.search(r"\d+\.\d+", joined) and any(
        m in joined for m in ("亿元", "万元", "%", "较上年", "同比", "百分点")
    ):
        return True
    if cn >= 6 and any(m in joined for m in ("分布情况", "划分的情况", "划分的发放", "损失准备")):
        return True
    return False


def row_is_intra_table_period_footer_row(
    row: List[str],
    *,
    value_start: int = 1,
) -> bool:
    """表内尾部「期间」行：标签列=期间，值列仅单列报告期、无数值（如 IRRBB1 一级资本段）。"""
    label = str(row[0] if row else "").strip()
    if label != "期间":
        return False
    if row_has_body_value_data(row, value_start=value_start):
        return False
    date_cols = [
        str(c).strip()
        for c in row[value_start:]
        if str(c).strip()
        and (is_report_date_cell(str(c).strip()) or is_year_cell(str(c).strip()))
    ]
    return len(date_cols) == 1


def row_has_serial_prefix(row: List[str]) -> bool:
    """序号列：1 / 4a / 19 20 等。"""
    if not row:
        return False
    first = str(row[0] or "").strip()
    return bool(first and _ROW_SERIAL_PREFIX_RE.match(first))


def serial_label_table_value_start(rows: List[List[str]], *, scan: int = 12) -> int:
    """序号|标签|数值 披露表 → 值列从第 3 列起（col0 序号、col1 标签）。"""
    from codes.table_engine.scope.header_scope import has_letter_column_header_row

    head = rows[: min(scan, len(rows))]
    for row in head:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if has_letter_column_header_row(cells):
            return 2
    if sum(1 for row in rows if row_has_serial_prefix(row)) >= 3:
        return 2
    return 1


def find_body_structure_break(
    rows: List[List[str]],
    *,
    min_body_rows: int = 3,
    value_start: Optional[int] = None,
) -> int:
    """表体进行中出现日期/文本值列，或表间叙述行 → 新表/表尾起点。"""
    vs = serial_label_table_value_start(rows) if value_start is None else value_start
    body_count = 0

    for i, row in enumerate(rows):
        if row_has_serial_prefix(row):
            if row_has_body_value_data(row, value_start=vs) or row_is_small_amount_body_row(
                row, value_start=vs
            ):
                body_count += 1
            continue
        if row_has_body_value_data(row, value_start=vs):
            body_count += 1
            continue
        if row_is_small_amount_body_row(row, value_start=vs):
            body_count += 1
            continue

        if body_count < min_body_rows:
            continue

        if row_is_intra_table_label_row(row, value_start=vs):
            continue

        if row_has_reporting_period_date_in_values(row, value_start=vs):
            if row_is_intra_table_period_footer_row(row, value_start=vs):
                continue
            return i
        if row_has_header_text_in_values(row, value_start=vs):
            return i
        if is_inter_table_narrative_row(row, value_start=vs):
            return i

    return -1


def _label_only_block_is_post_table_narrative(rows: List[List[str]], *, value_start: int = 1) -> bool:
    """标签列折行块：句末标点 / 脚注 / 长段叙述 → 表尾；短多行 → 表内节标题。"""
    if not rows:
        return False
    if any(row_has_body_value_data(r, value_start=value_start) for r in rows):
        return False
    lines: List[str] = []
    for row in rows:
        cells = _pad_row(row, len(row))
        parts = [str(c).strip() for c in cells if str(c).strip()]
        if parts:
            lines.append("".join(parts))
    if not lines:
        return False
    joined = "".join(lines)
    if _NOTE_PREFIX_RE.match(lines[0]) or _FOOTNOTE_MARKER_RE.match(lines[0]):
        return True
    if trailing_block_is_next_table_header(rows, value_start=value_start):
        return False
    if joined.rstrip().endswith(("。", "；")):
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    # 多行短折行 → 表内小节（如 以公允价值… / 的金融资产）
    if len(lines) >= 2 and all(
        len(re.findall(r"[\u4e00-\u9fff]", ln)) <= 24 for ln in lines
    ):
        return False
    if cn >= 28:
        return True
    return False


def is_tail_annotation_row(row: List[str], col_count: int) -> bool:
    """表尾注释行：值列无表体数据；脚注格式或仅标签列有内容。"""
    if col_count <= 0:
        return False
    cells = _pad_row(row, col_count)
    if row_has_body_value_data(cells):
        return False

    non_empty = [c for c in cells if c]
    if not non_empty:
        return True

    first = non_empty[0]
    if _NOTE_PREFIX_RE.match(first):
        return True
    if _FOOTNOTE_MARKER_RE.match(first):
        return True

    if is_likely_next_table_header_row(cells):
        return False

    # 值列全空、仅标签列：句末标点 → 表尾叙述；否则视为表内节标题
    if col_count > 1:
        value_part = cells[1:]
        if all(not str(c).strip() for c in value_part):
            label = str(cells[0] or "").strip()
            if not label:
                return True
            if label.rstrip().endswith(("。", "；")):
                return True
            return False
    return False


def find_trailing_non_body_start(
    rows: List[List[str]],
    *,
    value_start: int = 1,
) -> Optional[int]:
    """表体末行之后、连续非表体行（表头/叙述）的起点；无则 None。"""
    last_body = find_last_body_value_row(rows, value_start=value_start)
    if last_body < 0 or last_body >= len(rows) - 1:
        return None
    start = last_body + 1
    for i in range(start, len(rows)):
        if row_has_body_value_data(rows[i], value_start=value_start):
            return None
    return start
