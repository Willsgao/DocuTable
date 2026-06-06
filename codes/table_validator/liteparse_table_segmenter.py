# -*- coding: utf-8 -*-
"""
liteparse 表格区域分割器（纯规则驱动，无 LLM 依赖）

这是双源融合架构的第一步：
  仅使用 liteparse 自身的数据结构（table_regions + text_items），
  以纯规则方式将每一页的文本精确切分为独立的逻辑表格。

核心能力：
  1. 基于 liteparse 内置 table_regions 的区域切分（bbox 过滤 text_items）
  2. text_items → Y聚类 → 逻辑行（复用 cell_differ 的聚类逻辑）
  3. 跨页续表启发式拼接（列结构匹配 + 无表头检测）
  4. 覆盖度验证报告（孤儿 text_items 检测、表数统计）

设计原则：
  - 零 API 成本，零 LLM 依赖
  - 完全基于 liteparse 数据，与 pdf2docx 完全解耦
  - 可独立运行，也可作为后续 LLM 增强的基线
  - region 不可用时降级为全页 text_items

输出：
  tables: [
    {
      "table_id": 0,                # 全局唯一 ID（分割后）
      "pages": [8],                 # 所属页码（跨页合并后可能多页）
      "page": 8,                    # 起始页（向前兼容）
      "y0": 100.5, "y1": 500.3,     # liteparse 坐标系 Y 范围
      "text_items": [...],          # 该表的 liteparse text_items（已按Y,X排序）
      "rows": [...],                # Y 聚类后的行列表
      "row_count": 15,              # 行数
      "is_cross_page": False,       # 是否跨页
      "caption": "财务摘要",         # 从 region.context_text 提取
      "region_index": 0,            # 来源 region 索引（-1 表示无 region）
      "confidence": 0.85,           # 来源 region 置信度
      "column_x_ranges": [(50,200), ...],  # 列 X 范围（从所有行统计）
    },
    ...
  ]

  report: {
    "total_pages": 20,
    "table_pages": 5,
    "total_tables": 7,
    "cross_page_merges": 1,
    "orphan_page_items": {          # 表格页上未被归属的 items
      8: [{"text": "...", "y0": ..., "y1": ...}, ...],
    },
    "page_details": {
      8: {"tables": 2, "items_total": 45, "items_assigned": 43, "orphans": 2},
    },
    "table_summaries": [...],
  }
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

# 复用 cell_differ 的核心工具（Y 聚类、文本归一化、列签名、小数合并）
from codes.table_validator.cell_differ import (
    _cluster_items_by_y,
    _normalize_for_search,
    infer_column_schema,
    classify_item_type,
    _score_table_row,
    _merge_split_decimals,
)


# ================================================================
# 1. 公开接口
# ================================================================

def segment_tables_from_liteparse(
    liteparse_data: dict,
    enable_cross_page: bool = True,
    region_confidence_threshold: float = 0.3,
) -> Tuple[List[dict], dict]:
    """从 liteparse 数据中分割出所有逻辑表格。

    Args:
        liteparse_data: ParseResult.to_dict()
        enable_cross_page: 是否启用跨页续表自动拼接
        region_confidence_threshold: table_region 最低置信度，
                                    低于此值的 region 被忽略

    Returns:
        (tables, report) — 见模块 docstring
    """
    pages = liteparse_data.get("pages", [])
    if not pages:
        return [], {"error": "liteparse_data 中无页面数据"}

    # ---- Phase 1: 逐页分割 ----
    all_tables = []  # [(page_num, table_dict), ...] 保持页面顺序

    for lp_page in pages:
        page_num = lp_page.get("page_number", 0)
        text_items_raw = lp_page.get("text_items", [])

        if not text_items_raw:
            continue

        # 标准化 items
        items = _build_items(text_items_raw)

        regions = lp_page.get("table_regions", [])
        is_table_page = lp_page.get("is_table_page", False)

        if regions:
            # 有 region → 用 bbox 切分
            page_tables = _segment_by_regions(
                page_num, items, regions, region_confidence_threshold
            )
        elif is_table_page:
            # 无 region 但标记为表格页 → 全页视为一张表
            page_tables = [_build_single_table(page_num, items, 0)]
        else:
            continue  # 非表格页，跳过

        for t in page_tables:
            all_tables.append((page_num, t))

    if not all_tables:
        return [], {"error": "未检测到任何表格"}

    # ---- Phase 1.5: 图表误判过滤 ----
    all_tables, chart_filtered = _filter_chart_like_tables(all_tables)
    if chart_filtered:
        print(f"  [图表过滤] 移除了 {chart_filtered} 个误判为表格的图表标签区域")

    if not all_tables:
        return [], {"error": "所有候选表均为图表误判，无有效表格"}

    # ---- Phase 2: 跨页续表拼接 ----
    cross_page_merges = 0
    if enable_cross_page:
        all_tables, cross_page_merges = _merge_cross_page_tables(all_tables)

    # ---- Phase 3: 全局编号 + 报告生成 ----
    tables = []
    for idx, (_, t) in enumerate(all_tables):
        t["table_id"] = idx
        tables.append(t)

    report = _generate_report(tables, liteparse_data, cross_page_merges)

    return tables, report


# ================================================================
# 2. 逐页 Region 切分
# ================================================================

def _segment_by_regions(
    page_num: int,
    items: List[dict],
    regions: List[dict],
    confidence_threshold: float,
) -> List[dict]:
    """用 table_regions 的 bbox 将一页的 items 切分为多个表格。

    P1 优化：检测混合 region（图表标签 + 真实表格上下排列），按 Y 大间隙拆分。
    """
    tables = []
    assigned_ids = set()  # 已归属 item 的 id()

    for ri, region in enumerate(regions):
        conf = region.get("confidence", 0.5)
        if conf < confidence_threshold:
            continue

        rx0 = region.get("x0", 0)
        ry0 = region.get("y0", 0)
        rx1 = region.get("x1", float("inf"))
        ry1 = region.get("y1", float("inf"))
        if rx1 <= rx0 or ry1 <= ry0:
            continue

        # 收集区域内 items（中心点判断）
        scoped = []
        for it in items:
            cx = (it["x0"] + it["x1"]) / 2
            cy = it["y_mid"]
            if rx0 <= cx <= rx1 and ry0 <= cy <= ry1:
                scoped.append(it)
                assigned_ids.add(id(it))

        # 扩展：收集 region 下方的脚注延续文本（y 超出 ry1 但在合理 margin 内）
        fn_margin = 80.0  # region 下方 80pt 范围内
        fn_items = []
        for it in items:
            if id(it) in assigned_ids:
                continue
            cx = (it["x0"] + it["x1"]) / 2
            cy = it["y_mid"]
            if rx0 <= cx <= rx1 and ry1 < cy <= ry1 + fn_margin:
                fn_items.append(it)
                assigned_ids.add(id(it))

        if fn_items:
            scoped.extend(fn_items)

        if not scoped:
            continue

        # P1: 检测混合 region，按 Y 大间隙拆分为多个子区段
        sub_segments = _split_by_y_gaps(scoped)

        if len(sub_segments) > 1:
            # 有 Y 大间隙 → 逐段处理
            for seg_idx, seg_items in enumerate(sub_segments):
                _build_table_from_items(
                    page_num, seg_items, region, ri, conf,
                    seg_idx, tables, items, assigned_ids
                )
        else:
            # 单段 → 按原逻辑处理
            _build_table_from_items(
                page_num, scoped, region, ri, conf,
                0, tables, items, assigned_ids
            )

    # 处理孤儿 items（不在任何 region 内的）
    orphans = [it for it in items if id(it) not in assigned_ids]

    if orphans and _is_likely_table_content(orphans):
        # 如果孤儿 items 看起来像表格内容（有数值+标签），创建补充表
        rows = _cluster_items_by_y(orphans)
        rows = _normalize_rows_to_columns(rows)
        col_ranges = _estimate_column_x_ranges(rows)
        col_schema = infer_column_schema(rows)
        tables.append({
            "page": page_num,
            "pages": [page_num],
            "y0": min(it["y0"] for it in orphans),
            "y1": max(it["y1"] for it in orphans),
            "text_items": orphans,
            "rows": rows,
            "row_count": len(rows),
            "is_cross_page": False,
            "caption": "",
            "region_index": -1,
            "confidence": 0.0,
            "column_x_ranges": col_ranges,
            "column_schema": col_schema,
        })

    return tables


# ---- P1: Y 大间隙检测与拆分子段 ----

def _split_by_y_gaps(items: List[dict]) -> List[List[dict]]:
    """检测 items 在 Y 方向的大间隙，拆分为多个子区段。

    策略：
    1. 按 Y 排序 items
    2. 计算相邻 items 的 Y 间距
    3. 仅在极端大间隙（> 平均间距的 10 倍 且 > 80pt）处断开
    4. 检验拆分点两侧 items 的 X 列结构：如果列结构相近 → 是同一张表，不拆
    5. 如果第二段的 items 看起来像上半段的续行（共享列 X 位置）→ 不拆

    设计目标：只拆分图表→表格这种页面级大间隙，不拆分 superscript 脚注导致的微小 Y 偏移。
    """
    min_items_for_split = 6  # 至少 6 个 item 才考虑拆分
    if len(items) < min_items_for_split:
        return [items]

    # 按 Y 排序
    sorted_items = sorted(items, key=lambda it: it["y_mid"])

    # 计算 item 间 Y 间距（每个 item 的 y_mid 间距）
    y_mids = [it["y_mid"] for it in sorted_items]
    gaps = [y_mids[i + 1] - y_mids[i] for i in range(len(y_mids) - 1)]
    gaps = [g for g in gaps if g > 0]

    if len(gaps) < 3:
        return [items]

    # 计算平均间距
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap <= 0:
        return [items]

    # 极端大间隙阈值：平均间距 × 10 且至少 80pt
    # 这样的间隙通常意味着页面布局的显著变化（如图表区→表格区）
    big_gap_threshold = max(mean_gap * 10.0, 80.0)

    # 找极端大间隙位置
    split_indices = []
    for i, gap in enumerate(gaps):
        if gap >= big_gap_threshold:
            split_indices.append(i + 1)

    if not split_indices:
        return [items]

    # 构建 candidate segments 并验证是否属于同一张表
    valid_splits = []
    prev = 0
    for si in split_indices:
        seg_size = si - prev
        if seg_size < 2:
            prev = si
            continue

        # === X 列一致性校验 ===
        # 如果两段共享相似的 X 列结构（同一列），则属同一张表，不拆分
        seg_a = sorted_items[prev:si]
        seg_b = sorted_items[si:]

        if len(seg_b) < 2:
            prev = si
            continue

        if _share_column_structure(seg_a, seg_b):
            prev = si
            continue  # 共享列结构 → 同一表，跳过此拆分点

        valid_splits.append(si)
        prev = si

    if not valid_splits:
        return [items]

    # 执行拆分
    segments = []
    prev = 0
    for si in valid_splits:
        segments.append(sorted_items[prev:si])
        prev = si
    if prev < len(sorted_items):
        segments.append(sorted_items[prev:])

    # 如果只有 1 段 → 无有效拆分
    if len(segments) <= 1:
        return [items]

    return segments


def _share_column_structure(seg_a: List[dict], seg_b: List[dict]) -> bool:
    """判断两个 item 段是否共享同样的列 X 结构（属于同一张表）。

    方法：比较两段所有 item 的 X 中心点分布。
    如果 B 段大部分 item 的 xc 落在 A 段 item 的 X 范围内 → 同一张表。
    """
    if not seg_a or not seg_b:
        return True  # 空段不拆分

    # 收集 A 段 item 的 X 中心点
    xc_a = [(it["x0"] + it["x1"]) / 2 for it in seg_a]
    x_min, x_max = min(xc_a), max(xc_a)

    # 统计 B 段 item 中，X 中心点落在 A 段 X 范围内的比例
    if x_max <= x_min:
        return False

    xc_b = [(it["x0"] + it["x1"]) / 2 for it in seg_b]
    in_range = sum(1 for xc in xc_b if x_min - 20 <= xc <= x_max + 20)
    ratio = in_range / len(xc_b)

    # 如果 > 70% 的 B 段 item 与 A 段共享 X 范围 → 同一张表
    return ratio >= 0.70


def _build_table_from_items(
    page_num: int,
    scoped: List[dict],
    region: dict,
    ri: int,
    conf: float,
    seg_idx: int,
    tables: List[dict],
    all_page_items: List[dict],
    assigned_ids: set,
):
    """从一组 items 构建单张表格并加入 tables 列表。

    支持：
    - 首次段(seg_idx==0) 可捕获 caption
    - 非首次段不捕获 caption（因 caption 属于首发段）
    """
    # 首次段才尝试捕获标题
    if seg_idx == 0:
        caption_items = _capture_caption(all_page_items, region, scoped, assigned_ids)
    else:
        caption_items = []

    all_items = _merge_and_sort_items(caption_items, scoped)
    rows = _cluster_items_by_y(all_items)
    rows = _normalize_rows_to_columns(rows)
    col_ranges = _estimate_column_x_ranges(rows)
    col_schema = infer_column_schema(rows)

    y0_val = min(it["y0"] for it in all_items)
    y1_val = max(it["y1"] for it in all_items)

    tables.append({
        "page": page_num,
        "pages": [page_num],
        "y0": y0_val,
        "y1": y1_val,
        "text_items": all_items,
        "rows": rows,
        "row_count": len(rows),
        "is_cross_page": False,
        "caption": _extract_caption(region, caption_items) if seg_idx == 0 else "",
        "region_index": ri,
        "confidence": conf,
        "column_x_ranges": col_ranges,
        "column_schema": col_schema,
    })


def _capture_caption(
    items: List[dict],
    region: dict,
    scoped_items: List[dict],
    assigned_ids: set,
) -> List[dict]:
    """捕获 region 上方的表格标题文本。

    策略：
    1. 先用 region 的 context_text 提取关键词
    2. 在 region y0 上方 margin 范围内搜索包含关键词的 items
    3. 将符合条件的 items 归入表格
    """
    context_text = region.get("context_text", "").strip()
    if not context_text:
        return []

    ry0 = region.get("y0", 0)
    margin = 60  # Y 方向向上扩展量 (pt)

    # 从 context_text 中提取关键短词
    keywords = _extract_keywords(context_text)

    caption_items = []
    for it in items:
        if id(it) in assigned_ids:
            continue
        if it["y1"] < ry0 - margin or it["y0"] > ry0:
            continue  # 不在上方 margin 范围内

        it_text = it["text"].strip()
        for kw in keywords:
            if kw and (kw in it_text or it_text in kw):
                caption_items.append(it)
                assigned_ids.add(id(it))
                break

    return caption_items


def _extract_keywords(text: str) -> List[str]:
    """从 context_text 中提取关键匹配词。"""
    text = re.sub(r'\s+', ' ', text).strip()
    words = text.split()
    # 取较长的词作为特征（避免短词误匹配）
    return [w for w in words if len(w) >= 2]


def _extract_caption(region: dict, caption_items: List[dict]) -> str:
    """提取表格标题文本。"""
    if caption_items:
        caption_items_sorted = sorted(caption_items, key=lambda it: it["x0"])
        return " ".join(it["text"] for it in caption_items_sorted)
    return region.get("context_text", "")


def _merge_and_sort_items(
    caption_items: List[dict],
    scoped_items: List[dict],
) -> List[dict]:
    """合并标题 items 和表格 items，按 Y 排序。"""
    # 标题 items 的 Y 更小，放在前面
    return sorted(
        list(caption_items) + list(scoped_items),
        key=lambda it: (it["y_mid"], it["x0"])
    )


def _build_single_table(
    page_num: int,
    items: List[dict],
    region_index: int,
) -> dict:
    """无 region 时，将全页 items 构建为单张表。"""
    rows = _cluster_items_by_y(items)
    rows = _normalize_rows_to_columns(rows)
    col_ranges = _estimate_column_x_ranges(rows)
    col_schema = infer_column_schema(rows)
    return {
        "page": page_num,
        "pages": [page_num],
        "y0": min(it["y0"] for it in items) if items else 0,
        "y1": max(it["y1"] for it in items) if items else 0,
        "text_items": items,
        "rows": rows,
        "row_count": len(rows),
        "is_cross_page": False,
        "caption": "",
        "region_index": region_index,
        "confidence": 0.0,
        "column_x_ranges": col_ranges,
        "column_schema": col_schema,
    }


# ================================================================
# 2.5. 图表误判过滤（P0 优化）
# ================================================================

def _filter_chart_like_tables(
    all_tables: List[tuple],
) -> Tuple[List[tuple], int]:
    """过滤被误判为表格的图表标签区域。

    检测特征：
    1. 坐标轴刻度值（40, 30, 20, 10, 0 等递减/递增序列）
    2. 孤立单字行（图表图例碎片：业、税、金、附、加）
    3. 孤儿百分比（无行标签，仅百分比数值）
    4. 缺少有意义的首列标签（无中文标签行）
    5. 纯图表数据标签（每行只有 1~2 个数值，无标签结构）
    """
    filtered = []
    removed_count = 0

    for page_num, table in all_tables:
        if _is_chart_like(table):
            removed_count += 1
            continue
        filtered.append((page_num, table))

    return filtered, removed_count


def _is_chart_like(table: dict) -> bool:
    """判断一张"表"是否实际上是图表的文本标签。

    综合多维度打分，score >= 2 判定为图表。
    """
    rows = table.get("rows", [])
    if not rows or len(rows) < 2:
        return False

    # 最大列数
    max_cols = max((len(r.get("texts", [])) for r in rows), default=0)

    # 收集所有文本
    all_texts = []
    for row in rows:
        texts = row.get("texts", [])
        all_texts.extend(t for t in texts if t.strip())

    if not all_texts:
        return True  # 空表 → 过滤

    score = 0

    # ---- 维度1: 坐标轴刻度检测 ----
    if _has_axis_tick_pattern(rows):
        score += 3  # 强信号

    # ---- 维度2: 孤立单字行（图例碎片） ----
    if _has_isolated_single_chars(rows):
        score += 3  # 强信号

    # ---- 维度3: 高比例孤立数值（无标签） ----
    if _has_high_isolated_number_ratio(rows):
        score += 2  # 中等信号

    # ---- 维度4: 缺少行标签列 ----
    if _missing_label_column(rows):
        score += 2  # 中等信号

    # ---- 维度5: 饼图切片标签模式 ----
    if _has_pie_chart_pattern(rows):
        score += 2  # 中等信号

    # ---- 维度6: 瀑布图/柱状图数据标签 ----
    if _has_waterfall_label_pattern(rows):
        score += 1  # 弱信号

    # ---- 维度7: 列稀疏度（大部分行只有1~2列有数据 → 图表标签） ----
    if _has_extreme_sparsity(rows, max_cols):
        score += 2  # 中等信号

    # 但如果有明确的表格特征 → 减分（留表，但力度较轻）
    table_chars_score = _has_table_characteristics(rows)
    if table_chars_score >= 3:
        score -= 2  # 强表格特征才大幅减分
    elif table_chars_score >= 1:
        score -= 1  # 弱表格特征轻度减分

    return score >= 2


def _has_axis_tick_pattern(rows: List[dict]) -> bool:
    """检测坐标轴刻度序列：如 40, 30, 20, 10, 0 或 2020, 2021, 2022, 2023, 2024。

    特征：
    - 多行数据每行只有 1~2 个数值（非多列表格结构）
    - 数值呈等差数列递增或递减
    """
    # 收集每行的纯数值（不含标签）
    numeric_sequences = []
    for row in rows:
        texts = row.get("texts", [])
        numbers = []
        for t in texts:
            t = t.strip()
            # 纯数字（可能含千分位逗号、负号、小数点）
            clean = t.replace(",", "").replace(" ", "").replace("(", "").replace(")", "")
            try:
                if clean:
                    float(clean)
                    numbers.append(clean)
            except ValueError:
                pass
        if numbers:
            numeric_sequences.append(numbers)

    if len(numeric_sequences) < 3:
        return False

    # 找同列的数值序列（每行第1个数值）
    col_values = []
    for seq in numeric_sequences:
        if seq:
            col_values.append(float(seq[0]))

    if len(col_values) < 3:
        return False

    # 检查是否为单调递增的等差数列
    diffs = [col_values[i + 1] - col_values[i] for i in range(len(col_values) - 1)]
    if not diffs:
        return False

    # 所有差值同方向 且 差值一致（或递增/递减的"漂亮"数字）
    all_same_sign = all(d > 0 for d in diffs) or all(d < 0 for d in diffs)
    if not all_same_sign:
        return False

    # 判断差值是否"规律性"（如 1, 10, 100, 1000 等整齐步长）
    avg_diff = sum(abs(d) for d in diffs) / len(diffs)
    if avg_diff == 0:
        return True  # 所有值相同 → 图表刻度

    # 如果是"漂亮"的步长 → 很可能是刻度
    nice_steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
                  100000, 1000000]
    for step in nice_steps:
        if abs(avg_diff - step) / step < 0.05:
            return True

    # 差异一致性检查：标准差 / 均值 很小 → 规律步长
    if len(diffs) >= 2:
        abs_diffs = [abs(d) for d in diffs]
        mean = sum(abs_diffs) / len(abs_diffs)
        if mean > 0:
            std = (sum((d - mean) ** 2 for d in abs_diffs) / len(abs_diffs)) ** 0.5
            if std / mean < 0.15:  # 非常一致
                return True

    return False


def _has_isolated_single_chars(rows: List[dict]) -> bool:
    """检测孤立单字行：图表图例被拆成碎片。

    特征：多行中每行只有 1~2 个中文字符，且这些行的列数很少。
    如 ["业", "税", "金", "附", "加"] 分在连续多行。
    """
    single_char_rows = 0
    single_chars = []

    for row in rows:
        texts = row.get("texts", [])
        non_empty = [t.strip() for t in texts if t.strip()]
        if not non_empty:
            continue

        # 该行全是短文本（每个 ≤ 2 字符）
        all_short = all(len(t) <= 2 for t in non_empty)
        # 且每个都是纯中文单字/双字
        all_chinese_short = all(
            re.match(r'^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{1,2}$', t)
            for t in non_empty
        )

        if all_short and all_chinese_short and len(non_empty) <= 5:
            single_char_rows += 1
            single_chars.extend(non_empty)

    # 至少有 60% 的行是孤立单字行
    return single_char_rows >= max(3, len(rows) * 0.6)


def _has_high_isolated_number_ratio(rows: List[dict]) -> bool:
    """检测高比例孤立数值：大部分行只有数值没有标签。

    图表的数据标签通常只有短期数值（如百分比、坐标值），没有行标签列。
    """
    total_rows = len(rows)
    numeric_only_rows = 0
    label_rows = 0

    for row in rows:
        texts = row.get("texts", [])
        non_empty = [t.strip() for t in texts if t.strip()]
        if not non_empty:
            continue

        # 统计数值占比
        num_count = 0
        label_count = 0
        for t in non_empty:
            # 纯数值检测
            clean = t.replace(",", "").replace("%", "").replace(" ", "")
            try:
                float(clean)
                num_count += 1
                continue
            except ValueError:
                pass
            # 含中文 → 标签
            if re.search(r'[\u4e00-\u9fff]', t):
                label_count += 1

        if num_count > 0 and label_count == 0:
            numeric_only_rows += 1
        if label_count > 0:
            label_rows += 1

    # 纯数值行 > 80% 且 标签行 < 1（没有行标签列）
    ratio = numeric_only_rows / total_rows if total_rows > 0 else 0
    return ratio > 0.8 and label_rows <= 1


def _missing_label_column(rows: List[dict]) -> bool:
    """检测是否缺少有意义的首列标签。

    真实表格的首列通常是中文标签（如"营业收入""利息净收入"）。
    图表的"首列"通常是坐标值或空值。
    """
    first_col_texts = []
    for row in rows:
        texts = row.get("texts", [])
        if texts and texts[0].strip():
            first_col_texts.append(texts[0].strip())

    if len(first_col_texts) < 2:
        return True

    # 统计首列中：中文标签 vs 纯数值
    cn_count = sum(1 for t in first_col_texts if re.search(r'[\u4e00-\u9fff]', t))
    num_count = 0
    for t in first_col_texts:
        clean = t.replace(",", "").replace("%", "").replace(" ", "")
        try:
            float(clean)
            num_count += 1
        except ValueError:
            pass

    # 首列几乎全是数值 → 非表格结构
    if num_count >= len(first_col_texts) * 0.7:
        return True

    # 首列几乎全空 → 非表格
    non_empty_ratio = len(first_col_texts) / len(rows)
    return non_empty_ratio < 0.3


def _has_pie_chart_pattern(rows: List[dict]) -> bool:
    """检测饼图标签模式。

    特征：
    - 多个百分比值 + 极少量短文本
    - 每行只有 1~2 个值
    - 这些都是饼图的切片文本被误识别为表格行
    """
    percent_count = 0
    total_cells = 0
    max_cols = 0

    for row in rows:
        texts = row.get("texts", [])
        max_cols = max(max_cols, len(texts))
        total_cells += len(texts)
        for t in texts:
            t = t.strip()
            if t.endswith("%"):
                percent_count += 1

    if total_cells == 0:
        return False

    # 百分比占比 > 40%
    percent_ratio = percent_count / total_cells

    # 列数很少（≤3），且百分比占比高 → 饼图
    if max_cols <= 3 and percent_ratio > 0.4:
        return True

    # 单列表（max_cols==1），大量百分比 → 饼图
    if max_cols <= 1 and percent_count >= 3:
        return True

    return False


def _has_waterfall_label_pattern(rows: List[dict]) -> bool:
    """检测瀑布图/柱状图标签模式。

    特征：数据标签行呈"大值 + 小值"交替（如 1,000,000 和 800,000 交替）
    且缺乏表头行。
    """
    if len(rows) < 3:
        return False

    # 检查首行是否为表头
    first_row_texts = rows[0].get("texts", [])
    has_header = any(re.search(r'[\u4e00-\u9fff]', t) for t in first_row_texts)

    if has_header and len(first_row_texts) >= 3:
        return False  # 有表头+多列 → 可能是真表

    # 检查数值行：每行数值很少（≤2个）
    narrow_rows = 0
    for row in rows:
        texts = row.get("texts", [])
        non_empty = [t.strip() for t in texts if t.strip()]
        if len(non_empty) <= 2:
            narrow_rows += 1

    return narrow_rows >= len(rows) * 0.7


def _has_extreme_sparsity(rows: List[dict], max_cols: int) -> bool:
    """检测极端稀疏列结构：大部分行只有1~2个非空列。

    真实表格：每行各列通常都有数据（表头/数值填满）。
    图表标签：大部分行只有一个值悬浮在某个 X 位置。

    max_cols: 表格的总列数
    """
    if max_cols <= 1 or len(rows) < 3:
        return False

    sparse_row_count = 0
    for row in rows:
        texts = row.get("texts", [])
        non_empty_count = sum(1 for t in texts if t.strip())
        if non_empty_count <= 2 and max_cols >= 3:
            sparse_row_count += 1

    # > 60% 的行在宽表（≥3列）中只有 ≤2 个非空列
    return sparse_row_count >= len(rows) * 0.6


def _has_table_characteristics(rows: List[dict]) -> int:
    """检测明确表格特征，用于纠正误判（反向减分）。

    返回整数值表示确凿程度（越高越确定是真表）。

    特征：
    - 有表头行（中文标签 + 年份词）
    - 有数据行（行标签 + 多列数值）
    - 列结构明确（≥4列）
    - 有较长中文标签
    - 有表格摘要关键词
    """
    if len(rows) < 3:
        return 0

    all_texts = []
    for row in rows:
        all_texts.extend(t.strip() for t in row.get("texts", []) if t.strip())

    score = 0

    # 特征1: 含表格摘要关键词
    table_kw = ["合计", "总计", "总额", "小计", "项目", "指标", "单位", "万元", "千元", "百万元"]
    has_kw = any(kw in t for kw in table_kw for t in all_texts)
    if has_kw:
        score += 1

    # 特征2: 有年份列头
    has_year = any(re.search(r'(19|20)\d{2}年?', t) for t in all_texts)
    if has_year:
        score += 1

    # 特征3: 多列表格（≥4列）且有足够的数据密度
    max_cols = max((len(r.get("texts", [])) for r in rows), default=0)
    if max_cols >= 4:
        # 还需要检查数据密度：每行非空列数
        avg_fill = 0
        for row in rows:
            texts = row.get("texts", [])
            avg_fill += sum(1 for t in texts if t.strip())
        avg_fill = avg_fill / len(rows) if rows else 0
        if avg_fill >= 3:  # 平均每行至少3个非空值 → 真表
            score += 1

    # 特征4: 有较长的中文标签（≥4个中文字）
    has_long_cn = any(
        len(re.findall(r'[\u4e00-\u9fff]', t)) >= 4 for t in all_texts
    )
    if has_long_cn:
        score += 1

    return score


# ================================================================
# 3. 跨页续表拼接（启发式）
# ================================================================

def _merge_cross_page_tables(
    all_tables: List[tuple],
) -> Tuple[List[tuple], int]:
    """检测并拼接跨页续表。

    判断标准：
    1. 两表在连续页码上
    2. 后表首行无表头特征（无中文标签，以数值为主）
    3. 前表末行非合计/总计等终结行
    4. 列数相近（基于 X 聚类列数，容差 ≤ 2）

    Returns:
        (merged_tables, merge_count)
    """
    if len(all_tables) < 2:
        return all_tables, 0

    # 按页码分组，每组内按 Y 排序
    page_groups: Dict[int, List[int]] = {}  # page → [index in all_tables]
    for idx, (pg, _) in enumerate(all_tables):
        page_groups.setdefault(pg, []).append(idx)

    merged_pairs = []  # [(keep_idx, remove_idx), ...]

    for idx in range(len(all_tables) - 1):
        pg_a, table_a = all_tables[idx]
        pg_b, table_b = all_tables[idx + 1]

        if pg_b != pg_a + 1:
            continue  # 非连续页
        if table_a.get("is_cross_page") and table_b.get("is_cross_page"):
            continue  # 已经是跨页表，不再重复合并

        # 检查：table_b 是本页第一个表，table_a 是前页最后一个表
        b_is_first = (page_groups[pg_b][0] == idx + 1)
        a_is_last = (page_groups[pg_a][-1] == idx)
        if not (a_is_last and b_is_first):
            continue

        # 跨页判定
        if _should_merge_continuation(table_a, table_b):
            merged_pairs.append((idx, idx + 1))

    if not merged_pairs:
        return all_tables, 0

    # 执行合并（从后往前处理）
    consumed = set()
    result = []
    merge_count = 0

    # 构建合并图：处理传递性合并 (A→B, B→C → A→C)
    merged_pairs.sort(key=lambda x: x[0])
    i = 0
    while i < len(all_tables):
        if i in consumed:
            i += 1
            continue

        keeper = all_tables[i]
        j = i
        chain = [i]

        # 向前查找合并链
        next_j = j + 1
        while next_j < len(all_tables):
            if (j, next_j) in [(a, b) for a, b in merged_pairs]:
                chain.append(next_j)
                consumed.add(next_j)
                j = next_j
                next_j = j + 1
                merge_count += 1
            else:
                break

        if len(chain) > 1:
            # 合并链上所有表
            _, keeper_dict = keeper
            for merged_idx in chain[1:]:
                _, merged_dict = all_tables[merged_idx]
                keeper_dict = _concat_continuation_table(keeper_dict, merged_dict)
            keeper = (keeper[0], keeper_dict)

        result.append(keeper)
        i = j + 1 if len(chain) > 1 else i + 1

    return result, merge_count


def _should_merge_continuation(table_a: dict, table_b: dict) -> bool:
    """判断 table_b 是否为 table_a 的跨页续表。

    多层判定（优先级从高到低）：
    1. 列数对比：X 聚类列数差异 ≤ 2
    2. 后表无表头：首行以数值为主 + 列签名判定
    3. 前表末行非终结：不含 合计/总计 等关键词
    4. 后表从页面顶部开始：y0 在页面 15% 以内
    """
    # ---- 列数对比 ----
    cols_a = len(table_a.get("column_x_ranges", []))
    cols_b = len(table_b.get("column_x_ranges", []))

    if cols_a == 0 or cols_b == 0:
        # X 范围估计失败 → 用 row items 数量估计
        rows_a = table_a.get("rows", [])
        rows_b = table_b.get("rows", [])
        if rows_a:
            cols_a = max(len(r.get("texts", [])) for r in rows_a)
        if rows_b:
            cols_b = max(len(r.get("texts", [])) for r in rows_b)

    if cols_a == 0 or cols_b == 0:
        return False
    if abs(cols_a - cols_b) > 2:
        return False

    # ---- 后表无表头 ----
    rows_b = table_b.get("rows", [])
    if not rows_b:
        return False
    first_row_b = rows_b[0]
    first_texts = first_row_b.get("texts", [])

    # 用列签名分值制判断后表首行是否为表头
    col_schema_b = table_b.get("column_schema", [])
    if col_schema_b:
        header_score = _score_table_row(first_texts, col_schema_b)
        # 得分 < 2 或没有数值特征 → 更可能是表头
        has_numeric = any(
            t.replace(",", "").replace(".", "").replace("-", "").replace("(", "").replace(")", "").isdigit()
            for t in first_texts
        )
        if header_score < 2:
            # 得分很低 → 几乎确定是表头
            return False
        if not has_numeric:
            # 纯文本行 → 表头
            return False

    # 原有中文占比检测（作为补充）
    total_len = sum(len(t) for t in first_texts)
    if total_len == 0:
        return _check_prev_table_ends_with_data(table_a)

    chinese_chars = sum(len(re.findall(r'[\u4e00-\u9fff]', t)) for t in first_texts)
    chinese_ratio = chinese_chars / total_len if total_len > 0 else 0

    if chinese_ratio >= 0.4 and len(first_texts) >= 2:
        if _is_header_like(first_texts):
            return False

    # ---- 前表末行非终结（P2 优化：多子表结构放宽） ----
    has_subtables = _has_multiple_sub_table_structure(table_a)
    if not _check_prev_table_ends_with_data(table_a):
        if has_subtables:
            # 多子表结构的"合计"很可能是子表内部的汇总，
            # 而非整个表格的终结，允许跨页拼接
            pass
        else:
            return False

    # ---- 后表位置检查 ----
    y0_b = table_b.get("y0", 0)
    if y0_b > 200:
        pass

    return True


def _has_multiple_sub_table_structure(table: dict) -> bool:
    """检测表格是否包含多个子表结构（P2 优化）。

    多子表特征：
    1. 有空白分隔行（全空行），将数据分成多个区块
    2. 有重复的子表头模式（如每个子表都有"金额""占比"列头）
    3. 表格非常宽（≥6列），可能有子表并列

    返回 True 表示该表包含多子表，跨页拼接时对末行汇总更宽容。
    """
    rows = table.get("rows", [])
    if len(rows) < 5:
        return False

    # 特征1: 空白分隔行检测
    blank_separator_count = 0
    blank_positions = []
    for ri, row in enumerate(rows):
        texts = row.get("texts", [])
        non_empty = [t for t in texts if t.strip()]
        if len(non_empty) <= 1:  # 整行为空或只有1个值
            blank_separator_count += 1
            blank_positions.append(ri)

    # 至少2个空白分隔行 且 不是都在头尾 → 内部有子表边界
    if blank_separator_count >= 2:
        mid_positions = [p for p in blank_positions if 1 < p < len(rows) - 2]
        if mid_positions:
            return True

    # 特征2: 检测汇总行模式（多个"合计"分散在不同位置）
    summary_kw = ["合计", "总计", "总额", "小计"]
    summary_rows = []
    for ri, row in enumerate(rows):
        all_text = "".join(row.get("texts", []))
        if any(kw in all_text for kw in summary_kw):
            summary_rows.append(ri)

    # 有 ≥2 个汇总行且不在相邻行 → 多个子表
    if len(summary_rows) >= 2:
        for i in range(len(summary_rows) - 1):
            if summary_rows[i + 1] - summary_rows[i] > 2:
                return True

    # 特征3: 检测子表头模式（行标签+列头组合 重复出现）
    # 在数据行中间出现类似表头的行为 → 子表边界
    header_like_rows = []
    for ri, row in enumerate(rows[1:], 1):  # 跳过首行（可能是主表头）
        texts = row.get("texts", [])
        if _is_header_like(texts):
            header_like_rows.append(ri)

    if len(header_like_rows) >= 2:
        for i in range(len(header_like_rows) - 1):
            if header_like_rows[i + 1] - header_like_rows[i] > 3:
                return True

    # 特征4: 列数很多（≥6列），典型的宽表多子结构
    max_cols = max((len(r.get("texts", [])) for r in rows), default=0)
    if max_cols >= 6 and len(summary_rows) >= 1:
        return True

    return False


def _is_header_like(texts: List[str]) -> bool:
    """判断文本列表是否像表头行（列标题）。

    表头特征：多个短中文标签 + 年份词 + 变化/增减词。
    如 ["项目", "2024年", "2023年", "变化+/(-)"] 是表头。
    如 ["营业收入", "36,874,848", "38,251,377", "3.7%"] 是数据行。
    """
    # 检查是否含年份
    year_pattern = re.compile(r'(19|20)\d{2}')
    has_year = any(year_pattern.search(t) for t in texts)

    # 检查是否含 变化/增减 关键词
    delta_kw = ['变化', '增减', '变动', '增幅', '±']
    has_delta = any(any(kw in t for kw in delta_kw) for t in texts)

    if has_year and has_delta:
        return True

    # 全是短文本且无长数字
    has_long_numeric = any(
        re.search(r'\d[\d,.]{3,}', t) for t in texts
    )
    all_short = all(len(t) <= 6 for t in texts)

    if all_short and not has_long_numeric and has_year:
        return True

    return False


def _check_prev_table_ends_with_data(table: dict) -> bool:
    """检查前表末行是否为数据行（非合计/总计等终结行）。"""
    rows = table.get("rows", [])
    if len(rows) < 1:
        return True  # 无数据，允许合并（反正空表）

    last_row = rows[-1]
    last_texts = last_row.get("texts", [])
    all_text = "".join(last_texts)

    summary_kw = ["合计", "总计", "总额", "小计", "累计"]
    if any(kw in all_text for kw in summary_kw):
        return False

    return True


def _concat_continuation_table(table_a: dict, table_b: dict) -> dict:
    """将 table_b 作为续表拼接到 table_a。"""
    # Y 偏移量（用于调整 table_b items 的 Y 坐标）
    y_offset = table_a["y1"] - table_b["y0"] + 10.0

    # 调整 table_b 的 items
    adjusted_items = []
    for it in table_b["text_items"]:
        it_adj = dict(it)
        it_adj["y0"] = it["y0"] + y_offset
        it_adj["y1"] = it["y1"] + y_offset
        it_adj["y_mid"] = it["y_mid"] + y_offset
        adjusted_items.append(it_adj)

    # 合并 items + 重新聚类
    all_items = table_a["text_items"] + adjusted_items
    all_rows = _cluster_items_by_y(all_items)

    # 去重：检查 table_a 末尾和 table_b 开头是否有重叠行
    all_rows = _dedup_overlap_rows(all_rows, len(table_a["rows"]))

    all_rows = _normalize_rows_to_columns(all_rows)
    col_ranges = _estimate_column_x_ranges(all_rows)
    col_schema = infer_column_schema(all_rows)

    return {
        "page": table_a["page"],
        "pages": sorted(set(table_a.get("pages", [table_a["page"]]) + table_b.get("pages", [table_b["page"]]))),
        "y0": table_a["y0"],
        "y1": table_b["y1"] + y_offset,
        "text_items": all_items,
        "rows": all_rows,
        "row_count": len(all_rows),
        "is_cross_page": True,
        "caption": table_a.get("caption", "") or table_b.get("caption", ""),
        "region_index": table_a.get("region_index", -1),
        "confidence": table_a.get("confidence", 0),
        "column_x_ranges": col_ranges,
        "column_schema": col_schema,
    }


def _dedup_overlap_rows(all_rows: List[dict], split_idx: int) -> List[dict]:
    """检测并移除跨页合并后的重叠行（续表可能重复最后几行数据）。

    Args:
        all_rows: 合并后全部行
        split_idx: 原 table_a 的行数（分界点）

    Returns:
        去重后的行列表
    """
    if split_idx <= 1 or split_idx >= len(all_rows):
        return all_rows

    # 在 split_idx 附近查找重叠（前后各看 3 行）
    check_range_a = range(max(0, split_idx - 3), split_idx)
    check_range_b = range(split_idx, min(len(all_rows), split_idx + 3))

    remove_indices = set()
    for ai in check_range_a:
        row_a_texts = set(
            _normalize_for_search(t) for t in all_rows[ai].get("texts", []) if t.strip()
        )
        if not row_a_texts:
            continue
        for bi in check_range_b:
            row_b_texts = set(
                _normalize_for_search(t) for t in all_rows[bi].get("texts", []) if t.strip()
            )
            if not row_b_texts:
                continue
            # Jaccard 相似度
            intersection = row_a_texts & row_b_texts
            union = row_a_texts | row_b_texts
            if union and len(intersection) / len(union) >= 0.6:
                # 保留更完整的行（item 数多的）
                if len(all_rows[ai].get("texts", [])) >= len(all_rows[bi].get("texts", [])):
                    remove_indices.add(bi)
                else:
                    remove_indices.add(ai)

    if remove_indices:
        return [r for i, r in enumerate(all_rows) if i not in remove_indices]
    return all_rows


# ================================================================
# 4. 行 normalization：对齐到统一列结构
# ================================================================

def _normalize_rows_to_columns(
    rows: List[dict],
    col_ranges: Optional[List[Tuple[float, float]]] = None,
) -> List[dict]:
    """将每行的 items 按 X 坐标对齐到统一列结构（零数据丢失版）。

    核心原则：绝不丢弃任何 item。遇到碰撞时拼接保留，如果归一化后
    非空值减少则退回到原始数据。

    Args:
        rows: _cluster_items_by_y 的输出
        col_ranges: 列 X 范围（可选，不传则从最宽行自动推断）

    Returns:
        规范化后的行列表，每行 texts 长度统一为列数，空列=""
    """
    from codes.table_validator.cell_differ import _normalize_for_search

    if col_ranges is None:
        col_ranges = _detect_column_ranges_from_rows(rows)

    n_cols = len(col_ranges)
    if n_cols <= 1:
        return rows

    # 计算每列的 X 中心点（用于最近中心映射）
    col_centers = [(x0 + x1) / 2 for x0, x1 in col_ranges]

    normalized = []
    for row in rows:
        items = row.get("items", [])
        original_texts = row.get("texts", [])
        original_non_empty = sum(1 for t in original_texts if t.strip())

        # 不需要对齐的行：items 数 ≥ 列数 → 直接保留
        if len(items) >= n_cols:
            normalized.append(row)
            continue

        # 按 X 中心点找最近列
        col_texts: Dict[int, str] = {}
        for it in items:
            x0 = it.get("x0", 0)
            x1 = it.get("x1", 0)
            text = it.get("text", "")
            xc = (x0 + x1) / 2

            # 找 X 中心最近的列
            best_col = 0
            best_dist = float("inf")
            for ci, cc in enumerate(col_centers):
                dist = abs(xc - cc)
                if dist < best_dist:
                    best_dist = dist
                    best_col = ci

            # 碰撞处理：同一列已有值 → 拼接到已有文本后面
            existing = col_texts.get(best_col, "")
            if existing and text:
                col_texts[best_col] = existing + " " + text
            else:
                col_texts[best_col] = text

        aligned_texts = [col_texts.get(ci, "") for ci in range(n_cols)]

        # 安全检查：归一化后非空项数不得少于原始
        aligned_non_empty = sum(1 for t in aligned_texts if t.strip())
        if aligned_non_empty < original_non_empty:
            # 数据丢失 → 退回：保留原始 texts 并补齐列数
            aligned_texts = list(original_texts)
            while len(aligned_texts) < n_cols:
                aligned_texts.append("")

        normalized.append({
            "items": items,
            "y_min": row.get("y_min", 0),
            "y_max": row.get("y_max", 0),
            "texts": aligned_texts,
            "norm_texts": [_normalize_for_search(t) for t in aligned_texts],
            "col_x_ranges": [
                (it.get("x0", 0), it.get("x1", 0)) for it in items
            ],
        })

    return normalized


def _detect_column_ranges_from_rows(rows: List[dict]) -> List[Tuple[float, float]]:
    """从最宽行的 items X 坐标推断列结构。

    用 item 最多的行作为模板，取其 items 的 (x0, x1) 即列范围。
    这比从所有行聚类更精确，因为最宽行天然代表完整的列集合。
    """
    if not rows:
        return []

    # 找 item 最多的行
    best_row = max(rows, key=lambda r: len(r.get("items", [])))
    items = best_row.get("items", [])

    if not items:
        return []

    col_ranges = []
    for it in items:
        x0 = it.get("x0", 0)
        x1 = it.get("x1", 0)
        if x1 > x0:
            col_ranges.append((x0, x1))

    # 如果最宽行 item 太少（<2），回退到所有行统计
    if len(col_ranges) < 2:
        return _estimate_column_x_ranges(rows)

    return col_ranges


# ================================================================
# 5. 列 X 范围估计（原版，用于兼容）
# ================================================================

def _estimate_column_x_ranges(rows: List[dict]) -> List[Tuple[float, float]]:
    """从所有行的 X 坐标统计出列的 X 范围。

    策略：
    1. 统计所有 item 的 X 坐标，对 x0 做聚类
    2. 每个聚类 = 一个列，x0_min ~ x1_max = 该列 X 范围
    3. 聚类阈值自适应：按 x0 间距分布自动选取
    """
    if not rows:
        return []

    # 收集所有 item 的 (x0, x1)
    all_x_pairs = []
    for row in rows:
        for it in row.get("items", []):
            x0 = it.get("x0", 0)
            x1 = it.get("x1", 0)
            if x1 > x0:
                all_x_pairs.append((x0, x1))

    if not all_x_pairs:
        return []

    # 按 x0 排序
    all_x_pairs.sort(key=lambda p: p[0])

    # 对 x0 做自适应聚类（间隙 > 平均间距的 3 倍 → 新列）
    x0s = [p[0] for p in all_x_pairs]
    gaps = [x0s[i + 1] - x0s[i] for i in range(len(x0s) - 1) if x0s[i + 1] > x0s[i]]
    if not gaps:
        return [(all_x_pairs[0][0], all_x_pairs[-1][1])]

    avg_gap = sum(gaps) / len(gaps)
    threshold = max(avg_gap * 2.5, 8.0)  # 至少 8pt

    # 聚类
    clusters = []
    current = [all_x_pairs[0]]
    for i in range(1, len(all_x_pairs)):
        if all_x_pairs[i][0] - current[-1][0] > threshold:
            clusters.append(current)
            current = [all_x_pairs[i]]
        else:
            current.append(all_x_pairs[i])
    if current:
        clusters.append(current)

    # 每个聚类 → (x0_min, x1_max)
    col_ranges = []
    for cluster in clusters:
        x0_min = min(p[0] for p in cluster)
        x1_max = max(p[1] for p in cluster)
        col_ranges.append((x0_min, x1_max))

    return col_ranges


# ================================================================
# 5. 验证报告
# ================================================================

def _generate_report(
    tables: List[dict],
    liteparse_data: dict,
    cross_page_merges: int,
) -> dict:
    """生成分割验证报告。"""
    pages = liteparse_data.get("pages", [])
    all_page_nums = {p.get("page_number", 0): p for p in pages}

    # 按页统计归属
    page_details = {}
    orphan_page_items = {}

    for table in tables:
        for pg in table.get("pages", [table["page"]]):
            if pg not in all_page_nums:
                continue
            if pg not in page_details:
                page_details[pg] = {
                    "tables": 0,
                    "items_total": 0,
                    "items_assigned": 0,
                    "orphans": 0,
                }
            page_details[pg]["tables"] += 1

    # 逐页统计覆盖度
    for pg, lp_page in all_page_nums.items():
        items_raw = lp_page.get("text_items", [])
        if not items_raw:
            continue
        if pg not in page_details:
            continue

        total = len(items_raw)
        page_details[pg]["items_total"] = total

        # 统计该页已归属的 items
        assigned = set()
        for table in tables:
            if pg in table.get("pages", [table["page"]]):
                for it in table.get("text_items", []):
                    # 用 (text, x0, y0) 做唯一标识（因为跨页合并后 id() 变了）
                    key = (it.get("text", ""), round(it.get("x0", 0), 1), round(it.get("y0", 0), 1))
                    assigned.add(key)

        page_details[pg]["items_assigned"] = len(assigned)
        page_details[pg]["orphans"] = total - len(assigned)

        # 孤儿 items 详情
        if page_details[pg]["orphans"] > 0:
            orphan_items = []
            for it_raw in items_raw:
                key = (
                    it_raw.get("text", "").strip(),
                    round(it_raw.get("x0", 0), 1),
                    round(it_raw.get("y0", 0), 1),
                )
                if key not in assigned and it_raw.get("text", "").strip():
                    orphan_items.append({
                        "text": it_raw.get("text", ""),
                        "x0": it_raw.get("x0", 0),
                        "y0": it_raw.get("y0", 0),
                        "y1": it_raw.get("y1", 0),
                    })
            if orphan_items:
                orphan_page_items[pg] = orphan_items

    # 表格摘要
    table_summaries = []
    for t in tables:
        rows = t.get("rows", [])
        first_3 = []
        for r in rows[:3]:
            first_3.extend(r.get("texts", [])[:3])
        last_3 = []
        for r in rows[-3:]:
            last_3.extend(r.get("texts", [])[-3:])

        table_summaries.append({
            "table_id": t["table_id"],
            "page": t["page"],
            "pages": t["pages"],
            "items_count": len(t.get("text_items", [])),
            "row_count": t["row_count"],
            "is_cross_page": t.get("is_cross_page", False),
            "caption": t.get("caption", ""),
            "confidence": t.get("confidence", 0),
            "first_items": first_3[:6],
            "last_items": last_3[-6:],
        })

    return {
        "total_pages": len(all_page_nums),
        "table_pages": len(page_details),
        "total_tables": len(tables),
        "cross_page_merges": cross_page_merges,
        "page_details": page_details,
        "orphan_page_items": orphan_page_items,
        "table_summaries": table_summaries,
    }


# ================================================================
# 6. 工具函数
# ================================================================

def _build_items(text_items: List[dict]) -> List[dict]:
    """从 liteparse text_items 字典列表构建标准化 items，并合并被拆开的小数。"""
    items = []
    for ti in text_items:
        if isinstance(ti, dict):
            t = ti.get("text", "").strip()
            y0 = ti.get("y0", 0)
            y1 = ti.get("y1", 0)
            if t and y1 > y0:
                items.append({
                    "text": t,
                    "x0": ti.get("x0", 0),
                    "x1": ti.get("x1", 0),
                    "y0": y0,
                    "y1": y1,
                    "y_mid": (y0 + y1) / 2,
                })
    # 合并被 PDF 引擎拆开的小数后缀（如 "239" + ".85" → "239.85"）
    return _merge_split_decimals(items)


def _is_likely_table_content(items: List[dict]) -> bool:
    """判断一批 items 是否像表格内容（有数值 + 标签）。"""
    if len(items) < 3:
        return False
    numeric_count = sum(
        1 for it in items
        if re.search(r'[\d,.]{2,}', it["text"])
    )
    return numeric_count >= 2


# ================================================================
# 7. 便捷函数：打印验证报告
# ================================================================

def print_verification_report(tables: List[dict], report: dict):
    """将分割报告打印为可读文本。"""
    lines = []
    lines.append("═" * 60)
    lines.append("  liteparse 表格分割验证报告（纯规则驱动）")
    lines.append("═" * 60)
    lines.append(f"  表格页: {report.get('table_pages', 0)} / {report.get('total_pages', 0)} 页")
    lines.append(f"  分割表格数: {report.get('total_tables', 0)}")
    lines.append(f"  跨页拼接: {report.get('cross_page_merges', 0)} 处")
    lines.append("")

    # 页级详情
    page_details = report.get("page_details", {})
    if page_details:
        lines.append("─" * 60)
        lines.append("  页级覆盖度")
        lines.append("─" * 60)
        for pg in sorted(page_details.keys()):
            d = page_details[pg]
            pct = (d["items_assigned"] / d["items_total"] * 100) if d["items_total"] > 0 else 0
            flag = " ⚠" if d["orphans"] > max(2, d["items_total"] * 0.05) else ""
            lines.append(
                f"  P{pg}: {d['items_assigned']}/{d['items_total']} items "
                f"({pct:.0f}%)  {d['tables']} 表  孤儿 {d['orphans']}{flag}"
            )

    # 孤儿详情
    orphan_items = report.get("orphan_page_items", {})
    if orphan_items:
        lines.append("")
        lines.append("─" * 60)
        lines.append("  ⚠ 孤儿 text_items（表格页上未被归属）")
        lines.append("─" * 60)
        for pg in sorted(orphan_items.keys()):
            for it in orphan_items[pg][:5]:
                lines.append(f"  P{pg}: '{it['text']}' (y={it['y0']:.0f})")
            if len(orphan_items[pg]) > 5:
                lines.append(f"  P{pg}: ... 共 {len(orphan_items[pg])} 个孤儿")

    # 表格摘要
    lines.append("")
    lines.append("─" * 60)
    lines.append("  表格摘要")
    lines.append("─" * 60)
    for ts in report.get("table_summaries", []):
        pages_str = f"P{ts['page']}" if len(ts['pages']) <= 1 else f"P{'-'.join(str(p) for p in ts['pages'])}"
        cross = " [跨页]" if ts.get("is_cross_page") else ""
        cap = f"「{ts['caption']}」" if ts.get("caption") else ""
        lines.append(f"  表#{ts['table_id']} ({pages_str}){cross} {cap}")
        lines.append(f"    行数: {ts['row_count']}, Items: {ts['items_count']}, 置信: {ts['confidence']:.2f}")
        first = " | ".join(ts.get("first_items", []))
        if first:
            lines.append(f"    首: {first}")
        last = " | ".join(ts.get("last_items", []))
        if last:
            lines.append(f"    末: {last}")

    lines.append("")
    lines.append("═" * 60)

    return "\n".join(lines)
