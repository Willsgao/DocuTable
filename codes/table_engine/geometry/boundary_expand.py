# -*- coding: utf-8
"""表界向下扩展（liteparse region 过窄时）。"""

from __future__ import annotations

import re
from typing import List, Optional

from codes.table_engine.geometry.numeric import is_numeric_data_cell
from codes.table_engine.models import PageSource, RegionBox, SourceItem
from codes.table_engine.scope.header_scope import row_has_pillar_table_caption

_Y_CLUSTER_TOL = 6.0
_TABLE_TAIL_MAX_EXTRA = 42.0
_NARRATIVE_MIN_CN = 16
_SUBSECTION_CAPTION_RE = re.compile(r"^[（(][一二三四五六七八九十\d]+[)）]")
_NUMBERED_SECTION_RE = re.compile(r"^\d+[\.．、]\s*[\u4e00-\u9fff]")


def _next_overlapping_region_y0(
    page: PageSource,
    region: RegionBox,
    *,
    x_margin: float = 10.0,
) -> Optional[float]:
    """同页、水平重叠且位于当前 region 下方的下一 table region 顶边。"""
    x_lo, x_hi = region.x0 - x_margin, region.x1 + x_margin
    below: List[float] = []
    for other in page.table_regions:
        if other.y0 <= region.y0 + 5.0:
            continue
        if other.y0 < region.y1 - 10.0:
            continue
        if other.x1 < x_lo or other.x0 > x_hi:
            continue
        below.append(other.y0)
    return min(below) if below else None


_LARGE_REGION_GAP = 45.0
_CLIP_MARGIN_BELOW = 8.0
_LAST_REGION_MAX_EXPAND = 120.0


def expand_y_limit(
    page: PageSource,
    region: RegionBox,
    *,
    max_expand_below: float = 650.0,
    region_gap_margin: float = 15.0,
) -> float:
    """矮 region 向下扩展的上界 Y（不吞邻表间隙内的脚注/叙述）。"""
    y_limit = region.y1 + max_expand_below
    next_region_y0 = _next_overlapping_region_y0(page, region)
    if next_region_y0 is not None:
        gap = next_region_y0 - region.y1
        if gap > _LARGE_REGION_GAP:
            y_limit = region.y1 + _CLIP_MARGIN_BELOW
        else:
            y_limit = min(y_limit, next_region_y0 - region_gap_margin)
    else:
        y_limit = min(y_limit, region.y1 + _LAST_REGION_MAX_EXPAND)
    if page.page_height:
        y_limit = min(y_limit, page.page_height - 25.0)
    return y_limit


def effective_y_margin_below(page: PageSource, region: RegionBox) -> float:
    """邻表间隙收紧 band 下沿：大间隙防脚注；小间隙禁止越过下一 region。"""
    next_region_y0 = _next_overlapping_region_y0(page, region)
    if next_region_y0 is None:
        return _CLIP_MARGIN_BELOW
    gap = next_region_y0 - region.y1
    if gap <= 0:
        return 0.0
    if gap > _LARGE_REGION_GAP:
        return _CLIP_MARGIN_BELOW
    if gap > 20.0:
        return min(12.0, max(0.0, gap - 2.0))
    # 紧邻下表（如 VaR 2025/2024 仅数 pt）：不得用 30pt margin 吞下表列头
    return max(0.0, gap - 2.0)


def large_gap_above_region(page: PageSource, region: RegionBox) -> bool:
    return _large_gap_above_region(page, region)


def _large_gap_above_region(page: PageSource, region: RegionBox) -> bool:
    prev = [r for r in page.table_regions if r.y1 < region.y0 - 5.0]
    if not prev:
        return False
    above = max(prev, key=lambda r: r.y1)
    return (region.y0 - above.y1) > _LARGE_REGION_GAP


def expand_scope_items(
    page: PageSource,
    region: RegionBox,
    scoped: List[SourceItem],
    *,
    min_region_height: float = 200.0,
    max_expand_below: float = 650.0,
    region_gap_margin: float = 15.0,
) -> List[SourceItem]:
    """region bbox 过矮时，按 X 带向下扩展至表体结束。"""
    if region.y1 - region.y0 >= min_region_height:
        return scoped

    if not scoped:
        return scoped

    y_top = min(it.bbox.y0 for it in scoped)
    if _large_gap_above_region(page, region):
        y_top = max(y_top, region.y0 - _CLIP_MARGIN_BELOW)

    margin_below = effective_y_margin_below(page, region)
    y_limit = expand_y_limit(
        page,
        region,
        max_expand_below=max_expand_below,
        region_gap_margin=region_gap_margin,
    )
    if _next_overlapping_region_y0(page, region) is None:
        y_limit = min(y_limit, region.y1 + margin_below + _TABLE_TAIL_MAX_EXTRA)

    x_lo, x_hi = region.x0 - 10, region.x1 + 10

    candidates: List[SourceItem] = []
    for it in page.items:
        if not (x_lo <= it.bbox.cx <= x_hi):
            continue
        if it.bbox.y0 < y_top - 15:
            continue
        if it.bbox.y0 > y_limit:
            continue
        candidates.append(it)

    seen = {it.item_index for it in scoped}
    out: List[SourceItem] = list(scoped)
    for row_items in _cluster_items_by_y(candidates):
        row_y0 = min(it.bbox.y0 for it in row_items)
        new_items = [it for it in row_items if it.item_index not in seen]
        if not new_items:
            continue
        if row_y0 > region.y1 + 2:
            if _row_is_narrative_items(row_items):
                break
            if not _row_is_table_continuation_items(row_items):
                break
        for it in new_items:
            seen.add(it.item_index)
            out.append(it)

    out.sort(key=lambda x: (x.y_mid, x.x0))
    return out


def _cluster_items_by_y(items: List[SourceItem], tol: float = _Y_CLUSTER_TOL) -> List[List[SourceItem]]:
    if not items:
        return []
    ordered = sorted(items, key=lambda it: it.bbox.cy)
    clusters: List[List[SourceItem]] = [[ordered[0]]]
    for it in ordered[1:]:
        if it.bbox.cy - clusters[-1][-1].bbox.cy <= tol:
            clusters[-1].append(it)
        else:
            clusters.append([it])
    return clusters


def _row_is_section_break_items(items: List[SourceItem]) -> bool:
    """小节标题/编号段首（（六）…、1.手续费…）→ 非表体续行。"""
    cells = [str(it.text).strip() for it in items if str(it.text).strip()]
    if not cells:
        return False
    lead = cells[0]
    if _SUBSECTION_CAPTION_RE.match(lead):
        return True
    if _NUMBERED_SECTION_RE.match(lead):
        return True
    joined = "".join(cells)
    if _SUBSECTION_CAPTION_RE.search(joined) and not any(
        is_numeric_data_cell(c) for c in cells[1:]
    ):
        return True
    return False


def _row_is_narrative_items(items: List[SourceItem]) -> bool:
    if not items:
        return False
    joined = "".join(it.text.strip() for it in items if it.text.strip())
    cn = len(re.findall(r"[\u4e00-\u9fff]", joined))
    if cn >= _NARRATIVE_MIN_CN and any(it.bbox.width > 320 for it in items):
        return True
    if re.match(r"^(?:19|20)\d{2}年", joined) and cn >= 12 and (
        "。" in joined or "亿元" in joined or "%" in joined
    ):
        return True
    if cn >= 14 and re.search(r"\d+\.\d+", joined) and any(
        m in joined for m in ("亿元", "万元", "%", "较上年", "同比", "百分点", "占比为")
    ):
        return True
    return False


def _row_is_table_continuation_items(items: List[SourceItem]) -> bool:
    """region 下沿外的表尾续行（标签 + 数值列）。"""
    if not items or _row_is_narrative_items(items):
        return False
    if _row_is_section_break_items(items):
        return False
    cells = [str(it.text).strip() for it in items if str(it.text).strip()]
    if row_has_pillar_table_caption(cells):
        return False
    if any("下表列出" in c or "下表列示" in c for c in cells):
        return False
    has_value = any(
        is_numeric_data_cell(it.text.strip())
        and it.bbox.x0 > 160
        for it in items
        if it.text.strip()
    )
    has_short_label = any(
        it.bbox.x0 < 200
        and re.search(r"[\u4e00-\u9fff]", it.text)
        and len(it.text.strip()) <= 24
        for it in items
        if it.text.strip()
    )
    return has_value or has_short_label


_REGION_GAP_MARGIN = 12.0


def append_table_continuation_below(
    page: PageSource,
    region: RegionBox,
    scoped: List[SourceItem],
    margin_below: float,
    *,
    x_margin: float = 10.0,
    max_extra_below: float = _TABLE_TAIL_MAX_EXTRA,
) -> List[SourceItem]:
    """region bbox 略短于表尾时：在叙述段之前向下收录续行（如净利息收益率）。"""
    next_region_y0 = _next_overlapping_region_y0(page, region)
    if next_region_y0 is not None:
        gap = next_region_y0 - region.y1
        if gap <= _LARGE_REGION_GAP:
            return scoped

    y_floor = region.y1 + margin_below
    if next_region_y0 is not None and (next_region_y0 - region.y1) > _LARGE_REGION_GAP:
        y_ceiling = next_region_y0 - _REGION_GAP_MARGIN
    else:
        y_ceiling = region.y1 + margin_below + max_extra_below
    x_lo, x_hi = region.x0 - x_margin, region.x1 + x_margin
    seen = {it.item_index for it in scoped}

    candidates = [
        it
        for it in page.items
        if it.item_index not in seen
        and x_lo <= it.bbox.cx <= x_hi
        and y_floor < it.bbox.cy <= y_ceiling
    ]
    if not candidates:
        return scoped

    extra: List[SourceItem] = []
    for row_items in _cluster_items_by_y(candidates):
        if _row_is_narrative_items(row_items):
            break
        if _row_is_section_break_items(row_items):
            break
        if _row_is_table_continuation_items(row_items):
            extra.extend(row_items)
        else:
            break

    if not extra:
        return scoped
    return scoped + extra
