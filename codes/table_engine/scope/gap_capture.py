# -*- coding: utf-8
"""页内 region 间隙：表头剥离 + 说明文字捕获。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set, Tuple

from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.models import PageSource, RegionBox, SourceItem, TextBlock, BBox
from codes.table_engine.split.row_classify import (
    is_likely_next_table_header_row,
    row_has_body_value_data,
)
from codes.table_engine.geometry.numeric import (
    contains_numeric_data,
    is_numeric_data_cell,
    is_report_date_cell,
    is_year_cell,
)
from codes.table_engine.scope.header_scope import (
    has_annual_column_header_band,
    has_letter_column_header_row,
    is_annual_report_column_header_row,
    is_annual_report_unit_row,
    is_date_only_header_row,
    is_rmb_unit_lead_row,
    is_wrapped_column_subheader_row,
    peel_pre_header_from_items,
    row_has_pillar_table_caption,
    row_has_reporting_date,
)
from codes.table_engine.scope.region_scope import (
    TableScope,
    _HEADERLESS_GAP_REGION_BASE,
    build_headerless_gap_scope,
    build_table_scope,
    merge_items_dedup,
)
from codes.table_engine.split.boundary_overlap import (
    _LABEL_SUFFIX_WORDS,
    gap_has_narrative_text,
    region_pair_has_boundary_overlap,
    row_is_wrapped_label_continuation_tail,
)
from codes.table_engine.scope.page_chrome import (
    ensure_page_chrome_separated,
    extract_page_chrome,
    filter_items_without_chrome,
    items_look_like_page_chrome,
)

Y_MARGIN_BELOW = 30.0
_HEADER_STAMP_MAX_HEIGHT = 50.0
_NARRATIVE_MIN_CN = 18
_PAGE_CHROME_MAX_Y = 65.0
_PAGE_CHROME_MARKERS = ("第三支柱", "信息披露报告", "第三支柱信息披露")
_CONTINUATION_MAX_GAP = 35.0
_SECTION_CONTINUATION_MAX_GAP = 100.0
_LABEL_SUFFIX_CONTINUATION_MAX_GAP = 180.0
_LARGE_INTER_REGION_GAP = 45.0
_POST_TABLE_GAP_Y0 = 8.0
_POST_TABLE_TAIL_CONTINUATION_Y = 48.0
_TRAILING_GAP_TABLE_BASE = 9100
_HEADERLESS_TAIL_SCAN_PT = 220.0
_WRAP_TAIL_BELOW_REGION_PT = 55.0
_LABEL_VALUE_MIN_X = 140.0
_FOOTNOTE_ROW_RE = re.compile(r"^注[：:]")
_NUMBERED_FOOTNOTE_ITEM_RE = re.compile(r"^[12][\.．、]\s*[\u4e00-\u9fff]")
_ANNUAL_REPORT_CHROME_MARKERS = ("年度报告", "成都银行股份有限公司")
_SECTION_BREAK_MARKERS = (
    "所需的稳定资金",
    "可用的稳定资金合计",
    "可用的稳定资金",
    "净稳定资金比例合格",
)


def _gap_is_intra_table_section(items: Sequence[SourceItem]) -> bool:
    """同一张披露表内的小节切换（如 LIQ2 可用/所需稳定资金）。"""
    if not items or _gap_has_table_caption(items):
        return False
    text = _items_to_text(items)
    if not text:
        return False
    return any(m in text for m in _SECTION_BREAK_MARKERS)


def _union_regions(a: RegionBox, b: RegionBox) -> RegionBox:
    return RegionBox(
        x0=min(a.x0, b.x0),
        y0=min(a.y0, b.y0),
        x1=max(a.x1, b.x1),
        y1=max(a.y1, b.y1),
    )


def _regions_overlap_x(a: RegionBox, b: RegionBox, margin: float = 10.0) -> bool:
    return not (a.x1 < b.x0 - margin or b.x1 < a.x0 - margin)


def _gap_has_new_table_boundary(items: Sequence[SourceItem]) -> bool:
    """间隙含新表边界：披露表题、年报（二）（三）小节、新单位+列标表头。"""
    if not items:
        return False
    dicts = source_items_to_dicts(list(items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    for row in rows:
        row_items = [
            index_map[d["item_index"]]
            for d in row.get("items", [])
            if d.get("item_index") in index_map
        ]
        if not row_items:
            continue
        cells = [
            str(d.get("text", "")).strip()
            for d in row.get("items", [])
            if str(d.get("text", "")).strip()
        ]
        if row_has_pillar_table_caption(cells):
            return True
        if _gap_row_is_subsection_heading(row_items):
            return True
    return False


def _gap_has_table_caption(items: Sequence[SourceItem]) -> bool:
    """兼容旧名：间隙是否构成新表边界（含年报小节标题）。"""
    return _gap_has_new_table_boundary(items)


def _gap_has_value_data_items(items: Sequence[SourceItem]) -> bool:
    """间隙带内出现表体数值 → 非纯节标题切换。"""
    if not items:
        return False
    dicts = source_items_to_dicts(list(items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    for row in rows:
        for it in row.get("items") or []:
            t = str(it.get("text", "")).strip()
            if contains_numeric_data(t) and float(it.get("x0", 0)) > 140:
                return True
    return False


def _region_top_starts_with_fresh_header(
    page: PageSource,
    region: RegionBox,
) -> bool:
    """下一块 region 顶部是否带完整新表表头（单位/日期+列标）。"""
    items = _collect_gap_items(
        page,
        region.y0,
        region.y0 + 55.0,
        x_lo=region.x0 - 10,
        x_hi=region.x1 + 10,
    )
    if not items:
        return False
    dicts = source_items_to_dicts(items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)[:8]
    if not rows:
        return False
    first_cells = [
        str(d.get("text", "")).strip()
        for d in rows[0].get("items") or []
        if str(d.get("text", "")).strip()
    ]
    if row_has_body_value_data(first_cells):
        return False
    has_unit = False
    has_period = False
    has_col = False
    for row in rows:
        cells = [
            str(d.get("text", "")).strip()
            for d in row.get("items") or []
            if str(d.get("text", "")).strip()
        ]
        if is_rmb_unit_lead_row(cells):
            has_unit = True
        if any(is_report_date_cell(c) or is_year_cell(c) for c in cells):
            has_period = True
        if has_letter_column_header_row(cells) or is_date_only_header_row(cells):
            has_col = True
        if len({c for c in cells if len(c) <= 8}) >= 2:
            has_col = True
    if has_unit:
        return True
    return has_period and has_col


def _gap_is_period_header_bridge(gap_items: Sequence[SourceItem]) -> bool:
    """间隙为下一报告期表头折行（如 2023年 + 列标），非表后叙述。"""
    if not _gap_has_period_section_header(gap_items):
        return False
    # （二）（三）等新小节 + 其表头中的年份，不是同表「跨期折行桥」
    if _gap_has_new_table_boundary(gap_items):
        return False
    text = _items_to_text(gap_items)
    if re.search(r"(下表|如下|包括|分别为)", text):
        return False
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return False
    for ln in lines:
        if ln.rstrip().endswith("。") and len(re.findall(r"[\u4e00-\u9fff]", ln)) >= 16:
            return False
    return True


def _gap_is_intra_table_continuation(
    page: PageSource,
    merged: RegionBox,
    nxt: RegionBox,
    gap_items: Sequence[SourceItem],
) -> bool:
    """间隙为空行/节标题/折行续片、下块无新表头 → 同一张表续片。"""
    if _gap_has_new_table_boundary(gap_items):
        return False
    if _gap_is_period_header_bridge(gap_items):
        return True
    if _gap_has_period_section_header(gap_items) and not _gap_has_narrative_text(gap_items):
        return True
    if _region_top_starts_with_fresh_header(page, nxt):
        return False
    return True


def _gap_has_period_section_header(gap_items: Sequence[SourceItem]) -> bool:
    """间隙含下一段报告期年份（2023年/2024年）及表头折行。"""
    if not gap_items:
        return False
    for it in gap_items:
        t = str(it.text or "").strip()
        if is_year_cell(t) or (is_report_date_cell(t) and "年" in t):
            return True
    return False


def _gap_has_narrative_text(gap_items: Sequence[SourceItem]) -> bool:
    if _gap_is_period_header_bridge(gap_items):
        return False
    text = _items_to_text(gap_items)
    if not text:
        return False
    return _is_narrative_gap_text(text) and len(re.findall(r"[\u4e00-\u9fff]", text)) >= 28


def _merge_continuation_regions(
    page: PageSource,
    regions: List[Tuple[int, RegionBox]],
    start: int,
) -> Tuple[int, RegionBox, int, int, bool]:
    """合并续表 region（间隙小且无新表题）。返回 (index, box, next_idx, merged_count, period_bridge_merge)。"""
    region_index, region = regions[start]
    merged = region
    end = start + 1
    period_bridge_merge = False
    while end < len(regions):
        _, nxt = regions[end]
        gap = nxt.y0 - merged.y1
        if not _regions_overlap_x(merged, nxt):
            break
        gap_items = _collect_gap_items(
            page,
            merged.y1,
            nxt.y0,
            x_lo=merged.x0 - 10,
            x_hi=merged.x1 + 10,
        )
        if _gap_has_table_caption(gap_items):
            break
        # 下一块已是完整新表头（如「2024/2023/增减/2022」）→ 绝不当续表吞并
        # 否则 boundary_overlap 会把 max_gap 放到 180，误并两张独立表并弄丢右列
        if _region_top_starts_with_fresh_header(page, nxt):
            break
        if _gap_is_period_header_bridge(gap_items):
            period_bridge_merge = True
        if _gap_is_intra_table_continuation(page, merged, nxt, gap_items):
            max_gap = _SECTION_CONTINUATION_MAX_GAP
        elif _gap_is_intra_table_section(gap_items):
            max_gap = _SECTION_CONTINUATION_MAX_GAP
        else:
            max_gap = _CONTINUATION_MAX_GAP
        if gap > max_gap:
            if (
                not gap_has_narrative_text(gap_items)
                and region_pair_has_boundary_overlap(page, merged, nxt, gap_items)
            ):
                max_gap = max(max_gap, _LABEL_SUFFIX_CONTINUATION_MAX_GAP)
        if gap > max_gap:
            break
        merged = _union_regions(merged, nxt)
        end += 1
    return region_index, merged, end, end - start, period_bridge_merge


@dataclass
class PageScopePlan:
    scopes: List[TableScope] = field(default_factory=list)
    gap_texts: List[TextBlock] = field(default_factory=list)
    headerless_absorbed_item_ids: set[str] = field(default_factory=set)


def _region_area(region: RegionBox) -> float:
    return max(0.0, region.y1 - region.y0) * max(0.0, region.x1 - region.x0)


_HEADER_STAMP_MAX_GAP = 15.0


def _is_header_stamp_region(region: RegionBox, next_region: Optional[RegionBox]) -> bool:
    """邻接的矮 region：多为下一张表的表头残片，非独立小表。"""
    height = region.y1 - region.y0
    if height > _HEADER_STAMP_MAX_HEIGHT:
        return False
    if next_region is None:
        return height < 35.0
    gap = next_region.y0 - region.y1
    return gap <= _HEADER_STAMP_MAX_GAP


def _collect_gap_items(
    page: PageSource,
    gap_y0: float,
    gap_y1: float,
    *,
    x_lo: Optional[float] = None,
    x_hi: Optional[float] = None,
    exclude_item_ids: Optional[Set[str]] = None,
) -> List[SourceItem]:
    if gap_y1 - gap_y0 < 2.0:
        return []
    lo = x_lo if x_lo is not None else 0.0
    hi = x_hi if x_hi is not None else page.page_width
    skip = exclude_item_ids or set()
    out: List[SourceItem] = []
    for it in page.items:
        if skip and str(it.item_index) in skip:
            continue
        cy = it.bbox.cy
        if gap_y0 - 2 <= cy < gap_y1:
            if lo - 5 <= it.bbox.cx <= hi + 5:
                out.append(it)
    out.sort(key=lambda x: (x.y_mid, x.x0))
    return out


def _items_to_text(items: Sequence[SourceItem]) -> str:
    if not items:
        return ""
    dicts = source_items_to_dicts(list(items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    parts: List[str] = []
    for row in rows:
        line = " ".join(
            str(it.get("text", "")).strip()
            for it in sorted(row.get("items", []), key=lambda d: d.get("x0", 0))
            if str(it.get("text", "")).strip()
        )
        if line:
            parts.append(line)
    return "\n".join(parts)


_NARRATIVE_BLOCK_BREAK_GAP_PT = 28.0


def _append_gap_narrative_blocks(
    gap_texts: List[TextBlock],
    page_num: int,
    narrative: Sequence[SourceItem],
) -> None:
    if not narrative:
        return
    gap_texts.extend(_split_items_into_ordered_text_blocks(page_num, list(narrative)))


def _split_items_into_ordered_text_blocks(
    page_num: int,
    items: List[SourceItem],
) -> List[TextBlock]:
    """按 y 顺序拆成多个 TEXT 块，避免页眉与表后附注被合并成一个 entry。"""
    if not items:
        return []

    merged_rows = _cluster_gap_row_items(items)
    clusters: List[List[SourceItem]] = []
    current: List[SourceItem] = []
    prev_y1: float | None = None

    for row_items in merged_rows:
        if not row_items:
            continue
        row_y0 = min(it.bbox.y0 for it in row_items)
        row_y1 = max(it.bbox.y1 for it in row_items)

        if _gap_row_is_page_chrome_row(row_items):
            if current:
                clusters.append(current)
                current = []
            clusters.append(list(row_items))
            prev_y1 = row_y1
            continue

        if (
            prev_y1 is not None
            and row_y0 - prev_y1 > _NARRATIVE_BLOCK_BREAK_GAP_PT
            and current
        ):
            clusters.append(current)
            current = []

        current.extend(row_items)
        prev_y1 = row_y1

    if current:
        clusters.append(current)

    blocks: List[TextBlock] = []
    for cluster in clusters:
        if not cluster:
            continue
        # role 交由 ensure_page_chrome_separated 统一标注，避免中间正文误标页脚
        blocks.append(_make_text_block(page_num, cluster, role=None))
    return blocks


def _make_text_block(
    page_num: int,
    items: Sequence[SourceItem],
    *,
    role: Optional[str] = None,
) -> TextBlock:
    text = _items_to_text(items)
    y0 = min(it.bbox.y0 for it in items)
    y1 = max(it.bbox.y1 for it in items)
    return TextBlock(
        page=page_num,
        y0=y0,
        y1=y1,
        text=text,
        source_items=[it.item_index for it in items],
        role=role,
    )


def _is_narrative_gap_text(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", t))
    if cn >= _NARRATIVE_MIN_CN:
        return True
    if re.search(r"\d+\.\d+\s", t):
        return True
    if "。" in t and cn >= 8:
        return True
    return False


def _is_page_chrome_items(
    items: Sequence[SourceItem],
    *,
    page_height: float = 800.0,
) -> bool:
    if not items:
        return False
    if items_look_like_page_chrome(items, page_height=page_height):
        return True
    joined = "".join(str(it.text or "") for it in items)
    if any(m in joined for m in _ANNUAL_REPORT_CHROME_MARKERS):
        return True
    if max(it.bbox.y1 for it in items) > _PAGE_CHROME_MAX_Y:
        return False
    return any(m in joined for m in _PAGE_CHROME_MARKERS)


def _gap_row_is_page_chrome_row(row_items: List[SourceItem]) -> bool:
    return _is_page_chrome_items(row_items)


def _gap_row_should_exclude_from_headerless(row_items: List[SourceItem]) -> bool:
    return (
        _gap_row_is_footnote_or_narrative_prose(row_items)
        or _gap_row_is_section_opener(row_items)
        or _gap_row_is_subsection_heading(row_items)
        or _gap_row_is_page_chrome_row(row_items)
    )


def _cluster_gap_row_items(items: List[SourceItem]) -> List[List[SourceItem]]:
    if not items:
        return []
    dicts = source_items_to_dicts(items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    return _merge_split_section_caption_rows(rows, index_map)


def _gap_row_is_orphan_body_tail(row_items: List[SourceItem]) -> bool:
    """间隙中的上一表期末行（报告期日期 + 金额列），非下一张表表头。"""
    if not row_items:
        return False
    ordered = sorted(row_items, key=lambda it: it.bbox.x0)
    cells = [str(it.text).strip() for it in ordered if str(it.text).strip()]
    if len(cells) < 2:
        return False
    if not any(
        is_year_cell(c) or (is_report_date_cell(c) and "年" in c)
        for c in cells
    ):
        return False
    padded = ["", *cells]
    return row_has_body_value_data(padded, value_start=1)


def _gap_row_is_section_opener(row_items: List[SourceItem]) -> bool:
    """间隙中的新节标题（如 32 已发行债务证券），应进 TEXT 而非下一张表 scope。"""
    if not row_items:
        return False
    ordered = sorted(row_items, key=lambda it: it.bbox.x0)
    cells = [str(it.text).strip() for it in ordered if str(it.text).strip()]
    if not cells:
        return False
    joined = " ".join(cells)
    if re.match(r"^[（(]\d+[)）]", cells[0]) and len(cells) >= 2:
        cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
        return cn >= 8
    if not re.match(r"^\d{1,2}\s+[\u4e00-\u9fff]", joined):
        return False
    if any(
        is_numeric_data_cell(str(it.text).strip()) and it.bbox.x0 > 140
        for it in ordered
        if str(it.text).strip()
    ):
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    return cn <= 24 and len(cells) <= 3


def _gap_row_is_orphan_table_tail_row(
    row_items: List[SourceItem],
    *,
    large_gap_above: bool,
) -> bool:
    """大间隙中上一表表尾数据行（无报告期日期）→ TEXT，不进下一张表 description。"""
    if not large_gap_above or not row_items:
        return False
    return _gap_row_is_scope_table_body_row(row_items)


def _gap_row_is_scope_table_body_row(row_items: List[SourceItem]) -> bool:
    """标签列 + 多列数值的表体行：属 TABLE scope，不应重复进 gap TEXT。"""
    if not row_items:
        return False
    ordered = sorted(row_items, key=lambda it: it.bbox.x0)
    cells = [str(it.text).strip() for it in ordered if str(it.text).strip()]
    if len(cells) < 3:
        return False
    has_label = any(
        it.bbox.x0 < 250
        and not is_numeric_data_cell(str(it.text).strip())
        and str(it.text).strip() not in ("–", "-", "—", "－")
        for it in ordered
        if str(it.text).strip()
    )
    if not has_label:
        return False
    numeric_cols = sum(
        1
        for it in ordered
        if is_numeric_data_cell(str(it.text).strip())
        and it.bbox.x0 >= _LABEL_VALUE_MIN_X
    )
    if numeric_cols < 2:
        return False
    padded = ["", *cells]
    return row_has_body_value_data(padded, value_start=1)


def _gap_row_items_as_cells(row_items: List[SourceItem]) -> List[str]:
    ordered = sorted(row_items, key=lambda it: it.bbox.x0)
    return [str(it.text).strip() for it in ordered if str(it.text).strip()]


def _gap_row_is_label_wrap_tail(row_items: List[SourceItem]) -> bool:
    """折行标签尾片（无值列），与上行表体同属一张无表头表。"""
    cells = _gap_row_items_as_cells(row_items)
    if not cells:
        return False
    if row_is_wrapped_label_continuation_tail(cells):
        return True
    if len(row_items) != 1:
        return False
    it = row_items[0]
    text = str(it.text or "").strip()
    if len(text) < 9 or not re.search(r"[\u4e00-\u9fff]", text):
        return False
    x0 = float(it.bbox.x0)
    return 120.0 <= x0 <= 280.0


def _gap_row_is_footnote_or_narrative_prose(row_items: List[SourceItem]) -> bool:
    from codes.table_engine.scope.header_scope import row_is_footnote_prose_row

    if row_is_footnote_prose_row(row_items):
        return True
    cells = _gap_row_items_as_cells(row_items)
    if not cells:
        return False
    joined = "".join(cells)
    if _FOOTNOTE_ROW_RE.match(cells[0]):
        return True
    if _NUMBERED_FOOTNOTE_ITEM_RE.match(cells[0]) and len(joined) >= 16:
        return True
    if len(joined) >= 42 and _is_narrative_gap_text(joined):
        return True
    return False


def _gap_row_belongs_to_headerless_table(row_items: List[SourceItem]) -> bool:
    return (
        _gap_row_is_scope_table_body_row(row_items)
        or _gap_row_is_label_wrap_tail(row_items)
    )


_WRAP_FRAGMENT_STOPWORDS = frozenset({
    "项目", "单位", "增减", "本行", "本集团", "营业收入", "总资产",
})
_WRAP_FRAGMENT_PREFIXES = ("的", "量")


def _text_is_standalone_wrap_fragment(text: str) -> bool:
    """无表头表折行尾片：短片段或「的/量…」前缀，不含列标/表头词。"""
    t = str(text or "").strip()
    if not t or t in _WRAP_FRAGMENT_STOPWORDS:
        return False
    if _FOOTNOTE_ROW_RE.match(t):
        return False
    if any(m in t for m in _ANNUAL_REPORT_CHROME_MARKERS):
        return False
    if re.match(r"^[（(][一二三四五六七八九十\d]", t):
        return False
    if is_likely_next_table_header_row([t]):
        return False
    if t in _LABEL_SUFFIX_WORDS:
        return True
    if t.startswith(_WRAP_FRAGMENT_PREFIXES):
        return True
    if len(t) <= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", t):
        return True
    return False


def _item_is_label_wrap_fragment(
    it: SourceItem,
    pool: List[SourceItem],
) -> bool:
    """单 item 折行尾片：同 y 带无表体数值，且为短标签片段。"""
    text = str(it.text or "").strip()
    if not text:
        return False
    if _FOOTNOTE_ROW_RE.match(text):
        return False
    if any(m in text for m in _ANNUAL_REPORT_CHROME_MARKERS):
        return False
    if re.match(r"^[（(][一二三四五六七八九十\d]", text):
        return False
    if not _text_is_standalone_wrap_fragment(text):
        return False
    row_mates = [
        other for other in pool
        if abs(other.bbox.y0 - it.bbox.y0) < 5.0
    ]
    if _gap_row_is_scope_table_body_row(row_mates):
        return False
    return True


def _collect_label_wrap_tail_item_rows(
    pool: List[SourceItem],
    *,
    y_lo: float,
    y_hi: float,
) -> List[List[SourceItem]]:
    """折行尾片按单 item 识别，避免与节标题/列标 y 聚类粘连。"""
    groups: List[List[SourceItem]] = []
    for it in pool:
        if not (y_lo <= it.bbox.y0 <= y_hi):
            continue
        if _item_is_address_wrap_fragment(it, pool):
            groups.append([it])
            continue
        if it.bbox.x0 > _LABEL_VALUE_MIN_X + 30:
            continue
        if not _item_is_label_wrap_fragment(it, pool):
            continue
        groups.append([it])
    return groups


def _text_is_address_wrap_fragment(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t) < 9:
        return False
    if _FOOTNOTE_ROW_RE.match(t):
        return False
    if any(m in t for m in _ANNUAL_REPORT_CHROME_MARKERS):
        return False
    if re.match(r"^[（(][一二三四五六七八九十\d]", t):
        return False
    if is_likely_next_table_header_row([t]):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", t))


def _item_is_address_wrap_fragment(
    it: SourceItem,
    pool: List[SourceItem],
) -> bool:
    """机构地址等宽文本列折行尾片。"""
    text = str(it.text or "").strip()
    if not _text_is_address_wrap_fragment(text):
        return False
    x0 = float(it.bbox.x0)
    if x0 < 115.0 or x0 > _LABEL_VALUE_MIN_X + 90:
        return False
    row_mates = [
        other for other in pool
        if abs(other.bbox.y0 - it.bbox.y0) < 5.0
    ]
    if _gap_row_is_scope_table_body_row(row_mates):
        return False
    return True


def _assign_and_fuse_wraps_to_bodies(
    body_groups: List[List[SourceItem]],
    wrap_groups: List[List[SourceItem]],
) -> List[List[SourceItem]]:
    """折行尾片并入表体：交错行用「最近上方表体」，整块尾片用顺序配对。"""
    bodies = sorted(body_groups, key=lambda r: min(it.bbox.y0 for it in r))
    wraps = sorted(wrap_groups, key=lambda r: min(it.bbox.y0 for it in r))
    if not bodies or not wraps:
        return bodies

    last_body_y = max(min(it.bbox.y0 for it in b) for b in bodies)
    first_wrap_y = min(min(it.bbox.y0 for it in w) for w in wraps)
    if first_wrap_y > last_body_y + 3.0:
        for i in range(min(len(bodies), len(wraps))):
            _fuse_wrap_row_into_body_row(bodies[i], wraps[i])
        return bodies

    fused: set[int] = set()
    for wrap in wraps:
        wy = min(it.bbox.y0 for it in wrap)
        candidates = [
            b for b in bodies
            if id(b) not in fused
            and min(it.bbox.y1 for it in b) <= wy + 2.0
        ]
        if not candidates:
            continue
        body = max(candidates, key=lambda b: min(it.bbox.y0 for it in b))
        _fuse_wrap_row_into_body_row(body, wrap)
        fused.add(id(body))
    return bodies


def _fuse_wrap_row_into_body_row(
    body_row: List[SourceItem],
    wrap_row: List[SourceItem],
) -> None:
    """按 x0 锚点将折行尾片并入表体行对应文本列（非一律最左列）。"""
    if not body_row or not wrap_row:
        return
    wrap_it = wrap_row[0]
    wrap_x0 = float(wrap_it.bbox.x0)

    def _is_text_body_item(it: SourceItem) -> bool:
        t = str(it.text or "").strip()
        if not t or is_numeric_data_cell(t):
            return False
        if re.match(r"^\d+[a-z]?$", t, re.IGNORECASE):
            return False
        return bool(re.search(r"[\u4e00-\u9fff]", t))

    text_items = [it for it in body_row if _is_text_body_item(it)]
    if text_items:
        label_it = min(text_items, key=lambda it: abs(float(it.bbox.x0) - wrap_x0))
    else:
        label_it = min(body_row, key=lambda it: it.bbox.x0)
    label_it.text = f"{str(label_it.text or '').strip()}{str(wrap_it.text or '').strip()}"
    label_it.bbox = BBox(
        min(label_it.bbox.x0, wrap_it.bbox.x0),
        min(label_it.bbox.y0, wrap_it.bbox.y0),
        max(label_it.bbox.x1, wrap_it.bbox.x1),
        max(label_it.bbox.y1, wrap_it.bbox.y1),
    )


def _extract_pre_region_headerless_table(
    items: List[SourceItem],
    page: PageSource,
    region: RegionBox,
) -> Tuple[List[List[SourceItem]], List[SourceItem], set[str]]:
    """首个 region 上方无表头矩阵：表体行 + 折行尾片（允许被附注隔断、尾片可略低于 region 顶）。"""
    if not items:
        return [], items, set()

    region_y0 = region.y0
    below_raw = _collect_gap_items(
        page,
        region_y0,
        region_y0 + _WRAP_TAIL_BELOW_REGION_PT,
        x_lo=region.x0 - 12,
        x_hi=region.x1 + 12,
    )
    subsection_ids: set[str] = set()
    for row in _cluster_gap_row_items(below_raw):
        if _gap_row_is_subsection_heading(row):
            subsection_ids.update(it.item_index for it in row)
    below_band = [it for it in below_raw if it.item_index not in subsection_ids]
    merged_rows = _cluster_gap_row_items(items)

    body_groups: List[List[SourceItem]] = []
    for row_items in merged_rows:
        if _gap_row_should_exclude_from_headerless(row_items):
            continue
        if not _gap_row_is_scope_table_body_row(row_items):
            continue
        if min(it.bbox.y0 for it in row_items) < region_y0 + 3.0:
            body_groups.append(row_items)

    if not body_groups:
        return [], items, set()

    y_lo = min(min(it.bbox.y0 for it in g) for g in body_groups) - 4.0
    y_hi = region_y0 + _WRAP_TAIL_BELOW_REGION_PT

    wrap_groups = _collect_label_wrap_tail_item_rows(
        items, y_lo=y_lo, y_hi=region_y0 + 2.0,
    )
    if below_band:
        below_wraps = _collect_label_wrap_tail_item_rows(
            below_band,
            y_lo=region_y0 - 1.0,
            y_hi=y_hi,
        )
        wrap_groups = wrap_groups + below_wraps
        wrap_groups.sort(key=lambda r: min(it.bbox.y0 for it in r))
    if len(wrap_groups) > len(body_groups):
        wrap_groups = wrap_groups[: len(body_groups)]
    block_rows = _assign_and_fuse_wraps_to_bodies(body_groups, wrap_groups)
    block_rows.sort(key=lambda r: min(it.bbox.y0 for it in r))

    absorbed_ids = {it.item_index for r in block_rows for it in r}
    absorbed_ids |= {it.item_index for r in wrap_groups for it in r}
    remaining = [it for it in items if it.item_index not in absorbed_ids]
    flat_items = merge_items_dedup(*(block_rows))
    return [flat_items], remaining, absorbed_ids


def _extract_trailing_headerless_table_rows(
    items: List[SourceItem],
    *,
    upcoming_region_y0: float,
    page: PageSource | None = None,
    region: RegionBox | None = None,
) -> Tuple[List[List[SourceItem]], List[SourceItem], set[str]]:
    """兼容入口 → _extract_pre_region_headerless_table。"""
    if page is not None and region is not None:
        return _extract_pre_region_headerless_table(items, page, region)
    return [], items, set()


def _filter_scope_table_tail_items(
    items: List[SourceItem],
    *,
    last_region_y1: float = 0.0,
) -> List[SourceItem]:
    """region 底边未盖住表底时，去掉仍属上一张表表体的 item（避免 TABLE/TEXT 双写）。"""
    if not items:
        return []
    dicts = source_items_to_dicts(items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    drop_ids: set[str] = set()
    for row_items in _merge_split_section_caption_rows(rows, index_map):
        if last_region_y1 > 0:
            row_y = min(it.bbox.y0 for it in row_items)
            if row_y > last_region_y1 + _POST_TABLE_TAIL_CONTINUATION_Y:
                continue
        if _gap_row_is_scope_table_body_row(row_items):
            drop_ids.update(it.item_index for it in row_items)
    if not drop_ids:
        return items
    return [it for it in items if it.item_index not in drop_ids]


def _post_gap_row_opens_table_block(row_items: List[SourceItem]) -> bool:
    cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
    if not cells:
        return False
    if is_annual_report_column_header_row(cells):
        return True
    if is_annual_report_unit_row(cells):
        return True
    if has_annual_column_header_band([cells]):
        return True
    return False


def _post_gap_row_is_narrative_break(row_items: List[SourceItem]) -> bool:
    from codes.table_engine.geometry.boundary_expand import _row_is_section_break_items

    if _row_is_section_break_items(row_items):
        return True
    if _gap_row_is_subsection_heading(row_items):
        return True
    cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
    joined = "".join(cells)
    if re.match(r"^\d+[\.．、]\s*[\u4e00-\u9fff]", joined) and not any(
        is_numeric_data_cell(c) and it.bbox.x0 > 160
        for it, c in zip(row_items, cells)
        if c
    ):
        return True
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    return cn >= 20 and joined.rstrip().endswith("。")


def _extract_post_region_table_scopes(
    page: PageSource,
    last_region: RegionBox,
    post_items: List[SourceItem],
) -> Tuple[List[TableScope], List[SourceItem]]:
    """末 region 下方 gap 内的新表（如「2.投资收益」后的单位+项目表）。"""
    if not post_items:
        return [], post_items

    row_lists = _cluster_gap_row_items(post_items)
    if not row_lists:
        return [], post_items

    scopes: List[TableScope] = []
    consumed: set[str] = set()
    virtual = 0
    i = 0
    while i < len(row_lists):
        row = row_lists[i]
        starts = _post_gap_row_opens_table_block(row)
        if not starts and i + 1 < len(row_lists):
            if is_annual_report_unit_row(
                [str(it.text).strip() for it in row if str(it.text).strip()]
            ) and _post_gap_row_opens_table_block(row_lists[i + 1]):
                starts = True
        if not starts:
            i += 1
            continue

        block: List[SourceItem] = []
        if (
            i > 0
            and is_annual_report_unit_row(
                [str(it.text).strip() for it in row_lists[i - 1] if str(it.text).strip()]
            )
            and all(it.item_index not in consumed for it in row_lists[i - 1])
        ):
            block.extend(row_lists[i - 1])
            consumed.update(it.item_index for it in row_lists[i - 1])

        j = i
        while j < len(row_lists):
            cur = row_lists[j]
            if j > i and _post_gap_row_is_narrative_break(cur):
                break
            if j > i + 1 and _post_gap_row_opens_table_block(cur):
                break
            block.extend(cur)
            consumed.update(it.item_index for it in cur)
            j += 1

        if len(block) >= 2:
            items = merge_items_dedup(block)
            x0 = min(it.bbox.x0 for it in items) - 5.0
            x1 = max(it.bbox.x1 for it in items) + 5.0
            y0 = min(it.bbox.y0 for it in items)
            y1 = max(it.bbox.y1 for it in items)
            vidx = _TRAILING_GAP_TABLE_BASE + virtual
            virtual += 1
            region = RegionBox(x0, y0, x1, y1, 1.0, vidx)
            remaining, pre = peel_pre_header_from_items(items)
            scope = build_table_scope(
                page,
                region,
                vidx,
                pre_header_items=pre,
            )
            scope.metadata["trailing_gap_table"] = True
            scopes.append(scope)
        i = j

    remaining = [it for it in post_items if it.item_index not in consumed]
    return scopes, remaining


def _gap_row_is_upcoming_table_caption(
    row_items: List[SourceItem],
    *,
    upcoming_region_y0: Optional[float],
    large_gap_above: bool,
) -> bool:
    """附注节标题紧贴 upcoming region 顶边 → 并入该表 description，非独立 TEXT。"""
    if large_gap_above or upcoming_region_y0 is None:
        return False
    cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
    if not cells:
        return False
    joined = " ".join(cells)
    if not re.match(r"^\d{1,2}\s+[\u4e00-\u9fff]", joined):
        return False
    if any(
        is_numeric_data_cell(str(it.text).strip()) and it.bbox.x0 > 140
        for it in row_items
        if str(it.text).strip()
    ):
        return False
    row_y = min(it.bbox.y0 for it in row_items)
    if upcoming_region_y0 - row_y > 65:
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    return cn <= 30 and len(cells) <= 3


def _merge_split_section_caption_rows(
    clustered_rows: List[dict],
    index_map: dict,
) -> List[List[SourceItem]]:
    """「44」与下一行标题折行 → 合并为一条附注节标题。"""
    row_item_lists: List[List[SourceItem]] = []
    for row in clustered_rows:
        row_items = [
            index_map[d["item_index"]]
            for d in row.get("items", [])
            if d.get("item_index") in index_map
        ]
        if row_items:
            row_item_lists.append(row_items)

    merged: List[List[SourceItem]] = []
    i = 0
    while i < len(row_item_lists):
        row_items = row_item_lists[i]
        cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
        if (
            len(cells) == 1
            and re.fullmatch(r"\d{1,2}", cells[0])
            and i + 1 < len(row_item_lists)
        ):
            nxt = row_item_lists[i + 1]
            nxt_cells = [str(it.text).strip() for it in nxt if str(it.text).strip()]
            if (
                len(nxt_cells) == 1
                and re.search(r"[\u4e00-\u9fff]", nxt_cells[0])
                and not is_numeric_data_cell(nxt_cells[0])
            ):
                merged.append(row_items + nxt)
                i += 2
                continue
        merged.append(row_items)
        i += 1
    return merged


def _gap_row_is_subsection_heading(row_items: List[SourceItem]) -> bool:
    """年报小节标题：4.11 …、（一）…、（1）…。"""
    if not row_items:
        return False
    ordered = sorted(row_items, key=lambda it: it.bbox.x0)
    cells = [str(it.text).strip() for it in ordered if str(it.text).strip()]
    if not cells:
        return False
    joined = " ".join(cells)
    if re.match(r"^\d+\.\d+\s*[\u4e00-\u9fff]", joined):
        return True
    if re.match(r"^\d+[\.．、]\s*[\u4e00-\u9fff]", joined):
        if not any(
            is_numeric_data_cell(str(it.text).strip()) and it.bbox.x0 > 160
            for it in ordered
            if str(it.text).strip()
        ):
            return True
    if re.match(r"^[（(][一二三四五六七八九十\d]+[)）]\s*[\u4e00-\u9fff]", joined):
        return True
    if re.match(r"^[（(]\d+[)）]\s*[\u4e00-\u9fff]", joined):
        return True
    return False


def _region_is_award_list_block(page: PageSource, region: RegionBox) -> bool:
    """荣誉/奖项列表（无大额财务数值）→ TEXT，不当表格。"""
    items = _collect_gap_items(
        page,
        region.y0,
        region.y1,
        x_lo=region.x0 - 10,
        x_hi=region.x1 + 10,
    )
    if len(items) < 6:
        return False
    for it in items:
        t = str(it.text or "").strip().replace("，", ",")
        if re.match(r"^-?\d{1,3}(?:,\d{3}){2,}", t):
            return False
    serial_like = 0
    for it in items:
        t = str(it.text or "").strip()
        if re.fullmatch(r"\d{1,2}", t):
            serial_like += 1
        elif re.match(r"^\d{1,2}\s+[\u4e00-\u9fff]", t):
            serial_like += 1
    return serial_like >= 3


def _gap_row_is_annual_column_header_row(cells: List[str]) -> bool:
    """年报表头行：项目 + 多个报告期列 / 增减列。"""
    from codes.table_engine.scope.header_scope import is_annual_report_column_header_row
    return is_annual_report_column_header_row(cells)


def _split_large_gap_narrative_and_headers(
    items: List[SourceItem],
) -> Tuple[List[SourceItem], List[SourceItem]]:
    """大间隙：叙述按 y 保留；单位/列标进 pre_header 并入下一张表。"""
    from codes.table_engine.scope.header_scope import (
        is_annual_header_wrap_subrow,
        is_pre_table_header_band_row,
        is_rmb_unit_lead_row,
    )

    if not items:
        return [], []

    dicts = source_items_to_dicts(items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    merged_rows = _merge_split_section_caption_rows(rows, index_map)

    header_items: List[SourceItem] = []
    narrative_items: List[SourceItem] = []
    for row_items in merged_rows:
        if not row_items:
            continue
        if _gap_row_is_footnote_or_narrative_prose(row_items):
            narrative_items.extend(row_items)
            continue
        cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
        if (
            is_rmb_unit_lead_row(cells)
            or is_annual_report_unit_row(cells)
            or is_pre_table_header_band_row(row_items)
            or _gap_row_is_annual_column_header_row(cells)
            or is_annual_header_wrap_subrow(row_items)
        ):
            header_items.extend(row_items)
            continue
        if _gap_row_is_orphan_body_tail(row_items):
            narrative_items.extend(row_items)
            continue
        if _gap_row_is_section_opener(row_items):
            narrative_items.extend(row_items)
            continue
        if _gap_row_is_subsection_heading(row_items):
            narrative_items.extend(row_items)
            continue
        if _is_page_chrome_items(row_items):
            narrative_items.extend(row_items)
            continue
        narrative_items.extend(row_items)

    return narrative_items, header_items


def _split_description_and_narrative(
    items: List[SourceItem],
    *,
    upcoming_region_y0: Optional[float] = None,
    large_gap_above: bool = False,
) -> Tuple[str, List[SourceItem], List[SourceItem]]:
    """间隙剩余块：表题/短说明 vs 长叙述（均保留 source item 归属）。"""
    if not items:
        return "", [], []

    dicts = source_items_to_dicts(items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    desc_parts: List[str] = []
    desc_items: List[SourceItem] = []
    narrative_items: List[SourceItem] = []
    index_map = {it.item_index: it for it in items}

    merged_rows = _merge_split_section_caption_rows(rows, index_map)
    for row_items in merged_rows:
        if not row_items:
            continue
        if _gap_row_is_footnote_or_narrative_prose(row_items):
            narrative_items.extend(row_items)
            continue
        if _gap_row_is_orphan_body_tail(row_items):
            narrative_items.extend(row_items)
            continue
        if _gap_row_is_orphan_table_tail_row(row_items, large_gap_above=large_gap_above):
            narrative_items.extend(row_items)
            continue
        if _gap_row_is_upcoming_table_caption(
            row_items,
            upcoming_region_y0=upcoming_region_y0,
            large_gap_above=large_gap_above,
        ):
            line = " ".join(str(it.text).strip() for it in row_items if str(it.text).strip())
            desc_parts.append(line)
            desc_items.extend(row_items)
            continue
        if _gap_row_is_section_opener(row_items):
            narrative_items.extend(row_items)
            continue
        if _gap_row_is_subsection_heading(row_items):
            row_y = min(it.bbox.y0 for it in row_items)
            if (
                upcoming_region_y0 is not None
                and not large_gap_above
                and upcoming_region_y0 - row_y <= 32.0
            ):
                line = " ".join(
                    str(it.text).strip() for it in row_items if str(it.text).strip()
                )
                desc_parts.append(line)
                desc_items.extend(row_items)
                continue
            narrative_items.extend(row_items)
            continue
        if _is_page_chrome_items(row_items):
            narrative_items.extend(row_items)
            continue
        cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
        line = " ".join(cells)
        if any(_text_is_standalone_wrap_fragment(c) for c in cells):
            narrative_items.extend(row_items)
            continue
        if row_has_pillar_table_caption(cells) or (
            len(line) <= 48 and not _is_narrative_gap_text(line)
        ):
            desc_parts.append(line)
            desc_items.extend(row_items)
        else:
            narrative_items.extend(row_items)

    return "\n".join(desc_parts).strip(), desc_items, narrative_items


def _route_unassigned_gap_items(
    gap_items: Sequence[SourceItem],
    pre_header: Sequence[SourceItem],
    desc_items: Sequence[SourceItem],
    narrative: Sequence[SourceItem],
) -> List[SourceItem]:
    """gap 内未被 pre_header / desc / narrative 引用的 item → 补进 TEXT。"""
    routed = {
        it.item_index
        for it in (*pre_header, *desc_items, *narrative)
    }
    return [it for it in gap_items if it.item_index not in routed]


def _promote_header_desc_to_pre_header(
    pre_header: List[SourceItem],
    desc_items: List[SourceItem],
) -> Tuple[List[SourceItem], List[SourceItem], str]:
    """description 中误收的表头行 → pre_header（如报告期、折行列标）。"""
    from codes.table_engine.scope.header_scope import (
        is_annual_header_wrap_subrow,
        is_report_date_header_part_text,
    )

    if not desc_items:
        return pre_header, desc_items, ""

    dicts = source_items_to_dicts(desc_items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in desc_items}

    promoted: List[SourceItem] = []
    kept: List[SourceItem] = []
    for row in rows:
        row_items = [
            index_map[d["item_index"]]
            for d in row.get("items", [])
            if d.get("item_index") in index_map
        ]
        if not row_items:
            continue
        cells = [str(it.text).strip() for it in row_items if str(it.text).strip()]
        if _gap_row_is_orphan_body_tail(row_items) or _gap_row_is_section_opener(row_items):
            kept.extend(row_items)
            continue
        if is_annual_header_wrap_subrow(row_items) or _gap_row_is_annual_column_header_row(cells):
            promoted.extend(row_items)
            continue
        line_cells = cells[:1] if len(cells) == 1 else cells
        if any(
            row_has_reporting_date([c]) or is_wrapped_column_subheader_row([c])
            or is_report_date_header_part_text(c)
            for c in line_cells
        ) and not _gap_row_is_orphan_body_tail(row_items):
            promoted.extend(row_items)
        else:
            kept.extend(row_items)

    if not promoted:
        return pre_header, desc_items, _items_to_text(desc_items).strip()
    pre_header = merge_items_dedup(pre_header, promoted)
    desc = _items_to_text(kept).strip()
    return pre_header, kept, desc


def _finalize_gap_routing(
    gap_items: Sequence[SourceItem],
    pre_header: List[SourceItem],
    desc: str,
    desc_items: List[SourceItem],
    narrative: List[SourceItem],
) -> Tuple[str, List[SourceItem], List[SourceItem]]:
    """守恒：未路由 item 并入 narrative（TEXT），不丢弃。"""
    from codes.table_engine.scope.header_scope import partition_annual_header_wrap_items

    orphans = _route_unassigned_gap_items(gap_items, pre_header, desc_items, narrative)
    if orphans:
        narrative = list(narrative) + orphans
    narrative, wrap_header = partition_annual_header_wrap_items(narrative)
    if wrap_header:
        pre_header = merge_items_dedup(pre_header, wrap_header)
    return desc, desc_items, narrative


def _attach_wrap_header_to_previous_target(
    build_targets: List[
        Tuple[int, RegionBox, List[SourceItem], str, List[SourceItem], bool]
    ],
    wrap_header: Sequence[SourceItem],
) -> None:
    """折行表头碎片（增减/月日）物理落在邻表间隙时，回补到上一张表的 pre_header。"""
    if not wrap_header or not build_targets:
        return
    idx, region, pre_header, desc, desc_items, bridge = build_targets[-1]
    pre_header = merge_items_dedup(list(pre_header), list(wrap_header))
    build_targets[-1] = (idx, region, pre_header, desc, desc_items, bridge)


def _relocate_orphan_wrap_pre_header(
    pre_header: List[SourceItem],
    build_targets: List[
        Tuple[int, RegionBox, List[SourceItem], str, List[SourceItem], bool]
    ],
) -> List[SourceItem]:
    """折行碎片若未与当前 scope 主列标同批出现，归属上一张表表头。"""
    from codes.table_engine.scope.header_scope import (
        is_annual_header_wrap_subrow,
        partition_annual_header_wrap_items,
    )

    if not pre_header:
        return pre_header
    kept, wrap = partition_annual_header_wrap_items(pre_header)
    if not wrap:
        return pre_header

    dicts = source_items_to_dicts(kept)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    has_main_header = False
    for row in rows:
        cells = [
            str(d.get("text", "")).strip()
            for d in row.get("items", [])
            if str(d.get("text", "")).strip()
        ]
        if is_annual_report_column_header_row(cells):
            has_main_header = True
            break
    if has_main_header:
        return pre_header
    if build_targets:
        _attach_wrap_header_to_previous_target(build_targets, wrap)
        return kept
    return pre_header


def _region_rows_have_body_values_before(
    rows: Sequence[dict],
    before_idx: int,
) -> bool:
    """切分点上方已有表体数值（避免把页首小节误切）。"""
    for row in rows[:before_idx]:
        cells = [
            str(d.get("text", "")).strip()
            for d in row.get("items") or []
            if str(d.get("text", "")).strip()
        ]
        if row_has_body_value_data(cells):
            return True
    return False


def _row_looks_like_fresh_annual_header(cells: List[str]) -> bool:
    """新表表头：单位行 / 项目+报告期列 / 粘连报告期+增减。"""
    if not cells:
        return False
    if is_rmb_unit_lead_row(cells) or is_annual_report_unit_row(cells):
        return True
    if is_annual_report_column_header_row(cells):
        return True
    joined = " ".join(cells)
    if "项目" in joined and (
        "增减幅度" in joined
        or "主要原因" in joined
        or "变化原因" in joined
        or "12 月 31 日" in joined
        or "12月31日" in joined
    ):
        return True
    if "并表" in joined or "非并表" in joined:
        return True
    return False


def _split_merged_region_by_subsection(
    page: PageSource,
    region: RegionBox,
) -> List[RegionBox]:
    """单 region 内跨「（二）…」新小节 + 新表头时切开（liteparse 常误并上下表）。

    上块止于小节标题；下块从新表头起；标题与单位行留在间隙供 pre_header/叙述。
    若下方暂未识别到表头，仍在小节处切开（避免（四）（五）两表粘死）。
    """
    items = _collect_gap_items(
        page,
        region.y0 - 2.0,
        region.y1 + 2.0,
        x_lo=region.x0 - 10,
        x_hi=region.x1 + 10,
    )
    if len(items) < 8:
        return [region]

    dicts = source_items_to_dicts(items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    if len(rows) < 6:
        return [region]

    index_map = {it.item_index: it for it in items}
    cut_points: List[Tuple[float, float]] = []  # (upper_end_y, lower_start_y)

    for i, row in enumerate(rows):
        row_items = [
            index_map[d["item_index"]]
            for d in row.get("items") or []
            if d.get("item_index") in index_map
        ]
        if not row_items or not _gap_row_is_subsection_heading(row_items):
            continue
        if not _region_rows_have_body_values_before(rows, i):
            continue

        y_heading = min(it.bbox.y0 for it in row_items)
        y_header: Optional[float] = None
        scan_y1 = y_heading + 160.0
        for j in range(i + 1, min(i + 12, len(rows))):
            nxt = [
                index_map[d["item_index"]]
                for d in rows[j].get("items") or []
                if d.get("item_index") in index_map
            ]
            if not nxt:
                continue
            y0 = min(it.bbox.y0 for it in nxt)
            if y0 > scan_y1:
                break
            cells = [str(it.text).strip() for it in nxt if str(it.text).strip()]
            if _row_looks_like_fresh_annual_header(cells):
                y_header = y0
                break
        if y_header is None:
            # 硬切：上块止于小节，下块从下一有内容行开始
            for j in range(i + 1, min(i + 12, len(rows))):
                nxt = [
                    index_map[d["item_index"]]
                    for d in rows[j].get("items") or []
                    if d.get("item_index") in index_map
                ]
                if nxt:
                    y_header = min(it.bbox.y0 for it in nxt)
                    break
        if y_header is None or y_header <= y_heading + 4.0:
            continue
        cut_points.append((y_heading, y_header))

    if not cut_points:
        return [region]

    pieces: List[RegionBox] = []
    cursor = region.y0
    for upper_end, lower_start in cut_points:
        if upper_end - cursor >= 36.0:
            pieces.append(
                RegionBox(
                    x0=region.x0,
                    y0=cursor,
                    x1=region.x1,
                    y1=upper_end,
                    confidence=region.confidence,
                )
            )
        cursor = lower_start
    if region.y1 - cursor >= 36.0:
        pieces.append(
            RegionBox(
                x0=region.x0,
                y0=cursor,
                x1=region.x1,
                y1=region.y1,
                confidence=region.confidence,
            )
        )
    return pieces if len(pieces) >= 2 else [region]


def _expand_regions_split_by_subsection(
    page: PageSource,
    regions: List[Tuple[int, RegionBox]],
) -> List[Tuple[int, RegionBox]]:
    """预处理：把误并的大 region 按年报小节切开。"""
    out: List[Tuple[int, RegionBox]] = []
    for region_index, region in regions:
        parts = _split_merged_region_by_subsection(page, region)
        for pi, part in enumerate(parts):
            # 一律 region*1000+片段号：避免「region0 拆出的片段1」与「原 region1」撞号
            # （撞号会导致 seen_regions 跳过整张 liteparse 表，如 P29（五）利息净收入）
            out.append((region_index * 1000 + pi, part))
    out.sort(key=lambda pair: pair[1].y0)
    return out


def plan_page_scopes(page: PageSource) -> PageScopePlan:
    """为页内每个逻辑表规划 TableScope（含 gap 表头回补）。"""
    if not page.table_regions:
        return PageScopePlan()

    _, chrome_ids = extract_page_chrome(page)

    regions = sorted(
        enumerate(page.table_regions),
        key=lambda pair: pair[1].y0,
    )
    regions = _expand_regions_split_by_subsection(page, regions)
    build_targets: List[
        Tuple[int, RegionBox, List[SourceItem], str, List[SourceItem], bool]
    ] = []
    gap_texts: List[TextBlock] = []
    headerless_scopes: List[TableScope] = []
    headerless_absorbed_ids: set[str] = set()

    idx = 0
    while idx < len(regions):
        region_index, region = regions[idx]
        next_region = regions[idx + 1][1] if idx + 1 < len(regions) else None

        if _is_header_stamp_region(region, next_region) and next_region is not None:
            stamp_items = _collect_gap_items(
                page,
                region.y0 - 5,
                next_region.y0,
                x_lo=region.x0 - 10,
                x_hi=region.x1 + 10,
            )
            stamp_remaining, pre_header = peel_pre_header_from_items(stamp_items)
            between_items = _collect_gap_items(
                page,
                region.y1 + Y_MARGIN_BELOW,
                next_region.y0,
            )
            between_remaining, more_pre = peel_pre_header_from_items(between_items)
            pre_header = pre_header + more_pre
            remaining = merge_items_dedup(stamp_remaining, between_remaining)
            desc, desc_items, narrative = _split_description_and_narrative(remaining)
            desc, desc_items, narrative = _finalize_gap_routing(
                merge_items_dedup(stamp_items, between_items),
                pre_header,
                desc,
                desc_items,
                narrative,
            )
            if narrative:
                _append_gap_narrative_blocks(gap_texts, page.page_number, narrative)
            merged_index, merged_region, next_idx, _merge_count, period_bridge = (
                _merge_continuation_regions(page, regions, idx + 1)
            )
            build_targets.append(
                (merged_index, merged_region, pre_header, desc, desc_items, period_bridge)
            )
            idx = next_idx
            continue

        merged_index, merged_region, next_idx, _merge_count, period_bridge = (
            _merge_continuation_regions(page, regions, idx)
        )

        if _region_is_award_list_block(page, merged_region):
            award_items = _collect_gap_items(
                page,
                merged_region.y0,
                merged_region.y1,
                x_lo=merged_region.x0 - 10,
                x_hi=merged_region.x1 + 10,
            )
            if award_items:
                gap_texts.append(_make_text_block(page.page_number, award_items))
            idx = next_idx
            continue

        if idx == 0:
            gap_y0 = 0.0
        else:
            prev_region = regions[idx - 1][1]
            inter_gap = region.y0 - prev_region.y1
            if inter_gap > _LARGE_INTER_REGION_GAP:
                gap_y0 = prev_region.y1 + _POST_TABLE_GAP_Y0
            else:
                gap_y0 = prev_region.y1 + Y_MARGIN_BELOW
        gap_y1 = region.y0
        gap_items = _collect_gap_items(page, gap_y0, gap_y1)
        remaining, pre_header = peel_pre_header_from_items(gap_items)
        headerless_routed_ids: set[str] = set()
        if idx == 0:
            body_blocks, remaining, absorbed = _extract_pre_region_headerless_table(
                remaining,
                page,
                region,
            )
            headerless_absorbed_ids |= absorbed
            for bi, block in enumerate(body_blocks):
                headerless_routed_ids.update(it.item_index for it in block)
                headerless_scopes.append(
                    build_headerless_gap_scope(
                        page,
                        block,
                        virtual_index=_HEADERLESS_GAP_REGION_BASE + bi,
                    )
                )
        inter_gap_above = (
            region.y0 - regions[idx - 1][1].y1
            if idx > 0
            else 0.0
        )
        large_gap = inter_gap_above > _LARGE_INTER_REGION_GAP
        if large_gap:
            narrative, gap_headers = _split_large_gap_narrative_and_headers(remaining)
            pre_header = merge_items_dedup(pre_header, gap_headers)
            desc, desc_items = "", []
        else:
            desc, desc_items, narrative = _split_description_and_narrative(
                remaining,
                upcoming_region_y0=region.y0,
                large_gap_above=False,
            )
        pre_header, desc_items, desc = _promote_header_desc_to_pre_header(
            pre_header, desc_items,
        )
        route_exclude = (
            headerless_absorbed_ids
            if idx == 0 and headerless_absorbed_ids
            else headerless_routed_ids
        )
        route_source = (
            [it for it in gap_items if it.item_index not in route_exclude]
            if route_exclude
            else gap_items
        )
        desc, desc_items, narrative = _finalize_gap_routing(
            route_source, pre_header, desc, desc_items, narrative,
        )
        pre_header = _relocate_orphan_wrap_pre_header(pre_header, build_targets)
        if narrative:
            _append_gap_narrative_blocks(gap_texts, page.page_number, narrative)
        build_targets.append(
            (merged_index, merged_region, pre_header, desc, desc_items, period_bridge)
        )
        idx = next_idx

    scopes: List[TableScope] = []
    seen_regions: set[int] = set()
    for region_index, region, pre_header, desc, desc_items, period_bridge in build_targets:
        if region_index in seen_regions:
            continue
        seen_regions.add(region_index)
        scope = build_table_scope(
            page,
            region,
            region_index,
            pre_header_items=pre_header,
            description_text=desc,
            description_source_items=desc_items,
        )
        if headerless_absorbed_ids:
            scope.items = [
                it for it in scope.items
                if it.item_index not in headerless_absorbed_ids
            ]
        if period_bridge:
            scope.metadata["region_continuation_merged"] = True
        scopes.append(scope)

    scopes = headerless_scopes + scopes

    if not scopes:
        region_index, region = max(regions, key=lambda pair: _region_area(pair[1]))
        gap_items = _collect_gap_items(page, 0.0, region.y0)
        remaining, pre_header = peel_pre_header_from_items(gap_items)
        desc, desc_items, narrative = _split_description_and_narrative(remaining)
        desc, desc_items, narrative = _finalize_gap_routing(
            gap_items, pre_header, desc, desc_items, narrative,
        )
        if narrative:
            _append_gap_narrative_blocks(gap_texts, page.page_number, narrative)
        scopes.append(
            build_table_scope(
                page,
                region,
                region_index,
                pre_header_items=pre_header,
                description_text=desc,
                description_source_items=desc_items,
            )
        )

    if regions:
        last_region = max((r for _, r in regions), key=lambda r: r.y1)
        y_hi = (page.page_height or 800.0) - 25.0
        post_items = _collect_gap_items(
            page,
            last_region.y1 + _POST_TABLE_GAP_Y0,
            y_hi,
            x_lo=last_region.x0 - 10,
            x_hi=last_region.x1 + 10,
        )
        post_scopes, post_items = _extract_post_region_table_scopes(
            page, last_region, post_items,
        )
        scopes.extend(post_scopes)
        post_items = _filter_scope_table_tail_items(
            post_items, last_region_y1=last_region.y1,
        )
        if post_items:
            remaining, _pre = peel_pre_header_from_items(post_items)
            _desc, _desc_items, narrative = _split_description_and_narrative(remaining)
            _, _, narrative = _finalize_gap_routing(
                post_items, [], _desc, _desc_items, narrative,
            )
            if _desc and _desc_items and post_scopes:
                trail = post_scopes[-1]
                if not trail.description_text:
                    trail.description_text = _desc
                    trail.description_source_items = list(_desc_items)
                    _desc, _desc_items = "", []
            if narrative:
                _append_gap_narrative_blocks(gap_texts, page.page_number, narrative)
            if _desc_items:
                _append_gap_narrative_blocks(gap_texts, page.page_number, list(_desc_items))

    # 对照 liteparse：每个原始 table_region 必须被至少一个 scope 覆盖，否则补建
    scopes = _ensure_liteparse_regions_covered(page, scopes)

    # 页眉/页脚独立拆出（含原先被 page_height-25 裁掉的页脚）
    gap_texts = ensure_page_chrome_separated(page, gap_texts)
    if chrome_ids:
        # 避免页眉 items 被吸进首张表 scope
        for scope in scopes:
            scope.items = filter_items_without_chrome(scope.items, chrome_ids)
            if scope.pre_header_items:
                scope.pre_header_items = filter_items_without_chrome(
                    scope.pre_header_items, chrome_ids,
                )

    return PageScopePlan(
        scopes=scopes,
        gap_texts=gap_texts,
        headerless_absorbed_item_ids=headerless_absorbed_ids,
    )


def _y_overlap_ratio(a: RegionBox, b: RegionBox) -> float:
    """相对较小框高度的 Y 向重叠比例。"""
    hi = min(a.y1, b.y1)
    lo = max(a.y0, b.y0)
    ov = max(0.0, hi - lo)
    denom = max(1.0, min(a.y1 - a.y0, b.y1 - b.y0))
    return ov / denom


def _ensure_liteparse_regions_covered(
    page: PageSource,
    scopes: List[TableScope],
) -> List[TableScope]:
    """liteparse 检出的表区若未进入 scope，强制补上（防索引撞号/漏合并丢表）。"""
    if not page.table_regions:
        return scopes
    out = list(scopes)
    for ri, region in enumerate(page.table_regions):
        if (region.y1 - region.y0) < 20.0:
            continue
        covered = any(
            _y_overlap_ratio(region, s.region) >= 0.35
            for s in out
            if s.region is not None
        )
        if covered:
            continue
        print(
            f"  [Scope] P{page.page_number} liteparse region{ri} "
            f"y={region.y0:.0f}-{region.y1:.0f} 未被覆盖 → 补建 scope"
        )
        out.append(
            build_table_scope(
                page,
                region,
                region_index=ri * 1000 + 999,
            )
        )
    out.sort(key=lambda s: float(s.region.y0 if s.region else 0))
    return out
