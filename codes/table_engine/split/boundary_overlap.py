# -*- coding: utf-8
"""相邻表边界重叠检测：中间无叙述文本时，检查行/几何重叠并指导合并。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.models import DocumentEntry, PageSource, RegionBox, SourceItem, StructuredTable
from codes.table_engine.split.row_classify import (
    is_likely_next_table_header_row,
    row_has_body_value_data,
    row_has_value_data,
    row_is_intra_table_label_row,
)

_LABEL_SUFFIX_WORDS = frozenset({"净额", "润", "合计", "小计"})
_NOTE_PREFIX_RE = re.compile(r"^注[：:]")
_MAX_WRAP_TAIL_CN = 28
_MAX_BOUNDARY_GAP_PT = 120.0
_NO_NARRATIVE_MAX_GAP_PT = 180.0
_NARRATIVE_MIN_CN = 12
_REGION_EDGE_SCAN_PT = 22.0


def _normalize_value_cell(text: str) -> str:
    return str(text or "").strip().replace(",", "").replace(" ", "")


def row_value_fingerprint(row: List[str]) -> Tuple[str, ...]:
    vals: List[str] = []
    for c in row[1:]:
        t = _normalize_value_cell(c)
        if not t:
            continue
        if is_numeric_data_cell(t) or t.endswith("%") or re.match(r"^-?\d", t):
            vals.append(t)
    return tuple(vals)


def rows_share_duplicate_values(row_a: List[str], row_b: List[str]) -> bool:
    fa = row_value_fingerprint(row_a)
    fb = row_value_fingerprint(row_b)
    return len(fa) >= 2 and fa == fb


def _row_label_key(row: List[str]) -> str:
    return str(row[0] or "").strip().replace(" ", "")


def row_content_fingerprint(row: List[str]) -> Tuple[str, Tuple[str, ...]]:
    """标签 + 值列指纹，用于跨行/跨表去重。"""
    return (_row_label_key(row), row_value_fingerprint(row))


def rows_are_content_duplicates(row_a: List[str], row_b: List[str]) -> bool:
    """两行标签与数值列完全一致（含双空行）。"""
    if should_merge_label_suffix_pair(row_a, row_b):
        return False
    la, lb = _row_label_key(row_a), _row_label_key(row_b)
    if not la and not lb:
        return row_value_fingerprint(row_a) == row_value_fingerprint(row_b)
    if la != lb:
        return False
    va, vb = row_value_fingerprint(row_a), row_value_fingerprint(row_b)
    if va == vb:
        return True
    return rows_share_duplicate_values(row_a, row_b)


def count_trailing_duplicate_suffix_block(rows: List[List[str]]) -> int:
    """表末整块重复前一块 → 返回应删掉的末块行数。"""
    n = len(rows)
    for block in range(1, n // 2 + 1):
        suffix = rows[n - block :]
        prev = rows[n - 2 * block : n - block]
        if all(rows_are_content_duplicates(suffix[i], prev[i]) for i in range(block)):
            return block
    return 0


def _row_opens_new_table_header(row: List[str]) -> bool:
    """下一张表的单位/列头带：不得当作上表重复行删掉。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells:
        return False
    try:
        from codes.table_engine.scope.header_scope import (
            is_annual_report_column_header_row,
            is_annual_report_unit_row,
            is_rmb_unit_lead_row,
        )
    except ImportError:
        return False
    if is_rmb_unit_lead_row(cells) or is_annual_report_unit_row(cells):
        return True
    if is_likely_next_table_header_row(row):
        return True
    if is_annual_report_column_header_row(cells):
        return True
    if str(row[0] or "").strip() == "项目":
        return True
    return False


def count_lower_leading_duplicate_rows(
    rows_upper: List[List[str]],
    rows_lower: List[List[str]],
    *,
    tail_scan: int = 16,
    max_trim: int = 12,
) -> int:
    """下表首部连续行若在上表尾部已有同内容 → 可删行数（折行尾片/新表头除外）。"""
    if not rows_upper or not rows_lower:
        return 0
    tail = rows_upper[max(0, len(rows_upper) - tail_scan) :]
    trim = 0
    while trim < len(rows_lower) and trim < max_trim:
        row_l = rows_lower[trim]
        if _row_opens_new_table_header(row_l):
            break
        if trim == 0 and should_merge_label_suffix_pair(rows_upper[-1], row_l):
            break
        if not row_has_body_value_data(row_l) and not _row_label_key(row_l):
            break
        if not any(rows_are_content_duplicates(row_u, row_l) for row_u in tail):
            break
        trim += 1
    return trim


def row_is_label_suffix_tail(row: List[str]) -> bool:
    cells = [str(c).strip() for c in row if str(c).strip()]
    return bool(cells) and cells[0] in _LABEL_SUFFIX_WORDS


_WRAP_FRAGMENT_PREFIXES = ("的", "量")


def row_is_wrapped_label_continuation_tail(row: List[str]) -> bool:
    """折行标签尾片：仅左列短片段、无值列（产、的净资产、量净额（元/股）等）。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells:
        return False
    if is_likely_next_table_header_row(row):
        return False
    if row_has_value_data(row):
        return False
    first = cells[0]
    if _NOTE_PREFIX_RE.match(first):
        return False
    if first in ("增减", "期增减", "末增减", "比上年同期"):
        return False
    if first.startswith("其中"):
        return False
    if not str(row[0] or "").strip():
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", first))
    if cn == 0 or cn > _MAX_WRAP_TAIL_CN:
        return False
    if row_is_intra_table_label_row(row):
        return False
    if first in _LABEL_SUFFIX_WORDS:
        return True
    if first.startswith(_WRAP_FRAGMENT_PREFIXES):
        return True
    if cn <= 4 and len(first) <= 12:
        return True
    return False


def row_is_wrapped_label_head_row(row: List[str]) -> bool:
    """折行标签首片：仅左列、无值列，文本未完（以…计、计入…等），应并入下一行。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells or row_has_value_data(row):
        return False
    if len(cells) != 1 or not str(row[0] or "").strip():
        return False
    first = cells[0]
    if _NOTE_PREFIX_RE.match(first) or first.startswith("其中"):
        return False
    if is_likely_next_table_header_row(row):
        return False
    if first.startswith(("以", "入", "和", "及", "其", "—", "－", "指")):
        return True
    if first.endswith(("计", "入", "的", "及", "收益", "损益", "综合")):
        return True
    return False


def should_merge_wrapped_label_head_into_next(prev: List[str], cur: List[str]) -> bool:
    """上行仅标签首片、下行有续文或数值 → 首片并入下行标签列。"""
    if not row_is_wrapped_label_head_row(prev):
        return False
    if is_likely_next_table_header_row(cur):
        return False
    cur_label = str(cur[0] or "").strip()
    if not cur_label and not row_has_value_data(cur):
        return False
    return True


def row_is_change_reason_body_row(row: List[str]) -> bool:
    """变化原因表体行：两列金额 + 独立百分比列。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if len(cells) < 4:
        return False
    label = str(row[0] or "").strip()
    if not label or not re.search(r"[\u4e00-\u9fff]", label):
        return False
    amounts = sum(
        1
        for c in cells[1:]
        if is_numeric_data_cell(c) and not str(c).strip().endswith("%")
    )
    pcts = sum(
        1
        for c in cells[1:]
        if str(c).strip().endswith("%") and is_numeric_data_cell(str(c).strip())
    )
    if not pcts:
        from codes.table_engine.geometry.numeric import split_percent_trailing_text
        pcts = sum(
            1 for c in cells[1:] if split_percent_trailing_text(str(c).strip())
        )
    return amounts >= 2 and pcts >= 1


def row_is_reason_column_wrap_fragment(row: List[str]) -> bool:
    """主要原因列折行尾片（款、少等），非项目列标签折行。"""
    cells = [str(c).strip() for c in row if str(c).strip()]
    if not cells or row_has_value_data(row):
        return False
    if row_is_intra_table_label_row(row):
        return False
    if len(cells) != 1:
        return False
    if str(row[0] or "").strip():
        return False
    first = cells[0]
    cn = len(re.findall(r"[\u4e00-\u9fff]", first))
    if cn == 0 or cn > 6:
        return False
    if first in _LABEL_SUFFIX_WORDS:
        return False
    if first.startswith(_WRAP_FRAGMENT_PREFIXES):
        return False
    return cn <= 2 and len(first) <= 4


def should_merge_reason_column_wrap_pair(prev: List[str], cur: List[str]) -> bool:
    if not row_is_change_reason_body_row(prev):
        return False
    return row_is_reason_column_wrap_fragment(cur)


def address_wrap_column_index(row: List[str]) -> Optional[int]:
    """折行尾片唯一非空列索引（机构地址等宽文本列）。"""
    nonempty = [(i, str(c).strip()) for i, c in enumerate(row) if str(c).strip()]
    if len(nonempty) != 1:
        return None
    return nonempty[0][0]


def row_is_address_column_wrap_fragment(row: List[str]) -> bool:
    """宽文本列折行尾片：仅中间描述列有字，无序号/名称/数值。"""
    from codes.table_engine.split.row_classify import text_looks_like_wrapped_address

    nonempty = [(i, str(c).strip()) for i, c in enumerate(row) if str(c).strip()]
    if len(nonempty) != 1:
        return False
    ci, text = nonempty[0]
    if ci == 0:
        return False
    if row_has_value_data(row):
        return False
    if not text_looks_like_wrapped_address(text):
        if is_likely_next_table_header_row(row):
            return False
    if row_is_reason_column_wrap_fragment(row):
        return False
    if row_is_wrapped_label_continuation_tail(row):
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cn >= 2


def should_merge_address_column_wrap_pair(prev: List[str], cur: List[str]) -> bool:
    """上行表体 + 下行仅地址列续文 → 并入上行同列。"""
    ci = address_wrap_column_index(cur)
    if ci is None or ci <= 0:
        return False
    if not row_is_address_column_wrap_fragment(cur):
        return False
    if not str(prev[ci] or "").strip():
        return False
    if not (
        row_has_value_data(prev)
        or str(prev[0] or "").strip()
        or str(prev[1] or "").strip()
    ):
        return False
    return True


def should_merge_label_suffix_pair(prev: List[str], cur: List[str]) -> bool:
    return should_merge_wrapped_label_rows(prev, cur)


def should_merge_wrapped_label_rows(prev: List[str], cur: List[str]) -> bool:
    if should_merge_reason_column_wrap_pair(prev, cur):
        return False
    if row_is_intra_table_label_row(cur) and not row_is_wrapped_label_continuation_tail(cur):
        return False
    if not row_is_label_suffix_tail(cur) and not row_is_wrapped_label_continuation_tail(cur):
        return False
    prev_label = str(prev[0] or "").strip()
    if not prev_label:
        return False
    cur_label = str(cur[0] or "").strip()
    tail_has = row_has_value_data(cur)
    head_has = row_has_value_data(prev)
    # 「合计/小计」带完整数值 = 独立汇总行（如 债券 + 合计 数值相同），禁止当折行尾片合并
    if (
        cur_label in ("合计", "小计")
        and tail_has
        and head_has
        and row_has_body_value_data(cur)
        and row_has_body_value_data(prev)
    ):
        return False
    if tail_has and head_has:
        if row_is_label_suffix_tail(cur):
            fa, fb = row_value_fingerprint(prev), row_value_fingerprint(cur)
            if fa and fb and (fa == fb or set(fb).issubset(set(fa))):
                return True
        return rows_share_duplicate_values(prev, cur)
    if tail_has and not head_has:
        if row_is_label_suffix_tail(cur):
            return True
        return row_has_body_value_data(prev)
    if row_is_label_suffix_tail(cur):
        return True
    return row_has_body_value_data(prev)


def tables_x_overlap(
    upper: StructuredTable,
    lower: StructuredTable,
    *,
    margin: float = 12.0,
) -> bool:
    return not (
        upper.x1 < lower.x0 - margin
        or lower.x1 < upper.x0 - margin
    )


def tables_y_overlap(upper: StructuredTable, lower: StructuredTable) -> bool:
    return upper.y1 > lower.y0 + 3.0


@dataclass(frozen=True)
class BoundaryOverlap:
    kind: str  # label_suffix | duplicate_row | y_overlap


def detect_table_boundary_overlap(
    upper: StructuredTable,
    lower: StructuredTable,
    *,
    max_gap: float = _MAX_BOUNDARY_GAP_PT,
) -> Optional[BoundaryOverlap]:
    """两表边界是否行级/几何重叠（调用方须已确认中间无叙述文本）。"""
    rows_u = upper.iter_rows_dense()
    rows_l = lower.iter_rows_dense()
    if not rows_u or not rows_l:
        return None

    if tables_y_overlap(upper, lower):
        return BoundaryOverlap("y_overlap")

    gap = lower.y0 - upper.y1
    if gap > max_gap:
        if not (
            should_merge_label_suffix_pair(rows_u[-1], rows_l[0])
            or count_lower_leading_duplicate_rows(rows_u, rows_l) > 0
            or rows_are_content_duplicates(rows_u[-1], rows_l[0])
            or (
                row_has_body_value_data(rows_u[-1])
                and row_has_body_value_data(rows_l[0])
                and rows_share_duplicate_values(rows_u[-1], rows_l[0])
            )
        ):
            return None
    if not tables_x_overlap(upper, lower):
        return None

    last_u, first_l = rows_u[-1], rows_l[0]
    if should_merge_label_suffix_pair(last_u, first_l):
        return BoundaryOverlap("label_suffix")
    if count_lower_leading_duplicate_rows(rows_u, rows_l) > 0:
        return BoundaryOverlap("duplicate_row")
    if rows_are_content_duplicates(last_u, first_l):
        return BoundaryOverlap("duplicate_row")
    if (
        row_has_body_value_data(last_u)
        and row_has_body_value_data(first_l)
        and rows_share_duplicate_values(last_u, first_l)
    ):
        return BoundaryOverlap("duplicate_row")
    return None


def _cluster_row_texts(items: Sequence[SourceItem]) -> List[List[str]]:
    if not items:
        return []
    dicts = source_items_to_dicts(list(items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    out: List[List[str]] = []
    for row in rows:
        row_items = sorted(
            [
                index_map[d["item_index"]]
                for d in row.get("items", [])
                if d.get("item_index") in index_map
            ],
            key=lambda it: it.x0,
        )
        cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
        if cells:
            out.append(cells)
    return out


def _collect_region_edge_items(
    page: PageSource,
    region: RegionBox,
    *,
    edge: str,
) -> List[SourceItem]:
    x_lo = region.x0 - 12.0
    x_hi = region.x1 + 12.0
    if edge == "bottom":
        y_lo = region.y1 - _REGION_EDGE_SCAN_PT
        y_hi = region.y1 + 4.0
    else:
        y_lo = region.y0 - 4.0
        y_hi = region.y0 + _REGION_EDGE_SCAN_PT
    return [
        it
        for it in page.items
        if y_lo <= it.bbox.y0 <= y_hi
        and it.bbox.x1 >= x_lo
        and it.bbox.x0 <= x_hi
        and str(it.text or "").strip()
    ]


def region_pair_has_boundary_overlap(
    page: PageSource,
    upper: RegionBox,
    lower: RegionBox,
    gap_items: Sequence[SourceItem],
) -> bool:
    """scope 规划：两 region 间隙无叙述时，边界是否折行尾片/重复数据。"""
    bottom = _cluster_row_texts(_collect_region_edge_items(page, upper, edge="bottom"))
    top = _cluster_row_texts(_collect_region_edge_items(page, lower, edge="top"))
    gap_rows: List[List[str]] = []
    if gap_items:
        gap_rows = _cluster_row_texts(list(gap_items))
        if gap_rows and bottom and should_merge_label_suffix_pair(bottom[-1], gap_rows[0]):
            return True
        if gap_rows:
            top = gap_rows + top
    if not bottom or not top:
        return False

    last_u, first_l = bottom[-1], top[0]
    if should_merge_label_suffix_pair(last_u, first_l):
        return True
    if (
        row_has_body_value_data(last_u)
        and row_has_body_value_data(first_l)
        and rows_share_duplicate_values(last_u, first_l)
    ):
        return True
    return False


def gap_has_narrative_text(gap_items: Sequence[SourceItem]) -> bool:
    if not gap_items:
        return False
    lines: List[str] = []
    for row in _cluster_row_texts(gap_items):
        lines.append("".join(row))
    text = "\n".join(lines)
    if not text.strip():
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cn >= _NARRATIVE_MIN_CN and ("。" in text or "；" in text):
        return True
    if any(m in text for m in ("如下", "包括", "分别为", "下表")):
        return True
    return False


def has_narrative_text_between_entries(
    entries: Sequence[DocumentEntry],
    upper: DocumentEntry,
    lower: DocumentEntry,
) -> bool:
    """两表 y 间隙内是否存在叙述性 TEXT entry（须主要落在间隙内）。"""
    gap_y0 = upper.y1 - 3.0
    gap_y1 = lower.y0 + 3.0
    if gap_y1 <= gap_y0:
        gap_y0 = upper.y1 - 1.0
        gap_y1 = lower.y0 + 1.0
    for entry in entries:
        if entry.kind != "text" or entry.page != upper.page:
            continue
        if entry.text_block is None:
            continue
        text = entry.text_block.text.strip()
        if not text:
            continue
        if entry.y0 >= gap_y1 or entry.y1 <= gap_y0:
            continue
        mid_y = (entry.y0 + entry.y1) / 2.0
        if mid_y < gap_y0 or mid_y > gap_y1:
            if entry.y0 < upper.y1 - 5.0:
                continue
        cn = len(re.findall(r"[\u4e00-\u9fff]", text))
        if cn >= _NARRATIVE_MIN_CN and ("。" in text or "；" in text):
            return True
        if any(m in text for m in ("如下", "包括", "分别为")):
            return True
    return False


def iter_overlap_candidate_table_pairs(
    entries: List[DocumentEntry],
) -> List[Tuple[DocumentEntry, DocumentEntry]]:
    """同页表对：中间无叙述 TEXT 时，向上扫描所有前驱表（可跨过中间小表 region）。"""
    tables = [
        e for e in entries
        if e.kind == "table" and e.table is not None and e.page is not None
    ]
    tables.sort(key=lambda e: (e.page, e.y0, e.y1))
    pairs: List[Tuple[DocumentEntry, DocumentEntry]] = []
    for j in range(1, len(tables)):
        lower = tables[j]
        for i in range(j - 1, -1, -1):
            upper = tables[i]
            if upper.page != lower.page:
                break
            if has_narrative_text_between_entries(entries, upper, lower):
                break
            pairs.append((upper, lower))
    return pairs


def iter_adjacent_table_pairs_without_text(
    entries: List[DocumentEntry],
) -> List[Tuple[DocumentEntry, DocumentEntry]]:
    """同页按 y 相邻、中间无叙述文本的表对。"""
    tables = [
        e for e in entries
        if e.kind == "table" and e.table is not None and e.page is not None
    ]
    tables.sort(key=lambda e: (e.page, e.y0, e.y1))
    pairs: List[Tuple[DocumentEntry, DocumentEntry]] = []
    for i in range(len(tables) - 1):
        upper, lower = tables[i], tables[i + 1]
        if upper.page != lower.page:
            continue
        if has_narrative_text_between_entries(entries, upper, lower):
            continue
        pairs.append((upper, lower))
    return pairs
