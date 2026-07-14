# -*- coding: utf-8
"""表头带识别与 scope 上扩（无 table_validator 依赖）。"""

from __future__ import annotations

import re
from typing import List, Sequence

from codes.table_engine.geometry.column_anchors import is_report_period_cell
from codes.table_engine.geometry.numeric import (
    is_month_day_cell,
    is_numeric_data_cell,
    is_report_date_header_part_text,
    is_year_cell,
)
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.models import PageSource, RegionBox, SourceItem

_CC_HEADER_MARKERS = frozenset({"a", "b", "c", "数额", "代码"})
_ENTITY_SCOPE_LABELS = frozenset({"本集团", "本行"})
_MATURITY_COL_MARKERS = frozenset({
    "无期限",
    "实时偿还",
    "1个月以内",
    "1个月至3个月",
    "至3个月",
    "3个月至1年",
    "至1年",
    "1年至5年",
    "5年以上",
    "合计",
    "逾期",
    "即期偿还",
    "即时偿还",
})
_TABLE_SECTION_LEADS = frozenset({
    "资产",
    "负债",
    "投资",
    "衍生金融工具的名义金额",
})
_FV_COLUMN_MARKERS = frozenset({
    "金融资产",
    "债权类投资",
    "债券",
    "基金及其他",
    "权益工具",
    "资产合计",
    "衍生金融负债",
    "负债合计",
    "衍生",
})
_FV_HEADER_PHRASES = (
    "公允价值",
    "计入当期损益",
    "综合收益",
    "且其变动",
    "权益工具、",
)
_ROW_NUMBER_RE = re.compile(r"^\d{1,2}[a-z]?$", re.I)
_TABLE_CAPTION_RE = re.compile(r"表\s*\d+\s*[\(（][A-Za-z0-9]+[\)）]")
_WRAPPED_COL_SUBHEADER_MARKERS = (
    "不含息",
    "垫款",
    "占比",
    "百分比",
    "年利率",
    "减值",
)
_NARRATIVE_MIN_CN = 18


def _row_cells(row_items: Sequence[SourceItem]) -> List[str]:
    return [
        str(it.text).strip()
        for it in sorted(row_items, key=lambda x: x.x0)
        if str(it.text).strip()
    ]


def scope_y0_for_region(
    page: PageSource,
    region: RegionBox,
    *,
    upward_pt: float = 160.0,
    x_margin: float = 20.0,
) -> float:
    """表顶纳入 Y 范围：a/b/c 列标 + 年报表头带 + 表内首行「资产/负债」。"""
    y0 = float(region.y0)
    found: set[str] = set()
    band_items: List[SourceItem] = []

    for it in page.items:
        cy = it.bbox.cy
        if not (region.y0 - upward_pt <= cy < region.y0 + 8):
            continue
        if not (region.x0 - x_margin <= it.bbox.cx <= region.x1 + x_margin):
            continue
        band_items.append(it)
        t = str(it.text).strip()
        if t in ("a", "b", "c") and len(t) == 1:
            found.add(t)
            y0 = min(y0, it.bbox.y0)
        elif t in _CC_HEADER_MARKERS:
            y0 = min(y0, it.bbox.y0)

    if band_items:
        dicts = source_items_to_dicts(band_items)
        rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
        index_map = {it.item_index: it for it in band_items}
        for row in rows:
            row_items = [
                index_map[d["item_index"]]
                for d in row.get("items", [])
                if d.get("item_index") in index_map
            ]
            if not row_items:
                continue
            cells = _row_cells(row_items)
            if _scope_y0_expands_for_row(cells):
                y0 = min(y0, min(it.bbox.y0 for it in row_items))

    if len(found) >= 2:
        return max(0.0, y0 - 5.0)
    if y0 < region.y0 - 1.0:
        if _large_gap_above_region(page, region):
            return max(0.0, float(region.y0) - 5.0)
        return max(0.0, y0 - 5.0)
    return max(0.0, float(region.y0) - 5.0)


def has_letter_column_header_row(cells: List[str]) -> bool:
    labels = {
        c.lower()
        for c in cells
        if c and len(c) == 1 and c.isalpha()
    }
    if len(labels) >= 3 and {"a", "b", "c"}.issubset(labels):
        return True
    if len(labels) >= 2 and {"a", "b"}.issubset(labels):
        return True
    return False


def row_has_reporting_date(cells: List[str]) -> bool:
    for c in cells:
        t = str(c).strip()
        if not t or _ROW_NUMBER_RE.match(t):
            continue
        if is_year_cell(t) or is_month_day_cell(t):
            return True
        if re.search(r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日", t):
            return True
    return False


def is_rmb_unit_lead_row(cells: List[str]) -> bool:
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    lead = non_empty[0]
    if not lead.startswith(("（", "(")):
        return False
    if "人民币" not in lead and "百万元" not in lead and "万元" not in lead:
        return False
    return sum(1 for c in non_empty if is_numeric_data_cell(c)) == 0


def is_annual_report_unit_row(cells: List[str]) -> bool:
    """年报表单位行：单位：千元 / 单位:元 等（无括号前缀）。"""
    non_empty = [str(c).strip() for c in cells if str(c).strip()]
    if not non_empty or len(non_empty) > 2:
        return False
    joined = "".join(non_empty)
    if not joined.startswith("单位"):
        return False
    if not re.search(r"单位\s*[:：]", joined):
        return False
    if not re.search(r"(?:千元|万元|百万元|元|人民币|%|吨|立方米)", joined):
        return False
    return sum(1 for c in non_empty if is_numeric_data_cell(c)) == 0


_FOOTNOTE_PREFIX_RE = re.compile(r"^注[：:]")
_NUMBERED_NOTE_ITEM_RE = re.compile(r"^[12][\.．、]\s*[\u4e00-\u9fff]")


def _cell_is_header_date_token(text: str) -> bool:
    """表头日期格：短 token；长叙述里〔2010〕等引用年份不算。"""
    t = str(text or "").strip()
    if not t or len(t) > 28:
        return False
    if is_year_cell(t) or is_month_day_cell(t):
        return True
    return bool(re.fullmatch(r"(?:19|20)\d{2}年?", t))


def row_is_footnote_prose_row(row_items: Sequence[SourceItem]) -> bool:
    """表后/表间附注行（注：…、1./2. 列举说明），非列标表头。"""
    cells = _row_cells(row_items)
    if not cells:
        return False
    joined = "".join(cells)
    if _FOOTNOTE_PREFIX_RE.match(str(cells[0]).strip()):
        return True
    first = str(cells[0]).strip()
    if _NUMBERED_NOTE_ITEM_RE.match(first) and len(joined) >= 16:
        return "。" in joined or _cells_look_like_narrative_prose(cells)
    return False


def is_date_only_header_row(cells: List[str]) -> bool:
    texts = [c for c in cells if c]
    if not texts:
        return False
    if any(_ROW_NUMBER_RE.match(t) for t in texts):
        return False
    if any(_FOOTNOTE_PREFIX_RE.match(str(t).strip()) for t in texts):
        return False
    if _cells_look_like_narrative_prose(cells):
        return False
    date_like = sum(1 for t in texts if _cell_is_header_date_token(str(t)))
    numeric = sum(1 for t in texts if is_numeric_data_cell(t))
    return date_like >= 1 and numeric == 0


def is_period_year_header_row(row_items: Sequence[SourceItem]) -> bool:
    texts = _row_cells(row_items)
    return bool(texts) and all(is_year_cell(t) for t in texts)


def is_annual_report_column_header_row(cells: List[str]) -> bool:
    """年报表头行：项目 + 多个报告期列 / 增减列。"""
    if not cells:
        return False
    project_hits = sum(1 for c in cells if _cell_has_project_label(c))
    if project_hits >= 1:
        year_hits = sum(
            1 for c in cells
            if _cell_has_year_period_marker(c)
        )
        return year_hits >= 2
    if any(
        c in ("增减", "末增减", "本报告期比上年同期")
        for c in cells
    ):
        return any(_cell_has_year_period_marker(c) for c in cells)
    return False


def _cell_has_project_label(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if t == "项目":
        return True
    if t.startswith("项目"):
        return True
    if t.endswith("项目") and len(t) <= 20:
        return True
    return False


def _cell_has_year_period_marker(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if is_year_cell(t) or re.match(r"^\d{4}\s*年", t):
        return True
    if re.search(r"(?:19|20)\d{2}年", t):
        return True
    return False


def has_annual_column_header_band(
    rows: List[List[str]],
    *,
    scan: int = 12,
) -> bool:
    """表内是否已有「项目 + 多列报告期」等标准列标表头。"""
    for row in rows[:scan]:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if is_annual_report_column_header_row(cells):
            return True
    return False


def row_has_pillar_table_caption(cells: List[str]) -> bool:
    return bool(_TABLE_CAPTION_RE.search("".join(cells)))


def is_wrapped_column_subheader_row(cells: List[str]) -> bool:
    """折行列标（如「占不含息贷款」「和垫款总额」）。"""
    non_empty = [str(c).strip() for c in cells if str(c).strip()]
    if not non_empty or len(non_empty) > 2:
        return False
    joined = "".join(non_empty)
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if cn < 2 or cn > 18:
        return False
    if row_has_reporting_date(non_empty):
        return False
    if any(is_numeric_data_cell(c) for c in non_empty):
        return False
    if joined.startswith(("占", "和")) and cn >= 3:
        return True
    if joined.endswith("总额") and cn <= 12:
        return True
    if any(m in joined for m in _WRAPPED_COL_SUBHEADER_MARKERS):
        return True
    if re.search(r"[（(]%[）)]", joined):
        return True
    return False


def is_short_section_label_row(cells: List[str]) -> bool:
    """表内短小节标题（非表头带）。"""
    if row_has_pillar_table_caption(cells):
        return False
    if is_wrapped_column_subheader_row(cells):
        return False
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
    if is_date_only_header_row(cells):
        return False
    if row_has_reporting_date(cells):
        return False
    if is_rmb_unit_lead_row(cells):
        return False
    if has_letter_column_header_row(cells):
        return False
    return True


def _normalize_header_cell(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def is_entity_scope_header_cells(cells: List[str]) -> bool:
    labels = [_normalize_header_cell(c) for c in cells if str(c).strip()]
    if not labels:
        return False
    return all(lb in _ENTITY_SCOPE_LABELS for lb in labels)


def is_maturity_column_header_row(cells: List[str]) -> bool:
    """流动性/剩余到期日等多列中文列标行。"""
    norms = [_normalize_header_cell(c) for c in cells if str(c).strip()]
    if len(norms) < 3:
        return False
    hits = 0
    for n in norms:
        if n in _MATURITY_COL_MARKERS:
            hits += 1
            continue
        if any(m in n for m in ("无期限", "实时偿还", "合计", "即期", "逾期")):
            hits += 1
    return hits >= 3


def is_period_span_header_row(cells: List[str]) -> bool:
    """日期列下方的「1个月 / 3个月」等跨列子表头（≥2 个短标签）。"""
    norms = [_normalize_header_cell(c) for c in cells if str(c).strip()]
    if len(norms) < 2 or len(norms) > 5:
        return False
    if any(len(n) > 10 for n in norms):
        return False
    if any(re.search(r"\d{4}年\d{1,2}月", n) for n in norms):
        return False
    if any(is_numeric_data_cell(n) for n in norms):
        return False
    month_year = sum(1 for n in norms if "月" in n or re.fullmatch(r"\d{4}年?", n))
    return month_year >= 2


def is_single_year_label_row(cells: List[str]) -> bool:
    """单列报告期标签：2024年 / 2023年。"""
    non_empty = [str(c).strip() for c in cells if str(c).strip()]
    if len(non_empty) != 1:
        return False
    return bool(re.fullmatch(r"\d{4}年?", _normalize_header_cell(non_empty[0])))


def is_fair_value_column_header_row(cells: List[str]) -> bool:
    """公允价值层级表列标行。"""
    norms = [_normalize_header_cell(c) for c in cells if str(c).strip()]
    if len(norms) < 3:
        return False
    hits = 0
    for n in norms:
        if n in _FV_COLUMN_MARKERS:
            hits += 1
            continue
        if "合计" in n or "金融负债" in n or "金融资产" in n:
            hits += 1
    return hits >= 3


def is_wrapped_fv_header_row(cells: List[str]) -> bool:
    """折行表头：公允价值 / 计入当期损益 / 综合收益等（非节标题长句）。"""
    norms = [_normalize_header_cell(c) for c in cells if str(c).strip()]
    if not norms:
        return False
    if is_short_section_label_row(cells):
        return False
    if len(norms) == 1 and len(norms[0]) > 14:
        return False
    if any(is_numeric_data_cell(n) for n in norms):
        return False
    joined = "".join(norms)
    if len(joined) < 4:
        return False
    return any(p in joined for p in _FV_HEADER_PHRASES)


_PARTIAL_FV_MAX_CELL_LEN = 14


def is_partial_fv_subheader_row(cells: List[str]) -> bool:
    """列标折行片段：衍生 / 权益工具、 / 债券 等（短标签，非叙述折行）。"""
    norms = [_normalize_header_cell(c) for c in cells if str(c).strip()]
    if not norms or len(norms) > 5:
        return False
    if any(is_numeric_data_cell(n) for n in norms):
        return False
    if any(len(n) > _PARTIAL_FV_MAX_CELL_LEN for n in norms):
        return False
    if any(re.search(r"\d+\.\d+", n) for n in norms):
        return False
    fv_tokens = _FV_COLUMN_MARKERS | {"权益工具、", "权益工具", "债券", "衍生"}
    return all(n in fv_tokens for n in norms)


def is_table_section_lead_row(cells: List[str]) -> bool:
    """表内首行小节：资产 / 负债（非表头带，但标定表体起点）。"""
    non_empty = [str(c).strip() for c in cells if str(c).strip()]
    if len(non_empty) != 1:
        return False
    return _normalize_header_cell(non_empty[0]) in _TABLE_SECTION_LEADS


def _cells_look_like_narrative_prose(cells: List[str]) -> bool:
    """长段叙述折行：值列无表头，不应触发 scope 上扩。"""
    joined = "".join(str(c).strip() for c in cells if str(c).strip())
    if not joined:
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if cn < 14:
        return False
    if re.search(r"\d+\.\d+", joined) and any(
        m in joined for m in ("亿元", "万元", "%", "较上年", "同比", "百分点")
    ):
        return True
    if joined.rstrip().endswith(("。", "；")) and cn >= 10:
        return True
    return False


def _large_gap_above_region(page: PageSource, region: RegionBox, *, gap_pt: float = 45.0) -> bool:
    """当前 region 与上一 table region 之间为大间隙（表间叙述区）。"""
    prev = [r for r in page.table_regions if r.y1 < region.y0 - 5.0]
    if not prev:
        return False
    above = max(prev, key=lambda r: r.y1)
    return (region.y0 - above.y1) > gap_pt


def _scope_y0_expands_for_row(cells: List[str]) -> bool:
    """scope 上扩：年报表头 + pillar 列标，不含单独报告期（避免页眉误扩）。"""
    if _cells_look_like_narrative_prose(cells):
        return False
    if is_entity_scope_header_cells(cells):
        return True
    if is_maturity_column_header_row(cells):
        return True
    if is_table_section_lead_row(cells):
        return True
    if has_letter_column_header_row(cells):
        return True
    if is_rmb_unit_lead_row(cells):
        return True
    if is_period_span_header_row(cells):
        return True
    if is_single_year_label_row(cells):
        return True
    if is_fair_value_column_header_row(cells):
        return True
    if is_wrapped_fv_header_row(cells):
        return True
    if is_partial_fv_subheader_row(cells):
        return True
    if is_wrapped_column_subheader_row(cells):
        return True
    if is_annual_report_column_header_row(cells):
        return True
    if is_annual_report_unit_row(cells):
        return True
    return False


    return False


_ANNUAL_HEADER_WRAP_SUFFIXES = frozenset({"增减", "末增减", "期增减", "比上年同期"})


def is_annual_header_wrap_subrow(row_items: Sequence[SourceItem]) -> bool:
    """年报表头折行次行：增减尾片、月日片等，无数值列金额。"""
    if row_is_footnote_prose_row(row_items):
        return False
    cells = _row_cells(row_items)
    if not cells:
        return False
    if any(is_numeric_data_cell(c) for c in cells):
        return False
    for c in cells:
        if is_report_date_header_part_text(c):
            continue
        if c in _ANNUAL_HEADER_WRAP_SUFFIXES:
            continue
        if "比上年同期" in c or "比上年度" in c:
            continue
        if is_month_day_cell(c):
            continue
        return False
    return True


def row_is_annual_header_wrap_fragment_row(cells: List[str]) -> bool:
    """表内单行是否为年报表头折行碎片（用于禁止剥成表间叙述）。"""
    if not cells:
        return False
    if any(is_numeric_data_cell(c) for c in cells):
        return False
    non_empty = [str(c).strip() for c in cells if str(c).strip()]
    if not non_empty:
        return False
    for c in non_empty:
        if is_report_date_header_part_text(c):
            continue
        if c in _ANNUAL_HEADER_WRAP_SUFFIXES:
            continue
        if "比上年同期" in c or "比上年度" in c:
            continue
        if is_month_day_cell(c):
            continue
        return False
    return True


def partition_annual_header_wrap_items(
    items: Sequence[SourceItem],
) -> tuple[List[SourceItem], List[SourceItem]]:
    """从 item 列表中拆出年报表头折行碎片 → (剩余, 折行表头 item)。"""
    if not items:
        return [], []
    dicts = source_items_to_dicts(list(items))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in items}
    wrap: List[SourceItem] = []
    kept: List[SourceItem] = []
    for row in rows:
        row_items = [
            index_map[d["item_index"]]
            for d in row.get("items", [])
            if d.get("item_index") in index_map
        ]
        if row_items and is_annual_header_wrap_subrow(row_items):
            wrap.extend(row_items)
        else:
            kept.extend(row_items)
    return kept, wrap


def consolidate_annual_pre_header_items(
    pre: Sequence[SourceItem],
) -> List[SourceItem]:
    """折行表头碎片纵并到同 x 主列标 item（允许 y 落在表体之后）。"""
    if len(pre) < 2:
        return list(pre)

    dicts = source_items_to_dicts(list(pre))
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    index_map = {it.item_index: it for it in pre}
    wrap_ids: set[str] = set()

    anchor_items: List[SourceItem] = []
    for row in rows:
        row_items = [
            index_map[d["item_index"]]
            for d in row.get("items", [])
            if d.get("item_index") in index_map
        ]
        if not row_items:
            continue
        if is_annual_header_wrap_subrow(row_items):
            for wit in row_items:
                wtext = str(wit.text or "").strip()
                if not wtext:
                    continue
                wx = float(wit.bbox.x0)
                best: SourceItem | None = None
                best_d = 1e9
                for anchor in anchor_items:
                    at = str(anchor.text or "").strip()
                    if not at:
                        continue
                    if not (
                        "比上年同期" in at
                        or "比上年度" in at
                        or is_report_period_cell(at)
                    ):
                        continue
                    d = abs(float(anchor.bbox.x0) - wx)
                    if d < best_d:
                        best_d = d
                        best = anchor
                if best is not None and best_d <= 45.0:
                    base = str(best.text or "").strip()
                    best.text = f"{base}{wtext}" if base else wtext
                    merged = list(best.merged_from or [])
                    if wit.item_index not in merged:
                        merged.append(wit.item_index)
                    best.merged_from = merged
                    wrap_ids.add(wit.item_index)
            continue
        cells = _row_cells(row_items)
        if is_annual_report_column_header_row(cells) or is_pre_table_header_band_row(row_items):
            anchor_items.extend(row_items)

    if not wrap_ids:
        return list(pre)
    return [it for it in pre if it.item_index not in wrap_ids]


def is_pre_table_header_band_row(row_items: Sequence[SourceItem]) -> bool:
    """表前专检：pillar 表头 + 年报表头（主体/日期/到期日列标）。"""
    if row_is_footnote_prose_row(row_items):
        return False
    cells = _row_cells(row_items)
    if not cells:
        return False
    if is_annual_report_unit_row(cells):
        return True
    if is_pillar_grid_header_band(row_items):
        return True
    if is_entity_scope_header_cells(cells):
        return True
    if is_maturity_column_header_row(cells):
        return True
    if is_period_span_header_row(cells):
        return True
    if is_single_year_label_row(cells):
        return True
    if is_fair_value_column_header_row(cells):
        return True
    if is_wrapped_fv_header_row(cells):
        return True
    if is_partial_fv_subheader_row(cells):
        return True
    if is_wrapped_column_subheader_row(cells):
        return True
    if is_annual_report_column_header_row(cells):
        return True
    if is_annual_header_wrap_subrow(row_items):
        return True
    if row_has_reporting_date(cells):
        return True
    return False


def is_table_header_band_row(cells: List[str]) -> bool:
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    if is_date_only_header_row(cells):
        return True
    if any(c in ("数额", "代码", "占比", "期数", "金额") for c in non_empty):
        return True
    return False


def is_pillar_grid_header_band(row_items: Sequence[SourceItem]) -> bool:
    """间隙块底部是否为披露表表头带（a/b、单位行、报告期）。"""
    if row_is_footnote_prose_row(row_items):
        return False
    cells = _row_cells(row_items)
    if not cells:
        return False
    if row_has_pillar_table_caption(cells):
        return False
    if is_short_section_label_row(cells):
        return False
    if is_wrapped_column_subheader_row(cells):
        return True
    lead = cells[0].strip() if cells[0] else ""
    if lead and _ROW_NUMBER_RE.match(lead) and len(cells) <= 2:
        return False
    if has_letter_column_header_row(cells):
        return True
    if is_rmb_unit_lead_row(cells):
        return True
    if is_annual_report_unit_row(cells):
        return True
    if is_date_only_header_row(cells):
        return True
    if row_has_reporting_date(cells):
        return True
    if is_period_year_header_row(row_items):
        return True
    if is_table_header_band_row(cells):
        return True
    if len(cells) == 1 and cells[0] in ("数额", "代码", "占比", "期数", "金额", "序号"):
        return True
    return False


def _gap_row_is_narrative(cells: List[str]) -> bool:
    joined = "".join(cells)
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if cn >= _NARRATIVE_MIN_CN and ("。" in joined or "下表列" in joined):
        return True
    if cn >= 22 and ("变动情况" in joined or "第三层级" in joined):
        return True
    return False


def _peel_row_range(
    items: List[SourceItem],
    row_groups: List[List[SourceItem]],
    header_start: int,
    header_end: int,
) -> tuple[List[SourceItem], List[SourceItem]]:
    if header_start >= header_end:
        return list(items), []
    peeled_ids: set[str] = set()
    peeled: List[SourceItem] = []
    for i in range(header_start, header_end):
        for it in row_groups[i]:
            peeled.append(it)
            peeled_ids.add(it.item_index)
    remaining = [it for it in items if it.item_index not in peeled_ids]
    _assert_peel_conservation(items, remaining, peeled)
    return remaining, peeled


def _peel_by_entity_header_block(
    items: List[SourceItem],
    row_groups: List[List[SourceItem]],
    row_cells: List[List[str]],
) -> tuple[List[SourceItem], List[SourceItem]] | None:
    """本行/本集团 起至 gap 末（或叙述行前）的表头带。"""
    entity_idx: int | None = None
    for i in range(len(row_groups) - 1, -1, -1):
        if is_entity_scope_header_cells(row_cells[i]):
            entity_idx = i
            break
    if entity_idx is None:
        return None

    header_end = len(row_groups)
    for i in range(entity_idx + 1, len(row_groups)):
        if _gap_row_is_narrative(row_cells[i]):
            header_end = i
            break
    return _peel_row_range(items, row_groups, entity_idx, header_end)


def peel_pre_header_from_items(
    items: List[SourceItem],
) -> tuple[List[SourceItem], List[SourceItem]]:
    """从间隙块剥离表头带 → pre_header。

    年报表：
    - 资产/负债 之上连续向上收表头；
    - 或 本行/本集团 起向下收至 gap 末（公允价值层级表）。
    披露表等无锚点：自底向上逐行剥离。
    """
    if not items:
        return [], []

    dicts = source_items_to_dicts(items)
    rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
    if not rows:
        return list(items), []

    index_map = {it.item_index: it for it in items}

    def _row_items(row_dict: dict) -> List[SourceItem]:
        return [
            index_map[d["item_index"]]
            for d in row_dict.get("items", [])
            if d.get("item_index") in index_map
        ]

    row_groups = [_row_items(r) for r in rows]
    row_cells = [_row_cells(g) for g in row_groups]

    section_idx: int | None = None
    for i in range(len(row_groups) - 1, -1, -1):
        if is_table_section_lead_row(row_cells[i]):
            section_idx = i
            break

    if section_idx is None:
        entity_peel = _peel_by_entity_header_block(items, row_groups, row_cells)
        if entity_peel is not None:
            return entity_peel
        return _peel_pre_header_bottom_up(items)

    return _peel_pre_header_section_anchor(items, row_groups, row_cells, section_idx)


def _peel_pre_header_section_anchor(
    items: List[SourceItem],
    row_groups: List[List[SourceItem]],
    row_cells: List[List[str]],
    section_idx: int,
) -> tuple[List[SourceItem], List[SourceItem]]:
    """资产/负债 锚点：向上连续收表头带。"""
    search_end = section_idx
    header_start = search_end

    for i in range(search_end - 1, -1, -1):
        cells = row_cells[i]
        group = row_groups[i]
        if is_pre_table_header_band_row(group):
            header_start = i
            continue
        joined = "".join(cells)
        cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
        if cn >= _NARRATIVE_MIN_CN and ("。" in joined or "下表列" in joined):
            break
        if is_short_section_label_row(cells) and not row_has_pillar_table_caption(cells):
            break
        break

    if header_start >= search_end:
        entity_peel = _peel_by_entity_header_block(items, row_groups, row_cells)
        if entity_peel is not None:
            return entity_peel
        return list(items), []

    return _peel_row_range(items, row_groups, header_start, search_end)


def _assert_peel_conservation(
    original: Sequence[SourceItem],
    remaining: Sequence[SourceItem],
    peeled: Sequence[SourceItem],
) -> None:
    """peel 只做转移：original == remaining ∪ peeled。"""
    orig_ids = {it.item_index for it in original}
    out_ids = {it.item_index for it in remaining} | {it.item_index for it in peeled}
    if orig_ids != out_ids:
        missing = orig_ids - out_ids
        raise ValueError(f"peel_pre_header lost {len(missing)} source items")


def _peel_pre_header_bottom_up(
    items: List[SourceItem],
) -> tuple[List[SourceItem], List[SourceItem]]:
    """自底向上逐行剥离表头（pillar / 无资产·负债锚点）。"""
    remaining = list(items)
    peeled: List[SourceItem] = []

    while remaining:
        dicts = source_items_to_dicts(remaining)
        rows = cluster_items_by_y(dicts, use_dynamic_threshold=True)
        if not rows:
            break
        index_map = {it.item_index: it for it in remaining}
        row_dicts = rows[-1].get("items", [])
        row_items = [
            index_map[d["item_index"]]
            for d in row_dicts
            if d.get("item_index") in index_map
        ]
        if not row_items:
            break
        cells = _row_cells(row_items)
        if is_pillar_grid_header_band(row_items):
            peeled = row_items + peeled
            peel_ids = {it.item_index for it in row_items}
            remaining = [it for it in remaining if it.item_index not in peel_ids]
            continue
        if is_wrapped_column_subheader_row(cells):
            peeled = row_items + peeled
            peel_ids = {it.item_index for it in row_items}
            remaining = [it for it in remaining if it.item_index not in peel_ids]
            continue
        if is_annual_header_wrap_subrow(row_items):
            peeled = row_items + peeled
            peel_ids = {it.item_index for it in row_items}
            remaining = [it for it in remaining if it.item_index not in peel_ids]
            continue
        if cells and is_short_section_label_row(cells):
            if row_has_pillar_table_caption(cells):
                break
            # 短节标题留给 remaining → description / TEXT，不从此处剔除
            break
        break

    _assert_peel_conservation(items, remaining, peeled)
    return remaining, peeled


def header_band_present_in_rows(rows: List[List[SourceItem]], scan: int = 8) -> bool:
    for row in rows[:scan]:
        if is_pre_table_header_band_row(row):
            return True
    return False
