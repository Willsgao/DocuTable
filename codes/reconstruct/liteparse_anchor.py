# -*- coding: utf-8 -*-
"""把 liteparse 页内 text_items 锚定到单表，供还原主链使用。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _page_of(table: Dict[str, Any]) -> Optional[int]:
    for key in ("page", "page_num", "page_number"):
        v = table.get(key)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def _table_bbox(table: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """尽量取表区域；没有则 None（退化为整页字）。"""
    for key in ("bbox", "table_bbox", "rect"):
        b = table.get(key)
        if isinstance(b, (list, tuple)) and len(b) >= 4:
            try:
                return float(b[0]), float(b[1]), float(b[2]), float(b[3])
            except (TypeError, ValueError):
                pass
    # 常见：pdf2docx / mid_cache 直接挂 x0,y0,x1,y1
    if all(k in table for k in ("x0", "y0", "x1", "y1")):
        try:
            return (
                float(table["x0"]),
                float(table["y0"]),
                float(table["x1"]),
                float(table["y1"]),
            )
        except (TypeError, ValueError):
            pass
    return None


def _region_bbox_from_liteparse_page(
    page: Dict[str, Any],
    table_bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Optional[Tuple[float, float, float, float]]:
    """从 liteparse table_regions 取与表最相关的区域。"""
    regions = page.get("table_regions") or []
    best = None
    best_score = -1.0
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        try:
            rb = (
                float(reg["x0"]),
                float(reg["y0"]),
                float(reg["x1"]),
                float(reg["y1"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if table_bbox is None:
            area = max(0.0, rb[2] - rb[0]) * max(0.0, rb[3] - rb[1])
            if area > best_score:
                best_score = area
                best = rb
            continue
        ax0, ay0, ax1, ay1 = table_bbox
        bx0, by0, bx1, by1 = rb
        ix0, iy0 = max(ax0, bx0), max(ay0, by0)
        ix1, iy1 = min(ax1, bx1), min(ay1, by1)
        inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        if inter > best_score:
            best_score = inter
            best = rb
    return best


def _item_xyxy(item: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(item, dict):
        return None
    if all(k in item for k in ("x0", "y0", "x1", "y1")):
        try:
            return float(item["x0"]), float(item["y0"]), float(item["x1"]), float(item["y1"])
        except (TypeError, ValueError):
            return None
    if "bbox" in item and isinstance(item["bbox"], (list, tuple)) and len(item["bbox"]) >= 4:
        b = item["bbox"]
        try:
            return float(b[0]), float(b[1]), float(b[2]), float(b[3])
        except (TypeError, ValueError):
            return None
    # liteparse 原始：x,y,width,height
    if "x" in item and "y" in item:
        try:
            x = float(item.get("x") or 0)
            y = float(item.get("y") or 0)
            w = float(item.get("width") or 0)
            h = float(item.get("height") or 0)
            return x, y, x + w, y + h
        except (TypeError, ValueError):
            return None
    return None


def _overlaps(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
    *,
    pad: float = 2.0,
) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (
        ax1 + pad < bx0 or bx1 + pad < ax0 or ay1 + pad < by0 or by1 + pad < ay0
    )


def _is_page_or_section_title_above(text: str) -> bool:
    """上方探视时排除章节大标题/页眉，避免把「会计数据和财务指标摘要」吞进表。"""
    t = str(text or "").strip()
    if not t:
        return True
    if len(t) > 24:
        return True
    if t.startswith(("第", "附件", "目录", "章节")):
        return True
    # 长左对齐章节名
    if len(t) >= 10 and any(
        k in t for k in ("会计数据", "财务指标摘要", "重要提示", "释义", "公司简介")
    ):
        return True
    # 公司名/年报标题等 running header（短文案也会被 len<=16 误当表头续行）
    try:
        from codes.table_engine.scope.page_chrome import text_looks_like_page_chrome

        if text_looks_like_page_chrome(t, role_hint="page_header"):
            return True
    except Exception:
        if any(k in t for k in ("股份有限公司", "有限公司", "年度报告", "半年度报告")):
            return True
    return False


def _looks_like_header_continuation(text: str) -> bool:
    """紧挨年列表头上方的短续行（本年比上年 / 单位说明等）。"""
    t = str(text or "").strip()
    if not t or _is_page_or_section_title_above(t):
        return False
    # 上一表表体（合计/金额）绝不能当本表表头续行探视上去
    if t in ("合计", "小计", "总计", "净额", "其中"):
        return False
    compact = t.replace(",", "").replace("，", "").replace(" ", "")
    if re.match(r"^-?\(?\d+(\.\d+)?\)?%?$", compact):
        return False
    if len(t) <= 16:
        return True
    # 单位行略长仍属表头
    if any(k in t for k in ("人民币", "百万元", "千元", "除外", "单位")):
        return len(t) <= 28
    return False


def _word_is_page_chrome_header(text: str, y0: float, *, page_height: float) -> bool:
    """页眉带内的 running header 字框，不应进入表 _source_words。"""
    from codes.table_engine.scope.page_chrome import (
        header_band_y1,
        text_looks_like_page_chrome,
    )

    if float(y0) > header_band_y1(page_height) + 10.0:
        return False
    t = str(text or "").strip()
    if not t:
        return False
    # 表内金额整数/小数后缀（含被 PDF 拆开的「75」「.21」）不是页眉
    if re.match(r"^\d{1,6}$", t) or re.match(r"^\.\d+\D?$", t):
        return False
    if text_looks_like_page_chrome(t, role_hint="page_header"):
        return True
    # 章节碎片（单独无「股份有限公司」时 text_looks_like_page_chrome 可能漏）
    if t.startswith(("第", "附件", "目录", "章节")) and len(t) <= 40:
        return True
    if any(k in t for k in ("会计数据", "财务指标摘要", "重要提示", "释义")):
        return True
    return False


def _expand_bbox_peek_header_above(
    raw_items: Sequence[Any],
    use_bbox: Tuple[float, float, float, float],
    *,
    max_up: float = 42.0,
    max_gap: float = 20.0,
) -> Tuple[Tuple[float, float, float, float], List[str]]:
    """表头上沿向上探视紧邻短行，扩 y0；不越过 max_up。

    典型：region 从「2025年」起，上方「本年比上年」仍应纳入。
    """
    x0, y0, x1, y1 = use_bbox
    picked_y0: List[float] = []
    picked_texts: List[str] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text or not _looks_like_header_continuation(text):
            continue
        xy = _item_xyxy(it)
        if xy is None:
            continue
        ix0, iy0, ix1, iy1 = xy
        # 必须在当前上沿之上，且底边贴近上沿（紧邻，非远处章节题）
        if iy1 > y0 + 1.0:
            continue
        if y0 - iy0 > max_up:
            continue
        if y0 - iy1 > max_gap:
            continue
        # 与表水平范围有交集（压在列区上方）
        if ix1 < x0 - 4.0 or ix0 > x1 + 4.0:
            continue
        picked_y0.append(iy0)
        picked_texts.append(text)
    if not picked_y0:
        return use_bbox, []
    new_y0 = min(picked_y0)
    if new_y0 >= y0 - 0.5:
        return use_bbox, []
    return (x0, new_y0, x1, y1), picked_texts


def get_liteparse_page(
    liteparse_data: Optional[Dict[str, Any]],
    page_num: Any,
) -> Optional[Dict[str, Any]]:
    if not liteparse_data or page_num is None:
        return None
    try:
        want = int(page_num)
    except (TypeError, ValueError):
        return None
    for p in liteparse_data.get("pages") or []:
        if not isinstance(p, dict):
            continue
        pn = p.get("page_number", p.get("page", p.get("page_num")))
        try:
            if int(pn) == want:
                return p
        except (TypeError, ValueError):
            continue
    return None


def attach_liteparse_words(
    table: Dict[str, Any],
    liteparse_data: Optional[Dict[str, Any]] = None,
    *,
    liteparse_page: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写入 table['_source_words']：本表区域（或整页）的 liteparse 字框。

    返回附着摘要 dict。
    """
    page = liteparse_page
    if page is None and liteparse_data is not None:
        page = get_liteparse_page(liteparse_data, _page_of(table))

    summary: Dict[str, Any] = {
        "anchored": False,
        "word_count": 0,
        "page": _page_of(table),
        "used_table_bbox": False,
    }
    # 结构拆分预先分配的字框（attach 会重写 _source_words，先备份）
    pre_split_words: List[Dict[str, Any]] = []
    if table.get("_format_structure_split"):
        pre_split_words = [
            w for w in (table.get("_source_words") or [])
            if isinstance(w, dict)
        ]

    if not page:
        table["_source_words"] = list(table.get("_source_words") or [])
        summary["reason"] = "no_liteparse_page"
        table["_liteparse_anchor"] = summary
        return summary

    raw_items = page.get("text_items") or []
    bbox = _table_bbox(table)
    region_bbox = _region_bbox_from_liteparse_page(page, bbox)
    # 优先：表坐标 ∩ liteparse 表区（去掉页眉叙述）；否则表坐标；再否则表区
    use_bbox = None
    bbox_source = "none"
    if bbox is not None and region_bbox is not None:
        ax0, ay0, ax1, ay1 = bbox
        bx0, by0, bx1, by1 = region_bbox
        ix0, ix1 = max(ax0, bx0), min(ax1, bx1)
        # 表 bbox 上沿若高于 region（多行表头如「本年比上年」），保留表上沿，
        # 避免被 region 裁掉后凝结核永远看不到该行。
        top_kept = False
        if ay0 < by0 - 0.5:
            iy0 = ay0
            top_kept = True
        else:
            iy0 = max(ay0, by0)
        # 表 bbox 下沿若低于 region（liteparse 区漏掉合计/小计末行），保留表下沿，
        # 否则字框缺失 → 网格写回会吃掉「利息支出变动」等尾行。
        bottom_kept = False
        if ay1 > by1 + 0.5:
            iy1 = ay1
            bottom_kept = True
        else:
            iy1 = min(ay1, by1)
        if top_kept and bottom_kept:
            bbox_source = "table∩region+table_top+table_bottom"
        elif top_kept:
            bbox_source = "table∩region+table_top"
        elif bottom_kept:
            bbox_source = "table∩region+table_bottom"
        else:
            bbox_source = "table∩region"
        if ix1 > ix0 and iy1 > iy0:
            use_bbox = (ix0, iy0, ix1, iy1)
        else:
            use_bbox = bbox
            bbox_source = "table_xy"
    elif bbox is not None:
        use_bbox = bbox
        bbox_source = "table_xy"
    elif region_bbox is not None:
        use_bbox = region_bbox
        bbox_source = "liteparse_region"

    if use_bbox is not None:
        use_bbox, peeked = _expand_bbox_peek_header_above(raw_items, use_bbox)
        if peeked:
            bbox_source = f"{bbox_source}+peek_above"
            summary["peek_header_above"] = peeked[:8]

    try:
        page_height = float(
            page.get("height")
            or page.get("page_height")
            or (page.get("page_size") or [0, 842])[1]
            or 842.0
        )
    except (TypeError, ValueError, IndexError):
        page_height = 842.0

    words: List[Dict[str, Any]] = []
    chrome_skipped = 0
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        xy = _item_xyxy(it)
        if xy is None:
            continue
        if use_bbox is not None and not _overlaps(xy, use_bbox):
            continue
        if _word_is_page_chrome_header(text, xy[1], page_height=page_height):
            chrome_skipped += 1
            continue
        words.append({
            "text": text,
            "x0": xy[0],
            "y0": xy[1],
            "x1": xy[2],
            "y1": xy[3],
        })

    if use_bbox is not None:
        summary["used_table_bbox"] = True
        summary["bbox_source"] = bbox_source
        summary["bbox"] = [round(v, 2) for v in use_bbox]
    # 无命中时退整页
    if not words and use_bbox is not None:
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            text = str(it.get("text") or "").strip()
            xy = _item_xyxy(it)
            if not text or xy is None:
                continue
            if _word_is_page_chrome_header(text, xy[1], page_height=page_height):
                chrome_skipped += 1
                continue
            words.append({
                "text": text,
                "x0": xy[0], "y0": xy[1], "x1": xy[2], "y1": xy[3],
            })
        summary["used_table_bbox"] = False
        summary["fallback"] = "full_page"

    if chrome_skipped:
        summary["page_chrome_words_skipped"] = chrome_skipped

    table["_source_words"] = words
    summary["anchored"] = True
    summary["word_count"] = len(words)

    # 结构拆分已为各段分配字框：按预分配 y 跨度裁剪，防止大区域把前表字框塞进后段
    if table.get("_format_structure_split") and pre_split_words:
        span = table.get("_source_word_y_span")
        y0 = y1 = None
        if isinstance(span, (list, tuple)) and len(span) >= 2:
            try:
                y0, y1 = float(span[0]), float(span[1])
            except (TypeError, ValueError):
                y0 = y1 = None
        if y0 is None:
            try:
                y0 = min(float(w.get("y0") or 0) for w in pre_split_words)
                y1 = max(float(w.get("y1") or 0) for w in pre_split_words)
            except (TypeError, ValueError):
                y0 = y1 = None
        if y0 is not None and y1 is not None and words:
            clipped = [
                w for w in words
                if isinstance(w, dict)
                and (y0 - 3.0) <= float(w.get("y0") or 0) <= (y1 + 3.0)
            ]
            if len(clipped) >= 2:
                table["_source_words"] = clipped
                summary["word_count"] = len(clipped)
                summary["structure_split_yclip"] = True
            else:
                # 裁空则保留拆分字框，勿用大区域整表字框
                table["_source_words"] = list(pre_split_words)
                summary["word_count"] = len(pre_split_words)
                summary["structure_split_keep_pre"] = True
        else:
            table["_source_words"] = list(pre_split_words)
            summary["word_count"] = len(pre_split_words)
            summary["structure_split_keep_pre"] = True

    table["_liteparse_anchor"] = summary
    return summary
