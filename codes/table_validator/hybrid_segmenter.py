"""
混合表格分割器：Liteparse 边界主导 + pdf2docx 后置格式化

核心理念：分割归分割，格式化归格式化
  - Phase 1:   Liteparse table_regions → 粗粒度边界检测（Y 范围 + 标题）
  - Phase 1.5: 间隙文本捕获 → 表格间文本按关键词+Y距离挂载为 description_text/notes
               或恢复为遗漏表格，或保留为独立文本段落
  - Phase 2:   Liteparse text_items X/Y 聚类 → 行列结构重建（不依赖 pdf2docx）
  - Phase 2.5: 行级结构分裂 → 防止 boundary 内混入多张不同表格
  - Phase 4:   跨页拼接 → 续表识别与合并

优势：
  1. 边界判断简单：只需 Y 间隙 + 段落检测 + 新表头检测
  2. 列结构干净：X 坐标聚类天然形成列边界，无 gridSpan 展开幽灵列
  3. 不会产生粘合数据：每个 boundary 独立分割，互不污染
  4. pdf2docx 数据保留供后续格式化阶段校对列对齐 + 合并单元格恢复
  5. 间隙文本不丢失：表格间的说明/脚注/遗漏表格全部捕获
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from codes.liteparse_extractor.config import LITEPARSE_CONFIG

# 复用核心工具
from codes.table_validator.cell_differ import (
    _cluster_items_by_y,
    _normalize_for_search,
    _build_row_dict,
)
from codes.table_validator.liteparse_table_segmenter import (
    _build_items,
    _is_numeric_cell,
    _has_complete_table_header,
    _capture_caption,
    _extract_caption,
    _classify_table_quality,
    _compute_financial_confidence,
    _get_log,
    _normalize_rows_to_columns,
    decide_table_acceptance,
    ENTITY_KW,
)

from codes.table_validator.liteparse_table_segmenter import (
    _enhance_with_caption_boundary,
)
from codes.table_validator.header_boundary import (
    is_date_only_header_block,
    is_date_only_header_row_items,
    strip_tail_annotation_rows_from_data,
    compact_table_spacer_rows_and_columns,
    _ensure_y_mid,
    attach_stripped_footnotes,
    pop_footnote_bundle,
    assign_footnote_bundle,
    clear_footnote_bundle,
    footnote_text_key,
)
from codes.table_validator.table_content_splitter import (
    normalize_table_header_columns,
    split_mixed_table_entries,
)


# ================================================================
# Phase 1: Liteparse 边界检测
# ================================================================


def detect_table_boundaries_from_liteparse(
    liteparse_data: dict,
    region_confidence_threshold: float = 0.3,
) -> List[dict]:
    """从 liteparse regions 检测逻辑表格边界（仅 Y 范围 + 元数据）。

    不构建行列数据，只输出边界信息供 pdf2docx 融合使用。

    Returns:
        [{page, y0, y1, x0, x1, caption, caption_info, source_regions}]
    """
    pages = liteparse_data.get("pages", [])
    if not pages:
        return []

    boundaries = []

    for lp_page in pages:
        page_num = lp_page.get("page_number", 0)
        regions = lp_page.get("table_regions", [])
        text_items_raw = lp_page.get("text_items", [])

        if not regions:
            continue

        items = _build_items(text_items_raw)
        median_row_h = _estimate_median_row_height(items)

        valid_regions = [
            r for r in regions
            if r.get("confidence", 0) >= region_confidence_threshold
        ]
        valid_regions.sort(key=lambda r: r.get("y0", 0))

        if not valid_regions:
            continue

        # ── Region 邻近合并：将同一表格的碎片 region 合并为逻辑 boundary ──
        # 基于 4 条规则：①间隙无段落文本 ②后区无独立表头 ③Jaccard 相似度 ④结构兼容
        merged_regions = _merge_regions_by_proximity(
            valid_regions, items, page_num, median_row_h
        )
        for mr in merged_regions:
            context_text = mr.get("context_text", "").strip()
            has_range = any(
                kw in context_text for kw in [
                    "截至", "单位：", "万元", "千元", "百万元",
                    "财务数据", "资产负债表", "利润表", "现金流量",
                ]
            )
            boundaries.append({
                "page": page_num,
                "y0": mr.get("y0", 0),
                "y1": mr.get("y1", 0),
                "x0": mr.get("x0", 0),
                "x1": mr.get("x1", 0),
                "caption": context_text,
                "caption_info": {
                    "text": context_text,
                    "has_table_range_info": has_range,
                    "can_be_boundary": has_range,
                },
                "source_regions": mr.get("source_regions", []),
            })

    boundaries.sort(key=lambda b: (b.get("page", 0), b.get("y0", 0)))
    return boundaries


def _estimate_median_row_height(items: List[dict]) -> float:
    """从 liteparse text_items 估算中位行高。"""
    if len(items) < 4:
        return 12.0

    y_mids = sorted(
        it.get("y_mid", (it.get("y0", 0) + it.get("y1", 0)) / 2)
        for it in items
    )

    gaps = []
    for i in range(1, len(y_mids)):
        gap = y_mids[i] - y_mids[i - 1]
        if 4.0 < gap < 40.0:
            gaps.append(gap)

    if not gaps:
        return 12.0

    gaps.sort()
    mid = len(gaps) // 2
    return gaps[mid] if len(gaps) % 2 == 1 else (gaps[mid - 1] + gaps[mid]) / 2


def _merge_regions_by_proximity(
    regions: List[dict],
    all_page_items: List[dict],
    page_num: int,
    median_row_h: float,
) -> List[dict]:
    """按 Y 邻近度合并相邻 regions（纯空间规则）。

    判定规则：
    1. gap ≤ 3×行高 → 合并
    2. 间隙中有段落文本 → 拆分
    3. 间隙大 + 下方 region 有新表头 → 拆分
    4. 否则 → 合并（可能是行间距较大的续表）
    """
    if not regions:
        return []

    MERGE_RATIO = 3.0
    MIN_MERGE = 18.0
    MAX_MERGE = 60.0

    groups = [[regions[0]]]

    for i in range(1, len(regions)):
        prev = regions[i - 1]
        curr = regions[i]
        gap = curr.get("y0", 0) - prev.get("y1", 0)

        if gap > MAX_MERGE:
            groups.append([curr])
            continue

        if _has_paragraph_between(all_page_items, prev["y1"], curr["y0"]):
            groups.append([curr])
            continue

        # ── 表头缺失拦截 ─────────────────────────────────────────────
        # 非首个 region 缺失独立表头 → liteparse 拆分错误，应合并回上一组。
        # 核心原则：同一页上相邻的两个 region，中间没有文本，且后面的 region
        # 没有独立表头 → 就应该合并到前面，不做额外的 Jaccard 检查。
        if not _region_has_own_header(curr, all_page_items):
            groups[-1].append(curr)
            continue

        if gap <= max(median_row_h * MERGE_RATIO, MIN_MERGE):
            # 小间隙仍需检查表结构差异（防止两个不同表格紧密相邻被合并）
            if _have_different_table_structure(
                groups[-1][-1], curr, all_page_items
            ):
                groups.append([curr])
            else:
                groups[-1].append(curr)
            continue

        if _region_is_new_table(curr, all_page_items):
            groups.append([curr])
        elif _have_different_table_structure(
            groups[-1][-1], curr, all_page_items
        ):
            # Different table structure → split, don't merge
            groups.append([curr])
        else:
            groups[-1].append(curr)

    result = []
    for group in groups:
        y0 = min(r.get("y0", float("inf")) for r in group)
        y1 = max(r.get("y1", 0) for r in group)
        x0 = min(r.get("x0", float("inf")) for r in group)
        x1 = max(r.get("x1", 0) for r in group)

        context_text = group[0].get("context_text", "").strip()
        has_range = any(
            kw in context_text for kw in [
                "截至", "单位：", "万元", "千元", "百万元",
                "财务数据", "资产负债表", "利润表", "现金流量",
            ]
        )

        result.append({
            "page": page_num,
            "y0": y0, "y1": y1,
            "x0": x0, "x1": x1,
            "caption": context_text,
            "caption_info": {
                "text": context_text,
                "has_table_range_info": has_range,
                "can_be_boundary": has_range,
            },
            "source_regions": [
                regions.index(r) if r in regions else -1
                for r in group
            ],
        })

    return result


def _has_paragraph_between(items, y_above, y_below):
    """检测 Y 间隙中是否有段落文本。"""
    gap_items = [
        it for it in items
        if it.get("y0", 0) >= y_above and it.get("y1", 0) <= y_below
    ]
    if not gap_items:
        return False

    all_text = "".join(it.get("text", "") for it in gap_items)
    if re.search(r'[。，；：！？、]', all_text):
        return True

    cn = len(re.findall(r'[\u4e00-\u9fff]', all_text))
    total = len(all_text.strip())
    if total > 0 and cn / total > 0.5 and cn >= 10:
        return True

    return False


def _region_is_new_table(region, items):
    """判断 region 是否为新表格开头。"""
    context_text = region.get("context_text", "").strip()

    # 章节标题模式
    if re.search(r'^\d+[\s\.、）\)]', context_text):
        return True
    if re.search(r'[（\(][一二三四五六七八九十]+[）\)]', context_text):
        return True

    # 区域内首行含实体表头
    ry0, ry1 = region.get("y0", 0), region.get("y1", 0)
    region_items = sorted(
        [it for it in items
         if it.get("y0", 0) >= ry0 - 5 and it.get("y1", 0) <= ry1 + 5],
        key=lambda it: it.get("y0", 0),
    )

    if region_items:
        first_texts = [
            it.get("text", "")
            for it in region_items[:min(3, len(region_items))]
        ]
        all_first = "".join(first_texts)
        if any(kw in all_first for kw in
               {"本集团", "本行", "本公司", "母公司", "合并"}):
            return True

    return False


# ── strict year/date pattern: requires at least 4-digit year prefix ──
_STRICT_YEAR_RE = re.compile(
    r'(?:19|20)\d{2}[年月日/.\-]'
)


# ── delta / change keywords for financial table headers ──
_DELTA_KW = {
    "增加", "减少", "变动", "变化", "上升", "下降",
    "增长", "降低", "提升", "缩减", "差额", "调整",
}


def _region_has_own_header(
    region: dict,
    all_page_items: List[dict],
    lookahead_rows: int = 3,
) -> bool:
    """Check whether a liteparse region has its own table header rows.

    A liteparse region that *lacks* its own header is almost certainly
    an artificial split of a larger table — the preceding region
    contains the header row(s) and the current region is a bare data
    continuation.

    Detection signals (any one of these = has header):
      1. Chapter / section number prefix in the first row
         (e.g. "1.", "一、", "(一)")
      2. >= 2 year/date cells among the first lookahead_rows
         (e.g. "2024年", "12月31日")
      3. Entity-label cells (本集团/本行/本公司/母公司/子公司)
      4. Delta / change keywords (增加/减少/变动 etc.) that imply
         a comparative financial header structure

    If the very first clustered row is > 50% numeric, the region is
    treated as data-only (no header), regardless of later signals.

    Args:
        region: liteparse table region dict with y0 / y1.
        all_page_items: liteparse text_items for the page.
        lookahead_rows: how many clustered rows to inspect (default 3).

    Returns:
        True if the region appears to contain at least one header row.
    """
    ry0, ry1 = region.get("y0", 0), region.get("y1", 0)

    # ── scope: items whose centre lies inside the region (±8pt) ──
    scoped = [
        it for it in all_page_items
        if ry0 - 8 <= it.get("y_mid", (it["y0"] + it["y1"]) / 2) <= ry1 + 8
    ]
    if len(scoped) < 3:
        return True  # too few items → err on the safe side (don't force-merge)

    scoped.sort(key=lambda it: it.get("y0", 0))

    # ── cluster scoped items into rows by Y-mid proximity (8pt) ──
    rows: List[List[str]] = []
    for it in scoped:
        ym = it.get("y_mid", (it["y0"] + it["y1"]) / 2)
        if not rows or abs(ym - rows[-1][0]) > 8:
            rows.append([ym, it.get("text", "").strip()])
        else:
            rows[-1].append(it.get("text", "").strip())

    if not rows:
        return True

    # trim y-mid sentinel values → pure text rows
    text_rows = [r[1:] for r in rows]

    # ── signal 0: first row mostly numeric → pure data continuation ──
    first_row = text_rows[0]
    if first_row:
        first_count = sum(
            1 for c in first_row
            if _is_numeric_cell(c) or re.fullmatch(r'[\d,.\s\-%‰（）()]+', c)
        )
        if first_count / len(first_row) > 0.5:
            return False

    # ── scan first lookahead_rows ──
    year_count = 0
    for ri in range(min(lookahead_rows, len(text_rows))):
        row_texts = text_rows[ri]
        flat = "".join(row_texts)

        # Signal 1: chapter prefix
        if re.search(r'^\d+[\s\.、）\)]', flat):
            return True
        if re.search(r'[（\(][一二三四五六七八九十]+[）\)]', flat):
            return True

        # Signal 2: count year/date cells
        for t in row_texts:
            if _STRICT_YEAR_RE.search(t):
                year_count += 1

        # Signal 3: entity labels
        non_empty = [t for t in row_texts if t]
        if any(t in ENTITY_KW for t in non_empty):
            return True

        # Signal 4: delta keywords
        if any(kw in flat for kw in _DELTA_KW):
            return True

    if year_count >= 2:
        return True

    return False


def _get_region_text_tokens(
    items: List[dict],
    region: dict,
    n_rows: int = 3,
) -> List[str]:
    """Extract normalized text tokens from the first N rows of a region.

    Used for comparing whether two adjacent regions belong to the same
    table or different tables.  Only non-numeric text tokens are kept,
    since numeric values vary between every table instance.
    """
    ry0, ry1 = region.get("y0", 0), region.get("y1", 0)
    region_items = sorted(
        [it for it in items
         if it.get("y0", 0) >= ry0 - 5 and it.get("y1", 0) <= ry1 + 5],
        key=lambda it: it.get("y0", 0),
    )

    if not region_items:
        return []

    # Group items into rows by Y-mid proximity
    rows = []
    current_row = []
    current_y_mid = None

    for it in region_items:
        y_mid = (it.get("y0", 0) + it.get("y1", 0)) / 2
        if current_y_mid is None or abs(y_mid - current_y_mid) <= 6:
            current_row.append(it.get("text", "").strip())
            if current_y_mid is None:
                current_y_mid = y_mid
        else:
            if current_row:
                rows.append(current_row)
            current_row = [it.get("text", "").strip()]
            current_y_mid = y_mid
    if current_row:
        rows.append(current_row)

    # Extract tokens from first N rows — keep only non-numeric text
    tokens = []
    for row in rows[:n_rows]:
        for cell in row:
            if not cell:
                continue
            # Strip numbers & punctuation, keep Chinese chars + letters
            stripped = re.sub(r'[\d,.\s%‰（）()\-—–+*/=]', '', cell)
            if stripped and len(stripped) >= 1:
                tokens.append(stripped)

    return tokens


def _have_different_table_structure(
    prev_region: dict,
    curr_region: dict,
    all_page_items: List[dict],
) -> bool:
    """Check if two adjacent regions have fundamentally different table
    structures, indicating they should NOT be merged.

    Signals:
      1. Context text unit shift  (e.g. one says "百分比", other doesn't)
      2. Jaccard similarity of first N rows' text tokens < 0.2
    """
    # Signal 1: unit / annotation change in context text
    prev_ctx = prev_region.get("context_text", "").strip()
    curr_ctx = curr_region.get("context_text", "").strip()

    unit_kw = {"百万元", "千元", "万元", "亿元", "百分比", "占比", "%"}
    prev_units = {kw for kw in unit_kw if kw in prev_ctx}
    curr_units = {kw for kw in unit_kw if kw in curr_ctx}
    unit_changed = bool(prev_units or curr_units) and (prev_units != curr_units)

    if unit_changed:
        return True

    # Signal 2: first-row text token similarity
    prev_tokens = _get_region_text_tokens(all_page_items, prev_region)
    curr_tokens = _get_region_text_tokens(all_page_items, curr_region)

    if not prev_tokens or not curr_tokens:
        return False

    prev_set = set(prev_tokens)
    curr_set = set(curr_tokens)
    intersection = prev_set & curr_set
    union = prev_set | curr_set
    jaccard = len(intersection) / len(union) if union else 1.0

    # Very low Jaccard → structurally different tables
    return jaccard < 0.2


# ================================================================
# Phase 1.5: 间隙文本捕获 — 捕获表格边界之间的文本
# ================================================================


def _cluster_text_items_into_blocks(
    items: List[dict],
    y_gap_threshold: float = 15.0,
) -> List[dict]:
    """将 Y 范围内 text_items 按 Y 邻近度聚类为文本块。

    每个块包含：
        {y0, y1, text_items, full_text, num_ratio, col_count, cn_char_count}
    """
    if not items:
        return []

    sorted_items = sorted(items, key=lambda it: it.get("y0", 0))

    # 聚类：Y 间距 > threshold → 新块
    blocks = []
    current_block = [sorted_items[0]]

    for it in sorted_items[1:]:
        prev_bottom = max(
            blk.get("y1", 0) for blk in current_block
        )
        curr_top = it.get("y0", 0)
        if curr_top - prev_bottom > y_gap_threshold:
            blocks.append(current_block)
            current_block = [it]
        else:
            current_block.append(it)

    if current_block:
        blocks.append(current_block)

    # 计算每个块的元信息
    result = []
    for block_items in blocks:
        y0 = min(it.get("y0", 0) for it in block_items)
        y1 = max(it.get("y1", 0) for it in block_items)

        # 按行重建全文（Y 相近的同行 items 加空格，不同行换行）
        rows_by_y = _cluster_items_by_y(block_items)
        lines = []
        for row in rows_by_y:
            line = " ".join(
                it.get("text", "").strip()
                for it in sorted(row.get("items", []),
                                 key=lambda it: it.get("x0", 0))
            )
            if line.strip():
                lines.append(line.strip())
        full_text = "\n".join(lines)

        # 数值密度
        all_text = "".join(it.get("text", "") for it in block_items)
        num_chars = len(re.findall(r'[\d.,%‰（）()\-]', all_text))
        total_chars = len(all_text.strip())
        num_ratio = num_chars / total_chars if total_chars > 0 else 0

        # 中文密度
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', all_text))
        cn_char_count = cn_chars

        # X 聚类估算列数
        x_centers = sorted(set(
            round((it.get("x0", 0) + it.get("x1", 0)) / 2, 1)
            for it in block_items
        ))
        col_count = 1
        if len(x_centers) >= 2:
            gaps = [
                x_centers[i + 1] - x_centers[i]
                for i in range(len(x_centers) - 1)
            ]
            if gaps:
                med_gap = sorted(gaps)[len(gaps) // 2]
                if med_gap > 0:
                    col_count = sum(1 for g in gaps if g > med_gap * 0.5) + 1

        result.append({
            "y0": y0,
            "y1": y1,
            "text_items": block_items,
            "full_text": full_text,
            "num_ratio": num_ratio,
            "col_count": col_count,
            "cn_char_count": cn_char_count,
        })

    return result


def _is_potential_missed_table(block: dict) -> bool:
    """判断间隙文本块是否像被 liteparse 遗漏的表格。

    信号：
      1. 高数值密度（>35%）+ 多列对齐（≥2列）
      2. 含日期/年份模式 + 多列对齐 + **含实质数据行**（非纯表头）
      3. 多行结构 + 数值行 ≥2

    排除：仅含 2024年/12月31日 等多级表头、无数据行的块 → 应挂到下方主表。
    """
    items = block.get("text_items", [])
    if len(items) < 4:
        return False

    # 纯日期类表头 → 不是遗漏表格
    if is_date_only_header_block(block):
        return False

    num_ratio = block.get("num_ratio", 0)
    col_count = block.get("col_count", 1)
    full_text = block.get("full_text", "")

    # Signal 1: 高数值密度 + 多列
    if num_ratio > 0.35 and col_count >= 2:
        return True

    # Signal 2: 日期/年份 + 多列 + 必须有数据行（避免表头被误恢复为独立表）
    if _STRICT_YEAR_RE.search(full_text) and col_count >= 2:
        rows = _cluster_items_by_y(_ensure_y_mid(items))
        data_rows = 0
        for row in rows:
            row_items = row.get("items", [])
            if is_date_only_header_row_items(row_items):
                continue
            num_cells = sum(
                1 for it in row_items
                if _is_numeric_cell(it.get("text", ""))
            )
            if num_cells >= 2:
                data_rows += 1
        if data_rows >= 1:
            return True

    # Signal 3: 多行结构 + 含数值行
    rows = _cluster_items_by_y(items)
    if len(rows) >= 2:
        numeric_rows = 0
        for row in rows:
            row_text = "".join(
                it.get("text", "") for it in row.get("items", [])
            )
            num_cells = sum(
                1 for it in row.get("items", [])
                if _is_numeric_cell(it.get("text", ""))
            )
            if num_cells >= 2:
                numeric_rows += 1
        if numeric_rows >= 2:
            return True

    return False


def _classify_gap_text(
    block: dict,
    prev_boundary: Optional[dict],
    next_boundary: Optional[dict],
    median_row_h: float = 12.0,
) -> Tuple[str, Optional[str]]:
    """对间隙文本块进行分类，决定其归属。

    Returns:
        (target, field_name)
        target: "prev" / "next" / "standalone" / "new_table"
        field_name: "description_text" / "notes" / None
    """
    full_text = block.get("full_text", "")
    block_y0 = block.get("y0", 0)
    block_y1 = block.get("y1", 0)

    # ── Signal 1: 方向性关键词 ──
    # "下表" / "以下" / "列示如下" → 后一张表的 description_text
    leading_patterns = [
        r'下表', r'以下列示', r'列示如下', r'如下表', r'所示如下',
        r'详见下表', r'见下表', r'如下列示', r'披露如下',
    ]
    for pat in leading_patterns:
        if re.search(pat, full_text):
            return ("next", "description_text")

    # "注" / "注释" / "*" / "来源" → 前一张表的 notes
    footnote_patterns = [
        r'^注[：:\s]', r'^注释', r'^\*[^*]', r'^来源[：:]', r'^数据来源',
        r'^\d+[\.\、]\s*注', r'^附注', r'^说明[：:]', r'^资料[来源]',
        r'^\*注', r'^[*＊]注',
    ]
    for pat in footnote_patterns:
        if re.search(pat, full_text, re.MULTILINE):
            return ("prev", "notes")

    # "上表" / "上述" / "如上" → 前一张表的 description_text
    trailing_patterns = [
        r'上表', r'上述', r'如上', r'以上数据', r'从上表',
    ]
    for pat in trailing_patterns:
        if re.search(pat, full_text):
            return ("prev", "description_text")

    # ── Signal 1.5: 日期类多级表头 → 挂到下方主表（不按列数拆表） ──
    if is_date_only_header_block(block) and next_boundary is not None:
        return ("next", "_pre_header")

    # ── Signal 2: 疑似遗漏表格 ──
    if _is_potential_missed_table(block):
        return ("new_table", None)

    # ── Signal 3: 纯中文段落（>30 个汉字）→ 就近挂载 ──
    cn_count = block.get("cn_char_count", 0)
    if cn_count >= 30:
        # 长中文段落，Y 距离判定就近挂载
        dist_to_prev = float('inf')
        dist_to_next = float('inf')

        if prev_boundary:
            dist_to_prev = block_y0 - prev_boundary.get("y1", 0)
        if next_boundary:
            dist_to_next = next_boundary.get("y0", 0) - block_y1

        threshold = median_row_h * 3.0

        if dist_to_prev <= threshold and dist_to_prev < dist_to_next:
            # 靠近前表 → 作为后置说明
            return ("prev", "notes")
        elif dist_to_next <= threshold:
            # 靠近后表 → 作为前置说明
            return ("next", "description_text")
        # 距离都远 → 独立段落
        return ("standalone", None)

    # ── Signal 4: Y 距离启发式（小块文本） ──
    dist_to_prev = float('inf')
    dist_to_next = float('inf')

    if prev_boundary:
        dist_to_prev = block_y0 - prev_boundary.get("y1", 0)
    if next_boundary:
        dist_to_next = next_boundary.get("y0", 0) - block_y1

    threshold = median_row_h * 2.5

    if dist_to_prev <= threshold and dist_to_prev < dist_to_next:
        return ("prev", "notes")
    elif dist_to_next <= threshold:
        return ("next", "description_text")

    # 都不靠近 → 独立文本
    return ("standalone", None)


def _build_table_from_gap_block(
    block: dict,
    page_num: int,
    liteparse_data: dict,
) -> Optional[dict]:
    """从间隙文本块构建表格条目（遗漏表格恢复）。

    用 X/Y 聚类从 text_items 重建行列结构，置信度低于正常表格（0.65）。
    """
    items = block.get("text_items", [])
    if len(items) < 3:
        return None

    # Y 聚类为行
    rows = _cluster_items_by_y(items, use_dynamic_threshold=True)
    if len(rows) < 2:
        return None

    rows = _normalize_rows_to_columns(rows)
    if len(rows) < 2:
        return None

    # 转 data 格式
    data = []
    for row in rows:
        texts = row.get("texts", [])
        if texts:
            data.append(list(texts))
        else:
            cols = row.get("columns", [])
            if cols:
                data.append([c.get("text", "") for c in cols])
            else:
                data.append([])

    data = [r for r in data if any(str(c).strip() for c in r)]
    if len(data) < 2:
        return None

    # ── 脚注行剥离（防御：间隙恢复的表格也可能含脚注） ──
    col_count = max((len(r) for r in data), default=0)
    rows_before_strip = len(data)
    data, footnote_texts = _strip_footnote_rows_from_data(data, col_count)

    boundary = {
        "page": page_num,
        "y0": block.get("y0", 0),
        "y1": block.get("y1", 0),
        "x0": min(it.get("x0", 0) for it in items),
        "x1": max(it.get("x1", 0) for it in items),
        "caption": "",
        "caption_info": {},
        "source_regions": [],
    }

    table = _build_fused_table(data, boundary, docx_sources=[])
    table["extractor"] = "hybrid_gap_table_recovery"
    table["segment_source"] = "liteparse_gap_recovery"
    table["source_docx_count"] = 0
    table["confidence"] = 0.65
    table["parse_message"] = "间隙文本识别为遗漏表格(liteparse未检测到)"
    table["_is_gap_recovered_table"] = True
    table["type"] = "table"
    attach_stripped_footnotes(
        table, footnote_texts, rows_before_strip=rows_before_strip,
    )

    block_text = block.get("full_text", "")
    if block_text:
        snippet = block_text[:80]
        print(f"  [间隙恢复] P{page_num}: y={block['y0']:.0f}-{block['y1']:.0f} "
              f"\"{snippet}\"")

    return table


def _capture_gap_text_items(
    boundaries: List[dict],
    liteparse_data: dict,
) -> Tuple[List[dict], List[dict]]:
    """Phase 1.5: 捕获表格边界之间的间隙文本。

    对每页的 boundary 间隙：
      1. 聚类 text_items 为文本块
      2. 关键词 + Y 距离分类：description_text / notes / standalone / new_table
      3. description_text / notes → 挂载到边界元数据
      4. standalone → 独立文本条目
      5. new_table → 恢复为遗漏表格

    Returns:
        (enriched_boundaries, gap_entries)
        - enriched_boundaries: 带 _gap_description / _gap_notes 的边界
        - gap_entries: [{type: "text"/"table", ...}] 独立条目
    """
    if not boundaries:
        return boundaries, []

    # 构建 page → items 映射
    pages = liteparse_data.get("pages", [])
    page_items_map = {}
    for lp_page in pages:
        pn = lp_page.get("page_number", 0)
        raw = lp_page.get("text_items", [])
        if raw:
            page_items_map[pn] = _build_items(raw, pn)

    # 按页分组 boundaries
    boundaries_by_page: Dict[int, List[Tuple[int, dict]]] = {}
    for i, b in enumerate(boundaries):
        pn = b.get("page", 0)
        boundaries_by_page.setdefault(pn, []).append((i, b))

    enriched_boundaries = [dict(b) for b in boundaries]  # shallow copy
    gap_entries: List[dict] = []

    for page_num, page_boundaries in boundaries_by_page.items():
        page_items = page_items_map.get(page_num, [])
        if not page_items:
            continue

        median_row_h = _estimate_median_row_height(page_items)

        # 按 y0 排序
        page_boundaries.sort(key=lambda x: x[1].get("y0", 0))
        sorted_pairs = page_boundaries

        # 收集所有间隙：(prev_boundary, next_boundary_or_None)
        gaps: List[Tuple[Optional[dict], Optional[dict]]] = []

        # 页首间隙（第一个 boundary 之前）
        first_b = sorted_pairs[0][1]
        if first_b.get("y0", 0) > 0:
            gaps.append((None, first_b))

        # 中间间隙
        for i in range(1, len(sorted_pairs)):
            gaps.append((sorted_pairs[i - 1][1], sorted_pairs[i][1]))

        # 页尾间隙（最后一个 boundary 之后）
        last_b = sorted_pairs[-1][1]
        page_bottom = max(
            (it.get("y1", 0) for it in page_items), default=last_b.get("y1", 0) + 100
        )
        if last_b.get("y1", 0) < page_bottom - 10:
            gaps.append((last_b, None))

        # 处理每个间隙
        # ── Phase 2 向下扩展 30pt 收集表格行 ──
        # gap 的起点也需对齐，避免同一批 items 被两边重复收取
        Y_MARGIN_BELOW = 30.0

        for gap_idx, (prev_b, next_b) in enumerate(gaps):
            # 确定 Y 范围
            if prev_b is None:
                # 页首间隙
                gap_y0 = 0
                gap_y1 = next_b.get("y0", 0)
            elif next_b is None:
                # 页尾间隙（从上一个表格的扩展 y1 之后开始）
                gap_y0 = prev_b.get("y1", 0) + Y_MARGIN_BELOW
                gap_y1 = max(
                    it.get("y1", 0) for it in page_items
                ) + 10
            else:
                # 中间间隙（从上一个表格的扩展 y1 之后开始，到下一个表格的 y0）
                gap_y0 = prev_b.get("y1", 0) + Y_MARGIN_BELOW
                gap_y1 = next_b.get("y0", 0)

            if gap_y1 - gap_y0 < 2:
                continue

            # 收集间隙内的 text_items（中心点判断）
            gap_items = []
            for it in page_items:
                cy = it.get("y_mid", (it.get("y0", 0) + it.get("y1", 0)) / 2)
                if gap_y0 - 2 <= cy <= gap_y1 + 2:
                    gap_items.append(it)

            if not gap_items:
                continue

            # 聚类为文本块
            blocks = _cluster_text_items_into_blocks(gap_items)

            for block in blocks:
                target, field = _classify_gap_text(
                    block, prev_b, next_b, median_row_h
                )

                block_text = block.get("full_text", "")
                if not block_text:
                    continue

                # ── 找到对应的 boundary 索引 ──
                prev_idx = None
                next_idx = None
                for idx, b in enumerate(boundaries):
                    if prev_b is not None and b is prev_b:
                        prev_idx = idx
                    if next_b is not None and b is next_b:
                        next_idx = idx

                if target == "prev" and prev_idx is not None:
                    if field == "description_text":
                        cur = enriched_boundaries[prev_idx].get(
                            "_gap_description", ""
                        )
                        enriched_boundaries[prev_idx]["_gap_description"] = (
                            cur + "\n" + block_text if cur else block_text
                        )
                    elif field == "notes":
                        cur = enriched_boundaries[prev_idx].get("_gap_notes", "")
                        enriched_boundaries[prev_idx]["_gap_notes"] = (
                            cur + "\n" + block_text if cur else block_text
                        )

                elif target == "next" and next_idx is not None:
                    if field == "description_text":
                        cur = enriched_boundaries[next_idx].get(
                            "_gap_description", ""
                        )
                        enriched_boundaries[next_idx]["_gap_description"] = (
                            cur + "\n" + block_text if cur else block_text
                        )
                    elif field == "_pre_header":
                        pre = enriched_boundaries[next_idx].get(
                            "_pre_header_items", []
                        )
                        pre.extend(block.get("text_items", []))
                        enriched_boundaries[next_idx]["_pre_header_items"] = pre
                        new_y0 = block.get("y0", 0)
                        old_y0 = enriched_boundaries[next_idx].get("y0", 0)
                        if new_y0 > 0 and (old_y0 <= 0 or new_y0 < old_y0):
                            enriched_boundaries[next_idx]["y0"] = new_y0

                elif target == "new_table":
                    recovered = _build_table_from_gap_block(
                        block, page_num, liteparse_data
                    )
                    if recovered:
                        gap_entries.append(recovered)

                elif target == "standalone":
                    # 从 block["text_items"] 提取 item_index，用于 Tier 1 精确去重
                    _source_indices = [
                        it.get("item_index")
                        for it in block.get("text_items", [])
                        if it.get("item_index") is not None
                    ]
                    # 从 text_items 推算 X 范围，用于 Tier 2 空间重叠去重
                    # 避免缺省值 x0=0, x1=9999 导致 X 方向重叠比永远不达标
                    _text_items = block.get("text_items", [])
                    if _text_items:
                        _x0 = min(it.get("x0", 9999) for it in _text_items)
                        _x1 = max(it.get("x1", 0) for it in _text_items)
                    else:
                        _x0, _x1 = 0, 0
                    gap_entries.append({
                        "type": "text",
                        "page": page_num,
                        "y0": block.get("y0", 0),
                        "y1": block.get("y1", 0),
                        "x0": _x0,
                        "x1": _x1,
                        "bbox": [_x0, block.get("y0", 0), _x1, block.get("y1", 0)],
                        "context_text": block_text,
                        "data": block_text,
                        "num_ratio": block.get("num_ratio", 0),
                        "col_count": block.get("col_count", 1),
                        "cn_char_count": block.get("cn_char_count", 0),
                        "_gap_text": True,
                        "_source_item_indices": _source_indices,
                    })

    return enriched_boundaries, gap_entries


# ================================================================
# Phase 2: pdf2docx 表格 Y 范围估算
# ================================================================


def _normalize_for_match(text: str) -> str:
    """归一化文本用于匹配。"""
    if not text:
        return ""
    s = re.sub(r'\s+', '', text.strip())
    s = s.replace('，', ',').replace('、', ',')
    s = s.replace('（', '(').replace('）', ')')
    return s


def _estimate_docx_tables_y_ranges(
    docx_tables: List[dict],
    liteparse_data: dict,
) -> dict:
    """通过文本匹配估算 pdf2docx 表格的 Y 范围。"""
    pages_items = {}
    for lp_page in liteparse_data.get("pages", []):
        pn = lp_page.get("page_number", 0)
        raw = lp_page.get("text_items", [])
        if raw:
            pages_items[pn] = _build_items(raw, pn)

    result = {}
    for table in docx_tables:
        page = table.get("page", 0)
        data = table.get("data", [])
        if not data or page not in pages_items:
            result[id(table)] = None
            continue

        items = pages_items[page]

        first_texts = _extract_representative_cells(data, from_start=True)
        last_texts = _extract_representative_cells(data, from_start=False)

        y0 = _find_y_by_text(items, first_texts, find_min=True)
        y1 = _find_y_by_text(items, last_texts, find_min=False)

        result[id(table)] = (y0, y1) if (y0 is not None and y1 is not None) else None

    return result


def _extract_representative_cells(data, from_start=True):
    """提取代表性单元格文本（最多 4 个非空值）。"""
    texts = []
    rows_range = range(min(3, len(data))) if from_start else range(
        max(0, len(data) - 3), len(data)
    )
    for ri in rows_range:
        row = data[ri]
        for cell in row:
            cs = str(cell).strip()
            if cs and len(cs) >= 2:
                texts.append(cs)
                if len(texts) >= 4:
                    return texts
    return texts


def _find_y_by_text(items, search_texts, find_min=True):
    """在 liteparse items 中搜索文本并返回 Y 坐标。

    使用聚类方式选择匹配最多的 Y 簇，避免页面头部/尾部
    的偶然匹配（如 "2024年" 匹配到页眉 "2024年度报告"）。
    """
    if not search_texts:
        return None

    # 收集所有匹配的 Y 坐标
    y_matches = []
    for search in search_texts:
        sn = _normalize_for_match(search)
        if len(sn) < 2:
            continue
        for it in items:
            itn = _normalize_for_match(it.get("text", ""))
            if len(itn) < 2:
                continue
            if sn == itn or sn in itn or itn in sn:
                y = it.get("y0") if find_min else it.get("y1")
                if y is not None:
                    y_matches.append(y)

    if not y_matches:
        return None

    # 按 Y 聚类（gap > 30 视为不同簇）
    y_matches.sort()
    clusters = []
    current_cluster = [y_matches[0]]
    for y in y_matches[1:]:
        if y - current_cluster[-1] > 30:
            clusters.append(current_cluster)
            current_cluster = [y]
        else:
            current_cluster.append(y)
    clusters.append(current_cluster)

    # 选择匹配数最多的簇
    best_cluster = max(clusters, key=len)

    # 从最好簇中取 Y 坐标
    if find_min:
        return best_cluster[0]
    else:
        return best_cluster[-1]


# ================================================================
# Phase 3: 融合 — docx cell 数据 + liteparse 边界
# ================================================================


def fuse_docx_tables_with_boundaries(
    boundaries: List[dict],
    docx_tables: List[dict],
    liteparse_data: dict,
) -> Tuple[List[dict], dict]:
    """将 pdf2docx cell 数据按 liteparse 边界融合。

    Returns:
        (fused_tables, report)
    """
    docx_y_ranges = _estimate_docx_tables_y_ranges(docx_tables, liteparse_data)

    docx_by_page = {}
    for t in docx_tables:
        docx_by_page.setdefault(t.get("page", 0), []).append(t)

    # Phase 2a: 收集所有可能的 (boundary, docx) 匹配及重叠量
    all_matches = []  # (overlap_score, boundary_idx, docx_table_index)
    for bi, b in enumerate(boundaries):
        page = b.get("page", 0)
        by0, by1 = b.get("y0", 0), b.get("y1", 0)

        page_docx = docx_by_page.get(page, [])
        for dt in page_docx:
            dy = docx_y_ranges.get(id(dt))
            if dy is None:
                continue
            dy0, dy1 = dy
            # 计算 Y 重叠量
            overlap_start = max(dy0, by0 - 10)
            overlap_end = min(dy1, by1 + 10)
            if overlap_end > overlap_start:
                overlap_score = overlap_end - overlap_start
                all_matches.append((overlap_score, bi, id(dt)))

    # Phase 2b: 按重叠量降序，将每个 docx 表分配给重叠最大的 boundary
    assigned_ids = set()
    boundary_docx = {}  # bi -> [docx_table]

    for overlap_score, bi, dt_id in sorted(all_matches, key=lambda x: -x[0]):
        if dt_id in assigned_ids:
            continue
        assigned_ids.add(dt_id)
        boundary_docx.setdefault(bi, []).append(dt_id)

    # Phase 2c: 构建融合表
    # 建立 id → table 映射以便快速查找
    id_to_docx = {id(dt): dt for dt in docx_tables}

    fused = []
    liteparse_fallback_count = 0
    for bi, b in enumerate(boundaries):
        dt_ids = boundary_docx.get(bi, [])
        if not dt_ids:
            # liteparse 回退：无 docx 匹配时用 text_items 重建表格
            lp_table = _build_table_from_liteparse_fallback(b, liteparse_data)
            if lp_table:
                fused.append(lp_table)
                liteparse_fallback_count += 1
            continue

        matching = [id_to_docx[dt_id] for dt_id in dt_ids if dt_id in id_to_docx]
        if not matching:
            continue

        matching.sort(key=lambda dt: docx_y_ranges.get(id(dt), (0, 0))[0])

        merged_data = []
        for dt in matching:
            merged_data.extend(dt.get("data", []))

        if not merged_data:
            continue

        fused.append(_build_fused_table(merged_data, b, matching))

    # 未分配的 docx 表格
    for dt in docx_tables:
        if id(dt) not in assigned_ids:
            data = dt.get("data", [])
            if data and len(data) >= 2:
                tb = _build_fused_table(
                    data,
                    {"page": dt.get("page", 0), "y0": 0, "y1": 0,
                     "x0": 0, "x1": 0, "caption": dt.get("context_text", ""),
                     "caption_info": {}, "source_regions": []},
                    [dt],
                )
                tb["_is_orphan_docx"] = True
                fused.append(tb)

    fused.sort(key=lambda t: (t.get("page", 0), t.get("y0", 0)))
    for i, t in enumerate(fused):
        t["table_id"] = i

    docx_mapped_count = len([t for t in fused
        if not t.get("_is_orphan_docx") and not t.get("_is_liteparse_fallback")])

    stats = {
        "total_boundaries": len(boundaries),
        "total_docx_tables": len(docx_tables),
        "mapped_tables": docx_mapped_count + liteparse_fallback_count,
        "docx_mapped_tables": docx_mapped_count,
        "orphan_docx_tables": len([t for t in fused if t.get("_is_orphan_docx")]),
        "liteparse_fallback_tables": liteparse_fallback_count,
        "empty_boundaries": len(boundaries) - docx_mapped_count - liteparse_fallback_count,
    }

    print(f"  [混合融合] {stats['total_boundaries']} 边界 + "
          f"{stats['total_docx_tables']} docx 表 → "
          f"{stats['mapped_tables']} 融合表 "
          f"(docx={stats['docx_mapped_tables']} lite回退={stats['liteparse_fallback_tables']}), "
          f"{stats['orphan_docx_tables']} 孤立docx表")

    return fused, {"fusion_stats": stats, "method": "hybrid"}


_FOOTNOTE_PATTERN = re.compile(
    r'^\d+[\.\、\)\)]|'           # 1. 2、 3)
    r'^注[：:]|'                  # 注：注:
    r'^\*(?:\s|$|[^*])|'          # * / * 脚注 / *文本
    r'^[①②③④⑤⑥⑦⑧⑨⑩]|'        # 圈号脚注
    r'^来源[：:]|'                # 来源：
    r'^数据来源[：:]'              # 数据来源：
)


def _build_numeric_column_profile(
    data: List[list],
) -> List[bool]:
    """建立表格的数据列画像：标记每列是否为数值列。

    对每一列，统计所有行在该列的单元格中数值格占比。
    占比 ≥40% → 标记为数值列（如金额列、占比列）。

    Returns:
        is_numeric_col: [bool, ...] 与 data 列数等长的列表
    """
    if not data:
        return []

    max_cols = max((len(row) for row in data), default=0)
    if max_cols == 0:
        return []

    is_numeric_col = []
    for ci in range(max_cols):
        cells = [
            str(row[ci]) if ci < len(row) else ""
            for row in data
        ]
        valid = [c for c in cells if c.strip()]
        if not valid:
            is_numeric_col.append(False)
            continue
        num_count = sum(1 for c in valid if _is_numeric_cell(c))
        is_numeric_col.append(num_count / len(valid) >= 0.4)

    return is_numeric_col


def _is_data_aligned(
    row: list,
    is_numeric_col: List[bool],
) -> bool:
    """检查候选行在数值列上是否与表格对齐。

    对齐规则：在标记为"数值列"的列上，该格的期望值是
    空 / 数值 / ``－``（横杠占位符）。如果数值列上出现了
    中文文本内容 → 不对齐（判定为脚注/注释行）。
    """
    for ci, is_num in enumerate(is_numeric_col):
        if not is_num:
            continue
        cell = str(row[ci]).strip() if ci < len(row) else ""
        if not cell:
            continue  # 空值允许
        if cell in ("－", "-", "—", "–", "—", "N/A", "NA", "n/a"):
            continue  # 占位符允许
        if _is_numeric_cell(cell):
            continue  # 数值允许
        # 非空 + 非数值 + 非占位 → 不对齐
        return False
    return True


def _strip_footnote_rows_from_data(
    data: List[list],
    col_count: int,
) -> Tuple[List[list], List[str]]:
    """从表格 data 末尾剥离脚注/表下说明行（共享表尾规则）。"""
    return strip_tail_annotation_rows_from_data(data, col_count=col_count)


def _fix_vertical_cjk_rows(data: List[list]) -> List[list]:
    """安全网：检测并修复竖排 CJK 单字行。

    当 liteparse 将中文句子以单字粒度返回时，即使 _merge_chinese_chars
    已合并同行的连续单字，仍可能出现多行各含单个 CJK 字符的竖排文本。
    （例如单字 Y 间距略超合并阈值，但 Y 聚类又将它们各自分到独立行）

    检测条件：≥60% 的行仅含一个 CJK 单字（且无非空数值列）。
    修复方式：将所有符合条件的单字行拼接为一行，丢弃明显不是表格的数据。
    """
    if len(data) < 3:
        return data

    n = len(data)
    single_cjk_indices = []
    for i, row in enumerate(data):
        non_empty = [str(c).strip() for c in row if str(c).strip()]
        if len(non_empty) == 1:
            txt = non_empty[0]
            if len(txt) == 1 and '\u4e00' <= txt <= '\u9fff':
                single_cjk_indices.append(i)

    # 阈值：≥60% 的行是单字，且至少 3 行
    if len(single_cjk_indices) < 3 or len(single_cjk_indices) < n * 0.6:
        return data

    # 合并所有单字行为一行
    merged_text = ""
    for i in single_cjk_indices:
        merged_text += str(data[i][0]).strip()

    # 构建新行：保留非单字行 + 合并行放在首位
    keep_indices = set(single_cjk_indices)
    merged_row = [merged_text] + [""] * max((len(r) - 1 for r in data), default=0)
    new_data = [merged_row]
    for i, row in enumerate(data):
        if i not in keep_indices:
            # 将剩余行中可能存在的短列补齐到与合并行等长
            padded = list(row)
            while len(padded) < len(merged_row):
                padded.append("")
            new_data.append(padded)

    LOG = _get_log()
    LOG.info("  [竖排CJK修复] %d个单字行合并为1行 → \"%s\"",
             len(single_cjk_indices), merged_text[:40])

    return new_data


def _dedupe_text_items(items: List[dict]) -> List[dict]:
    """去除重复 text item（pre_header 与 boundary 范围重叠时）。

    优先 item_index；若无则用 (text, x0, y0) 空间键，避免页码不一致导致去重失效。
    """
    seen: set = set()
    out: List[dict] = []
    for it in items:
        idx = it.get("item_index")
        if idx is not None:
            key = ("i", idx)
        else:
            key = (
                "p",
                it.get("text", ""),
                round(it.get("x0", 0), 1),
                round(it.get("y0", 0), 1),
            )
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _build_table_from_liteparse_fallback(
    boundary: dict,
    liteparse_data: dict,
) -> Optional[dict]:
    """从 liteparse text_items 重建结构化表格（纯 Liteparse 分割主路径）。

    每个 liteparse boundary 范围内的 text_items 通过 Y 聚类分组为行 →
    X 坐标归一化为列 → 构建标准 data 格式 [[cell, cell, ...], ...]。

    X 坐标聚类天然形成列边界，无需依赖 pdf2docx gridSpan，
    从根本上避免幽灵列和粘合数据问题。
    """
    page = boundary.get("page", 0)
    by0, by1 = boundary.get("y0", 0), boundary.get("y1", 0)
    bx0, bx1 = boundary.get("x0", 0), boundary.get("x1", 9999)

    # 找到对应页的 liteparse text_items
    lp_pages = liteparse_data.get("pages", [])
    page_items = None
    for lp_page in lp_pages:
        if lp_page.get("page_number") == page:
            raw = lp_page.get("text_items", [])
            if raw:
                page_items = _build_items(raw, page)
            break

    if not page_items:
        return None

    # Phase 1.5：间隙捕获的多级表头（2024年/12月31日 等）并入本表
    pre_header_items = boundary.get("_pre_header_items", []) or []

    # 收集 boundary 范围内的 items（中心点判断，下方留 30pt 容差捕获漏行）
    scoped = []
    y_margin_below = 30.0
    scope_y0 = by0
    if pre_header_items:
        scope_y0 = min(by0, min(it.get("y0", by0) for it in pre_header_items))
        scoped.extend(pre_header_items)
    for it in page_items:
        cx = (it["x0"] + it["x1"]) / 2
        cy = it.get("y_mid", (it["y0"] + it["y1"]) / 2)
        if bx0 - 10 <= cx <= bx1 + 10 and scope_y0 - 10 <= cy <= by1 + y_margin_below:
            scoped.append(it)

    # 去重（pre_header 可能与 scoped 重叠）
    scoped = _dedupe_text_items(scoped)

    if len(scoped) < 3:
        return None

    # Y 聚类为行 → 列归一化
    rows = _cluster_items_by_y(scoped, use_dynamic_threshold=True)
    if len(rows) < 2:
        return None

    rows = _normalize_rows_to_columns(rows)
    if len(rows) < 2:
        return None

    # 转换为 data 格式 [[cell, cell, ...], ...]
    data = []
    for row in rows:
        texts = row.get("texts", [])
        if texts:
            data.append(list(texts))
        else:
            cols = row.get("columns", [])
            if cols:
                data.append([c.get("text", "") for c in cols])
            else:
                data.append([])

    # 移除空行
    data = [r for r in data if any(str(c).strip() for c in r)]
    if len(data) < 2:
        return None

    # ── 安全网：竖排单字行检测 ──
    # 当 _merge_chinese_chars 未能完全合并时（如单字 Y 间距略超阈值），
    # 检测 data 是否为竖排 CJK 文本（每行仅 1 个 CJK 单字），若为竖排则
    # 将多行合并为一行并标记为非表格段落
    data = _fix_vertical_cjk_rows(data)

    # ── 表头列合并（2024+年 → 2024年）+ 去重 ──
    data = normalize_table_header_columns(data)

    # ── 脚注行剥离 ──
    col_count = max((len(r) for r in data), default=0)
    rows_before_strip = len(data)
    data, footnote_texts = _strip_footnote_rows_from_data(data, col_count)

    # 列压缩延后到 Phase 2.65（结构分裂之后），避免跨子表错合并

    table = _build_fused_table(data, boundary, docx_sources=[])
    table["extractor"] = "hybrid_liteparse_segmentation"
    table["segment_source"] = "liteparse_segmentation"
    table["source_docx_count"] = 0
    table["confidence"] = 0.85  # 纯 liteparse 分割，X/Y 聚类可靠度高
    table["parse_message"] = "liteparse分割(X/Y聚类重建行列)"
    table["_is_liteparse_fallback"] = True
    attach_stripped_footnotes(
        table, footnote_texts, rows_before_strip=rows_before_strip,
    )

    LOG = _get_log()
    LOG.info(
        "  [liteparse分割] P%d y=%.0f-%.0f rows=%d cols=%d%s",
        page, by0, by1, len(data), len(data[0]) if data else 0,
        f" stripped_footnotes={len(footnote_texts)}" if footnote_texts else "",
    )
    print(f"  [liteparse分割] P{page}: {len(data)}行×"
          f"{len(data[0]) if data else 0}列"
          f"{'（剥离脚注行）' if footnote_texts else ''}")

    return table


def _build_fused_table(merged_data, boundary, docx_sources):
    """构建标准格式融合表格。"""
    page = boundary.get("page", 0)
    caption = boundary.get("caption", "")

    row_count = len(merged_data)
    col_count = max((len(r) for r in merged_data), default=0)

    # ── 间隙文本挂载（Phase 1.5 产物） ──
    gap_desc = boundary.get("_gap_description", "")
    gap_notes = boundary.get("_gap_notes", "")
    # description_text: gap_desc（前置说明）优先，caption 次之
    desc = gap_desc or ""
    if caption and boundary.get("caption_info", {}).get("has_table_range_info"):
        desc = (desc + "\n" + caption) if desc else caption
    # notes: gap_notes（间隙脚注），_inline_notes 不再合并（脚注行转为独立文本条目）
    notes = gap_notes or ""

    table = {
        "page": page,
        "pages": [page],
        "y0": boundary.get("y0", 0),
        "y1": boundary.get("y1", 0),
        "type": "table",
        "data": merged_data,
        "rows": row_count,
        "cols": col_count,
        "extractor": "hybrid_fusion",
        "title": caption or f"表格-P{page}",
        "caption": caption,
        "caption_info": boundary.get("caption_info", {}),
        "description_text": desc,
        "notes": notes,
        "is_cross_page": False,
        "is_merged_adjacent": len(boundary.get("source_regions", [])) > 1,
        "is_split_from_mixed": False,
        "segment_source": "hybrid_fusion",
        "confidence": 0.90,
        "source_region_count": len(boundary.get("source_regions", [])),
        "source_docx_count": len(docx_sources),
        "table_id": -1,
        "column_x_ranges": [],
        "_cross_page_candidate": False,
        "_cross_page_ref_table_id": -1,
    }

    # 快速质量评估
    table["is_real_table"] = row_count >= 2
    table["is_complete"] = row_count >= 2
    table["table_category"] = "财务数据表" if row_count >= 2 else "空表"
    table["has_header"] = _quick_header_check(merged_data)
    table["has_numeric_data"] = _quick_numeric_check(merged_data)
    table["quality_decision"] = "accepted" if row_count >= 2 else "rejected"
    table["quality_decision_reason"] = (
        "混合融合表格" if row_count >= 2 else "空表"
    )
    table["quality_decision_score"] = 0.85 if row_count >= 2 else 0.0
    table["quality_flags"] = {}
    table["quality_reason"] = ""
    table["quality_checks"] = {}
    table["numeric_col_count"] = sum(
        1 for c in range(col_count)
        if any(
            c < len(merged_data[r]) and _is_numeric_cell(str(merged_data[r][c]))
            for r in range(row_count)
        )
    )
    table["financial_confidence"] = 0.80 if row_count >= 2 else 0.0
    table["parse_status"] = "success" if row_count >= 2 else "failed"
    table["parse_message"] = (
        "混合融合(边界lite+cell docx)" if row_count >= 2 else "空表"
    )

    return table


def _quick_header_check(data):
    """快速表头检测。"""
    for ri in range(min(3, len(data))):
        row = data[ri]
        non_num = sum(
            1 for c in row
            if str(c).strip() and not _is_numeric_cell(str(c))
        )
        if non_num >= max(1, len(row) // 2):
            return True
    return False


def _quick_numeric_check(data):
    """快速数值检测。"""
    return sum(
        1 for row in data
        if any(_is_numeric_cell(str(c)) for c in row)
    ) >= 2


# ================================================================
# Phase 3.5: 行级结构分裂 — 检测融合表中混入的多张不同表格
# ================================================================


_YEAR_PATTERN = re.compile(
    r'(?:19|20)\d{2}[年]|(?:1[012]|[1-9])月\d{1,2}日|截至.*(?:19|20)\d{2}|'
    r'(?:19|20)\d{2}\.(?:1[012]|[1-9])\.(?:3[01]|[12]\d|[1-9])'
)


def _find_structure_break_in_data(
    data: List[list],
    caption: str = "",
) -> int:
    """Find the row index where a new, structurally different table begins
    within merged data rows.

    Detection signals (in priority order):
      0. Column width jump — current row has >= 40% more columns than
         median of preceding 3 rows (strong structural indicator)
      1. Unit annotation shift — a row has "百分比"/"占比" but caption doesn't
      2. New column headers — a row has "金额"/"占比" keywords, non-numeric, and
         the next row is data-like (2+ numeric cells)
      3. Year / date pattern — a row has "2024年"/"12月31日" etc., non-numeric,
         after at least 3 prior rows, and followed by a sub-header or data row;
         skips if the row looks like a footnote (contains "注"/"附注" etc.)

    Returns the split row index, or -1 if no break is detected.
    The data rows BEFORE the returned index belong to the first table;
    rows FROM the returned index belong to the second table.
    """
    if len(data) < 8:
        return -1

    NEW_UNIT_KW = {"百分比", "占比", "比例"}
    NEW_HEADER_KW = {"金额", "占比", "比例", "数量", "比重", "利率"}
    FOOTNOTE_KW = {"注：", "附注", "注释", "说明：", "资料"}

    # Scan from row 3 to len-2 (need at least 2 rows after split point)
    for i in range(3, len(data) - 2):
        row = [str(c).strip() for c in data[i]]
        row_text = "".join(row)
        non_empty = [c for c in row if c]

        if len(non_empty) < 2:
            continue

        num_count = sum(1 for c in non_empty if _is_numeric_cell(c))

        # Signal 0: Column width jump — different tables often have
        #            different column counts from pdf2docx merged cells
        if i >= 3:
            prev_widths = [len(data[j]) for j in range(i - 3, i) if data[j]]
            if prev_widths:
                med_prev = sorted(prev_widths)[len(prev_widths) // 2]
                curr_width = len(data[i]) if data[i] else 0
                # >40% column increase + at least 6 cols minimum
                if curr_width > med_prev * 1.4 and curr_width > 8:
                    return i

        # Signal 1: unit annotation shift
        has_new_unit = any(kw in row_text for kw in NEW_UNIT_KW)
        cap_has_unit = any(kw in caption for kw in NEW_UNIT_KW)

        if has_new_unit and not cap_has_unit and num_count == 0:
            return i

        # Signal 2: new column header keywords + data follows
        has_header_kw = any(kw in row_text for kw in NEW_HEADER_KW)
        if has_header_kw and num_count == 0 and len(non_empty) >= 3:
            # Verify: next row is data-like
            if i + 1 < len(data):
                nr = [str(c).strip() for c in data[i + 1]]
                nr_ne = [c for c in nr if c]
                nr_num = sum(1 for c in nr_ne if _is_numeric_cell(c))
                if nr_num >= 2:
                    return i

        # Signal 3: year / date pattern in a non-numeric row
        #            (after substantial prior data → new table header section)
        if num_count == 0 and _YEAR_PATTERN.search(row_text):
            # Skip if the row looks like a footnote/annotation with a year reference
            if any(kw in row_text for kw in FOOTNOTE_KW):
                continue
            # At least 3 rows before — enough for a prior table body
            if i >= 3:
                # Verify: next 1-2 rows are also header-like (non-numeric)
                # or the following row has cell count consistent with a table
                next_header_rows = 0
                for offset in range(1, min(3, len(data) - i)):
                    nr = [str(c).strip() for c in data[i + offset]]
                    nr_ne = [c for c in nr if c]
                    nr_num = sum(1 for c in nr_ne if _is_numeric_cell(c))
                    if nr_num == 0 and nr_ne:
                        next_header_rows += 1
                    else:
                        # Data row reached — check it looks like table data
                        if nr_num >= 2:
                            return i
                        break
                # 2 consecutive header rows strongly suggests new table
                if next_header_rows >= 1:
                    # Look further ahead for a data row
                    for offset in range(2, min(5, len(data) - i)):
                        fr = [str(c).strip() for c in data[i + offset]]
                        fr_ne = [c for c in fr if c]
                        fr_num = sum(1 for c in fr_ne if _is_numeric_cell(c))
                        if fr_num >= 2:
                            return i
                        if len(fr_ne) >= 2:
                            # Non-numeric row past 2 header rows → stop looking
                            break

    return -1


def _re_estimate_subtable_y(
    table: dict,
    liteparse_data: dict,
    y0_floor: float | None = None,
) -> None:
    """Re-estimate y0/y1 for a sub-table from liteparse text_items.

    After splitting a fused table, both sub-tables inherit the parent's
    y0 via deepcopy.  If the parent was matched to the wrong liteparse
    boundary (common when pdf2docx merges two separate liteparse regions
    into one docx table), the sort order ends up reversed.

    This function searches liteparse text_items on the sub-table's page
    for distinctive non-numeric cell text and sets y0/y1 accordingly.

    Args:
        y0_floor: Minimum expected y0.  For sub-table B (split from a
            fused table), this should be >= sub-table A's y1.  Prevents
            matching common date strings (e.g. "2024年") to the wrong
            table region.
    """
    import re as _re

    data = table.get("data", [])
    if not data:
        return
    page = table.get("page", 0)

    # Locate liteparse text items for this page
    page_items = None
    for lp_page in liteparse_data.get("pages", []):
        if lp_page.get("page_number", 0) == page:
            raw = lp_page.get("text_items", [])
            if raw:
                page_items = _build_items(raw, page)
            break

    if not page_items:
        return

    def _find_y_for_text(
        target_text: str,
        find_max: bool = False,
        y_min: float | None = None,
    ) -> float | None:
        tn = _normalize_for_match(target_text)
        if len(tn) < 2:
            return None
        best_y = None
        for it in page_items:
            itn = _normalize_for_match(it.get("text", ""))
            if len(itn) < 2:
                continue
            if tn == itn or tn in itn or itn in tn:
                y = it.get("y1" if find_max else "y0")
                if y is None:
                    continue
                # Apply Y floor constraint
                if y_min is not None and y < y_min:
                    continue
                if best_y is None:
                    best_y = y
                elif find_max:
                    best_y = max(best_y, y)
                else:
                    best_y = min(best_y, y)
        return best_y

    # --- y0: search first 3 rows for distinctive non-numeric text ---
    # Gather candidate texts (skip common date/header tokens)
    candidates = []
    for row in data[:3]:
        for cell in row:
            cs = str(cell).strip()
            if not cs or len(cs) < 2 or _is_numeric_cell(cs):
                continue
            # Skip bare year/month strings that appear everywhere
            if _re.fullmatch(r'(?:19|20)\d{2}年?', cs):
                continue
            if _re.fullmatch(r'\d{1,2}月\d{1,2}日', cs):
                continue
            candidates.append(cs)

    # Try candidates; prefer those that give a Y above the floor
    for cs in candidates:
        y = _find_y_for_text(cs, find_max=False, y_min=y0_floor)
        if y is not None:
            table["y0"] = y
            break

    # Fallback: re-try without floor constraint
    if table.get("y0") == table.get("_y0_before_split"):
        for cs in candidates:
            y = _find_y_for_text(cs, find_max=False)
            if y is not None:
                table["y0"] = y
                break

    # --- y1: search last 3 rows for distinctive non-numeric text ---
    candidates_bottom = []
    for row in reversed(data[-3:]):
        for cell in reversed(row):
            cs = str(cell).strip()
            if not cs or len(cs) < 2 or _is_numeric_cell(cs):
                continue
            if _re.fullmatch(r'(?:19|20)\d{2}年?', cs):
                continue
            if _re.fullmatch(r'\d{1,2}月\d{1,2}日', cs):
                continue
            candidates_bottom.append(cs)

    for cs in candidates_bottom:
        y = _find_y_for_text(cs, find_max=True)
        if y is not None:
            table["y1"] = y
            break


def _split_fused_table_by_structure(
    tables: List[dict],
    liteparse_data: dict | None = None,
) -> List[dict]:
    """Post-fusion structural splitter.

    Scans each fused table's data rows for internal structure breaks.
    When a new table structure is detected (unit change, new headers),
    splits the table into two at that boundary.

    After splitting, re-estimates each sub-table's Y coordinates from
    liteparse text_items so sort order reflects physical page position.

    This handles the case where pdf2docx or liteparse region detection
    produces a single merged table containing rows from multiple
    structurally distinct tables.
    """
    import copy
    result = []
    split_count = 0

    for table in tables:
        data = table.get("data", [])
        if len(data) < 8:
            result.append(table)
            continue

        caption = table.get("caption", "")
        split_row = _find_structure_break_in_data(data, caption)

        if split_row < 0:
            result.append(table)
            continue

        t1_data = data[:split_row]
        t2_data = data[split_row:]

        if len(t1_data) < 2 or len(t2_data) < 2:
            result.append(table)
            continue

        # 脚注只跟原表最末段（B），深拷贝前取出避免复制到两个子表
        fn_recs = pop_footnote_bundle(table)

        # Build sub-table A
        t1 = copy.deepcopy(table)
        t1["data"] = t1_data
        t1["rows"] = len(t1_data)
        t1["cols"] = max((len(r) for r in t1_data), default=0)
        t1["_split_from_merged"] = True
        t1["_split_suffix"] = "A"
        t1["_y0_before_split"] = t1.get("y0")
        t1["_y1_before_split"] = t1.get("y1")
        clear_footnote_bundle(t1)

        # Build sub-table B
        t2 = copy.deepcopy(table)
        t2["data"] = t2_data
        t2["rows"] = len(t2_data)
        t2["cols"] = max((len(r) for r in t2_data), default=0)
        t2["_split_from_merged"] = True
        t2["_split_suffix"] = "B"
        t2["_y0_before_split"] = t2.get("y0")
        t2["_y1_before_split"] = t2.get("y1")
        clear_footnote_bundle(t2)
        if fn_recs:
            assign_footnote_bundle(t2, fn_recs)

        # Re-estimate Y from liteparse so physical page order is preserved
        if liteparse_data is not None:
            _re_estimate_subtable_y(t1, liteparse_data)
            # Sub-table B: use sub-table A's y1 as floor so common date strings
            # (e.g. "2024年") don't match to the wrong table region
            t1_y1 = t1.get("y1")
            y0_floor_for_t2 = (t1_y1 + 10) if t1_y1 is not None else None
            _re_estimate_subtable_y(t2, liteparse_data, y0_floor=y0_floor_for_t2)

        result.append(t1)
        result.append(t2)
        split_count += 1
        print(f"  [结构分裂] P{table.get('page', '?')}: "
              f"行 {split_row} 处检测到新表格结构, "
              f"拆分为 {len(t1_data)}+{len(t2_data)} 行 "
              f"[y0: {t1.get('y0','?')}/{t2.get('y0','?')}]")

    if split_count > 0:
        print(f"  [结构分裂] 共从 {split_count} 个融合表中分离出内部不同表格")

    # Sort by page + document order (split sub-table A always before B)
    def _physical_table_sort_key(t: dict):
        page = t.get("page", 0)
        base_y = float(t.get("_y0_before_split", t.get("y0", 0)) or 0)
        suffix = t.get("_split_suffix", "")
        sub = 1 if suffix == "B" else 0
        return (page, base_y, sub, float(t.get("y0", 0) or 0))

    result.sort(key=_physical_table_sort_key)

    # Renumber
    for i, t in enumerate(result):
        t["table_id"] = i

    return result


# ================================================================
# Phase 4: 跨页续表拼接
# ================================================================


def _merge_cross_page_hybrid(tables, liteparse_data):
    """混合表格的跨页续表拼接。"""
    if len(tables) < 2:
        return tables

    merged_indices = set()
    for i in range(len(tables) - 1):
        if i in merged_indices:
            continue
        ta, tb = tables[i], tables[i + 1]
        pa = ta.get("pages", [ta.get("page", 0)])[-1]
        pb = tb.get("pages", [tb.get("page", 0)])[0]

        if pb <= pa:
            continue

        tb_data = tb.get("data", [])
        if not tb_data:
            continue

        # 检查后表是否有完整表头
        tb_rows = [{"texts": [str(c) for c in r]} for r in tb_data]
        if _has_complete_table_header({"rows": tb_rows, "page": pb}):
            continue

        cols_a, cols_b = ta.get("cols", 0), tb.get("cols", 0)
        if cols_a > 0 and cols_b > 0 and abs(cols_a - cols_b) <= 2:
            ta["data"] = ta.get("data", []) + tb_data
            ta["rows"] = len(ta["data"])
            ta["cols"] = max(cols_a, cols_b)
            ta["pages"] = sorted(set(ta.get("pages", [pa]) + [pb]))
            ta["is_cross_page"] = True
            ta["y1"] = tb.get("y1", ta.get("y1", 0))
            merged_indices.add(i + 1)

    return [t for i, t in enumerate(tables) if i not in merged_indices]


# ================================================================
# 一键式入口
# ================================================================


def hybrid_segment_tables(
    liteparse_data: dict,
    docx_tables: List[dict],
    enable_cross_page: bool = False,  # 暂关闭跨页拼接，先专注表格/文本边界分割
    region_confidence_threshold: float = 0.3,
) -> Tuple[List[dict], dict]:
    """一站式混合表格分割（纯 Liteparse 主导）。

    Phase 1:   liteparse regions → 表格边界检测（Y 范围 + 标题）
    Phase 1.5: 间隙文本捕获 → 表格间说明/脚注挂载，遗漏表格恢复，独立文本段落
    Phase 2:   liteparse text_items X/Y 聚类 → 行列结构重建（不依赖 pdf2docx）
    Phase 2.5: 行级结构分裂 → 防御 boundary 内混入多表
    Phase 3:   跨页续表拼接（enable_cross_page=True 时）
    Phase 4:   重新编号 + 标题增强 + 间隙条目合并

    pdf2docx 表格仅作为辅助参考传入，不在分割阶段使用其 cell 数据，
    避免 gridSpan 错误污染列结构。

    Args:
        liteparse_data: ParseResult.to_dict()
        docx_tables: pdf2docx 表格列表 [{page, data: [[str]], ...}]（暂存，供格式化阶段使用）
        enable_cross_page: 是否启用跨页续表拼接
        region_confidence_threshold: region 最低置信度

    Returns:
        (tables, report)
        tables 中混合了 table + text 类型条目，按 page/y0 排序
    """
    LOG = _get_log()
    LOG.info("[混合] hybrid_segment_tables 开始: docx=%d", len(docx_tables))

    # ── Phase 1: 边界检测 ──────────────────────────────────────────
    boundaries = detect_table_boundaries_from_liteparse(
        liteparse_data, region_confidence_threshold
    )

    if not boundaries:
        LOG.info("[混合] 无 liteparse 边界，回退纯 docx")
        print("  [混合] 无 liteparse 边界，使用纯 pdf2docx 表格")
        tables = []
        for dt in docx_tables:
            data = dt.get("data", [])
            if data and len(data) >= 2:
                tb = _build_fused_table(
                    data,
                    {"page": dt.get("page", 0), "y0": 0, "y1": 0,
                     "x0": 0, "x1": 0,
                     "caption": dt.get("context_text", ""),
                     "caption_info": {}, "source_regions": []},
                    [dt],
                )
                tables.append(tb)
        for i, t in enumerate(tables):
            t["table_id"] = i
        return tables, {
            "fusion_stats": {"total_boundaries": 0, "total_docx_tables": len(docx_tables)},
            "method": "docx_only",
        }

    print(f"  [混合] 检测到 {len(boundaries)} 个表格边界")
    for b in boundaries:
        print(f"    P{b['page']}: y={b['y0']:.0f}-{b['y1']:.0f}"
              f" caption=\"{b.get('caption','')[:50]}\"")

    # ── Phase 1.5: 间隙文本捕获 ─────────────────────────────────────
    boundaries, gap_entries = _capture_gap_text_items(boundaries, liteparse_data)

    gap_table_count = sum(1 for e in gap_entries if e.get("type") == "table")
    gap_text_count = sum(1 for e in gap_entries if e.get("type") == "text")
    if gap_entries:
        print(f"  [间隙捕获] {len(gap_entries)} 个间隙条目 "
              f"(恢复遗漏表={gap_table_count}, 独立文本={gap_text_count})")

    # ── Phase 2: 纯 Liteparse 分割 ──────────────────────────────────
    # 使用 enriched boundaries（含 _gap_description / _gap_notes）
    tables = []
    liteparse_count = 0
    for b in boundaries:
        t = _build_table_from_liteparse_fallback(b, liteparse_data)
        if t:
            tables.append(t)
            liteparse_count += 1

    tables.sort(key=lambda t: (t.get("page", 0), t.get("y0", 0)))
    for i, t in enumerate(tables):
        t["table_id"] = i

    print(f"  [混合] {len(boundaries)} 边界 → {liteparse_count} liteparse表格 "
          f"(docx={len(docx_tables)}暂存供格式化阶段使用)")

    # ── Phase 2.5: 行级结构分裂 ─────────────────────────────────────
    tables = _split_fused_table_by_structure(tables, liteparse_data)

    # ── Phase 2.6: 表内段落/子表拆分 ───────────────────────────────
    tables = split_mixed_table_entries(tables)

    # ── Phase 2.65: 全表空行/空列清理 + 互补列合并（结构分裂后再压一次）──
    for t in tables:
        if t.get("type") == "text" or not t.get("data"):
            continue
        data = compact_table_spacer_rows_and_columns(t["data"])
        t["data"] = data
        t["rows"] = len(data)
        t["cols"] = max((len(r) for r in data), default=0)

    # ── Phase 3: 跨页拼接 ──────────────────────────────────────────
    if enable_cross_page:
        tables = _merge_cross_page_hybrid(tables, liteparse_data)

    # ── Phase 4: 合并间隙条目 + 脚注文本提取 + 重新编号 + 质量增强 ────
    # 将 gap_entries（独立文本/遗漏表格）并入 tables 列表。
    # 去重：Phase 2 向下扩展了 30pt Y 容差，可能导致 gap 中部分文本
    # 已包含在表格内，需过滤避免重复。
    gap_deduped = []
    for ge in gap_entries:
        ge_page = ge.get("page", 0)
        ge_y0 = ge.get("y0", -1)
        ge_y1 = ge.get("y1", -1)
        # 检查是否落在任意表格的扩展 Y 范围内
        overlapped = False
        for t in tables:
            if t.get("page", 0) != ge_page:
                continue
            ty1 = t.get("y1", 0)
            # gap 条目的 Y 范围与表格的扩展区域（y1+35pt）有重叠 → 已纳入
            if ge_y0 <= ty1 + 35 and ge_y1 >= ty1 - 5:
                overlapped = True
                break
        if not overlapped:
            gap_deduped.append(ge)

    # 脚注：从表格载荷取出，在原 Y 位置输出唯一 text 条目
    footnote_entries = []
    seen_footnote_keys: set = set()
    for t in tables:
        for rec in pop_footnote_bundle(t):
            text = str(rec.get("text", "")).strip()
            if not text:
                continue
            page = int(rec.get("page", t.get("page", 0)) or 0)
            key = footnote_text_key(page, text)
            if key in seen_footnote_keys:
                continue
            seen_footnote_keys.add(key)
            footnote_entries.append({
                "type": "text",
                "page": page,
                "y0": float(rec.get("y0", t.get("y1", 0)) or 0),
                "y1": float(rec.get("y1", t.get("y1", 0)) or 0),
                "context_text": text,
                "_is_footnote": True,
            })

    # 间隙文本若与已登记脚注正文相同 → 丢弃，禁止第二份复制
    gap_final = []
    for ge in gap_deduped:
        if ge.get("type") == "text":
            ctx = ge.get("context_text") or ge.get("data") or ""
            if isinstance(ctx, str) and ctx.strip():
                if footnote_text_key(ge.get("page", 0), ctx) in seen_footnote_keys:
                    continue
        gap_final.append(ge)

    all_entries = tables + gap_final
    if footnote_entries:
        all_entries.extend(footnote_entries)
        print(f"  [脚注提取] {len(footnote_entries)} 个脚注行转为独立文本条目（原位置唯一）")

    all_entries.sort(key=lambda t: (t.get("page", 0), t.get("y0", 0)))

    # 独立文本条目补全字段（确保下游兼容）
    for entry in all_entries:
        if entry.get("type") == "text":
            entry.setdefault("title", entry.get("context_text", "")[:80])
            entry.setdefault("data", [])
            entry.setdefault("rows", 0)
            entry.setdefault("cols", 0)
            entry.setdefault("confidence", 0.60)
            entry.setdefault("extractor", "hybrid_gap_text")
            entry.setdefault("segment_source", "gap_text_capture")
            entry.setdefault("is_real_table", False)
            entry.setdefault("is_complete", False)
            entry.setdefault("table_category", "文本段落")
            entry.setdefault("has_header", False)
            entry.setdefault("has_numeric_data", False)
            entry.setdefault("quality_decision", "accepted")
            entry.setdefault("parse_status", "success")

    report = {
        "fusion_stats": {
            "total_boundaries": len(boundaries),
            "total_docx_tables": len(docx_tables),
            "mapped_tables": liteparse_count,
            "docx_mapped_tables": 0,
            "orphan_docx_tables": 0,
            "liteparse_fallback_tables": liteparse_count,
            "empty_boundaries": len(boundaries) - liteparse_count,
            "gap_total": len(gap_entries),
            "gap_recovered_tables": gap_table_count,
            "gap_standalone_texts": gap_text_count,
        },
        "method": "liteparse_only",
    }

    # 重新编号（table + text 混合）
    for i, t in enumerate(all_entries):
        t["table_id"] = i

    all_entries = _enhance_with_caption_boundary(all_entries)

    LOG.info("[混合] 完成: tables=%d text=%d",
             liteparse_count + gap_table_count, gap_text_count)
    return all_entries, report
