# -*- coding: utf-8 -*-
"""liteparse / mid_cache 读写桥接（只读加载，写回走独立缓存文件）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_tables_from_mid(pdf_path: str) -> Tuple[List[dict], dict]:
    """从 mid_cache data.json 加载 tables。

    Returns:
        (tables, full_payload)
    """
    from codes.pdf_extractor.utils import load_mid_data

    payload = load_mid_data(pdf_path)
    if not payload:
        return [], {}
    tables = payload.get("tables") or []
    return tables, payload


def load_liteparse_dict(pdf_path: str) -> Optional[dict]:
    try:
        from codes.liteparse_extractor.cache_manager import load_parse_result

        result = load_parse_result(pdf_path)
        return result.to_dict() if result else None
    except Exception:
        return None


def get_liteparse_page(liteparse_data: Optional[dict], page_num: int) -> Optional[dict]:
    if not liteparse_data:
        return None
    for p in liteparse_data.get("pages") or []:
        if p.get("page_number") == page_num:
            return p
    return None


# 后页页眉区：仅此高度内的文字才可能是页眉（公司名/年报标题/页码）
_PAGE_HEADER_Y_MAX = 85.0


def page_gap_text(
    liteparse_data: Optional[dict],
    page_a: int,
    page_b: int,
    table_a: Optional[dict] = None,
    table_b: Optional[dict] = None,
) -> str:
    """取两表之间可能阻断合并的文本。

    - 同页：用 table_a.y1 ~ table_b.y0 之间的 text_items
    - 跨页：后页表上方 text_items（含页眉与表前说明）
    """
    if page_a == page_b:
        lp = get_liteparse_page(liteparse_data, page_a)
        if not lp:
            return (table_b or {}).get("context_text") or ""
        y0 = float((table_a or {}).get("y1") or 0)
        y1 = float((table_b or {}).get("y0") or 1e9)
        chunks = []
        for it in lp.get("text_items") or []:
            ty = float(it.get("y0", it.get("top", 0)) or 0)
            if y0 < ty < y1:
                t = (it.get("text") or "").strip()
                if t:
                    chunks.append(t)
        return "\n".join(chunks)

    # 跨页：收集后页、后表上方的全部文本（再由候选逻辑区分页眉 vs 表前说明）
    lp_b = get_liteparse_page(liteparse_data, page_b)
    if lp_b:
        y_hi = float((table_b or {}).get("y0") or 1e9)
        chunks = []
        for it in lp_b.get("text_items") or []:
            ty = float(it.get("y0", it.get("top", 0)) or 0)
            if ty >= y_hi:
                continue
            t = (it.get("text") or "").strip()
            if t:
                chunks.append(t)
        if chunks:
            return "\n".join(chunks)
        regions = lp_b.get("table_regions") or []
        full = lp_b.get("full_text") or ""
        if regions:
            rt = (regions[0].get("region_text") or "").strip()
            if rt and rt in full:
                return full.split(rt, 1)[0].strip()
    return (table_b or {}).get("context_text") or ""


def cross_page_gap_zones(
    liteparse_data: Optional[dict],
    page_b: int,
    table_b: Optional[dict] = None,
    *,
    page_header_y_max: float = _PAGE_HEADER_Y_MAX,
) -> Tuple[List[str], List[str]]:
    """跨页时拆分后页表上方文本：页首页眉 vs 表前说明（非页眉）。

    Returns:
        (page_header_lines, pre_table_lines)
        仅 y < page_header_y_max 的视为可能页眉；其余为后表说明/小节，不可当页眉吞并。
    """
    lp_b = get_liteparse_page(liteparse_data, page_b)
    if not lp_b:
        return [], []
    y_hi = float((table_b or {}).get("y0") or 1e9)
    header_lines: List[str] = []
    pre_table: List[str] = []
    for it in lp_b.get("text_items") or []:
        ty = float(it.get("y0", it.get("top", 0)) or 0)
        if ty >= y_hi:
            continue
        t = (it.get("text") or "").strip()
        if not t:
            continue
        if ty < page_header_y_max:
            header_lines.append(t)
        else:
            pre_table.append(t)
    return header_lines, pre_table


def meaningful_text_lines(text: str) -> List[str]:
    import re

    lines = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^[\d\-\s./]+$", s):
            continue
        # 页码类
        if re.match(r"^第?\s*\d+\s*页$", s):
            continue
        lines.append(s)
    return lines


def region_text_for_table(
    liteparse_data: Optional[dict],
    table: dict,
) -> str:
    """尽量匹配同页 region 文本。"""
    page = table.get("page", 0)
    lp = get_liteparse_page(liteparse_data, page)
    if not lp:
        return ""
    regions = lp.get("table_regions") or []
    if not regions:
        return lp.get("full_text") or ""
    if len(regions) == 1:
        return regions[0].get("region_text") or ""

    # 多 region：用标签交集粗匹配
    data = table.get("data") or []
    labels = []
    for row in data[:5]:
        for cell in (row or [])[:2]:
            s = str(cell or "").strip()
            if s and len(s) <= 40:
                labels.append(s)
    best, best_score = "", -1
    for reg in regions:
        rt = reg.get("region_text") or ""
        score = sum(1 for lb in labels if lb and lb in rt)
        if score > best_score:
            best_score = score
            best = rt
    return best


def correction_cache_path(pdf_path: str) -> Path:
    from codes.pdf_extractor.utils import get_pdf_cache_dir

    return get_pdf_cache_dir(pdf_path) / "format_correction.json"


def save_report(pdf_path: str, report_dict: dict) -> Path:
    path = correction_cache_path(pdf_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, ensure_ascii=False, indent=2)
    return path


def load_report(pdf_path: str) -> Optional[dict]:
    path = correction_cache_path(pdf_path)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
