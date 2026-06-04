# -*- coding: utf-8 -*-
"""
页码分配模块 — 为 docx 通道提取的表格分配正确的 PDF 页码。

独立于 processor.py，方便单独测试和优化。

方案（v4 - 滑动窗口上下文锚定 + 分段DP）：
  先通过滑动窗口内的上下文唯一匹配锁定有确定性的表，
  再用已锚定表作为硬边界分段执行 DP，大幅减少相邻页串位。

  流程：
    0. V2-Lite 扫描获取 region_map + 区域级上下文
    1. 预计算 n-gram 页级频率 → 识别页眉/页脚污染
    2. 滑动窗口上下文锚定（两层策略）：
       L1: 以 docx 页码为中心的窗口内 → 区域上下文唯一命中 → 锁定
       L2: 窗口内页级上下文 + n-gram 过滤 → 唯一命中 → 锁定
       校验：目标页有V2-Lite表格区域 + 锚定间不严重破坏单调性
    3. 按锚定表分段：未锚定表的 DP 候选页被相邻锚点约束
       例：T3→P8, T8→P15 → T4-T7 只搜索 P8-P15
    4. 逐段构建评分矩阵 + 逐段 DP
    5. 合理性验证 + 局部精修 + 拥挤修正（跳过锚定表）
    6. 单调性 + 页数边界约束

旧方案保留为 fallback（6阶段DP流水线）。
"""

import math
import re
import fitz
import logging
from pathlib import Path


# ============================================================
# DP 调试日志器
# ============================================================

def _get_dp_logger(context):
    """为当前 PDF 创建专用的 DP 调试日志器。

    日志写入 data/mid_cache/<pdf_name>/dp_debug.log，
    每次运行自动清空旧日志（mode='w'）。
    """
    logger = logging.getLogger(f"dp_{id(context)}")
    if logger.handlers:
        return logger  # 已经初始化过

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if context and hasattr(context, 'pdf_path') and context.pdf_path:
        pdf_stem = Path(context.pdf_path).stem[:100]
        for ch in ('\\', '/', ':', '*', '?', '"', '<', '>', '|'):
            pdf_stem = pdf_stem.replace(ch, '_')
        from codes.pdf_extractor.utils import get_pdf_cache_dir
        log_dir = get_pdf_cache_dir(context.pdf_path)
    else:
        from codes.pdf_extractor.utils import get_mid_data_dir
        log_dir = get_mid_data_dir() / "_dp_unknown"

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "dp_debug.log"

    fh = logging.FileHandler(str(log_path), mode='w', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    ))
    logger.addHandler(fh)

    logger.info("=== DP 调试日志 (v3 修正版全局DP) ===")
    if context and hasattr(context, 'pdf_path'):
        logger.info("PDF: %s", context.pdf_path)
    logger.info("日志文件: %s", log_path)
    return logger


# ============================================================
# 配置常量
# ============================================================

V2_CONFIG = {
    # 行分组
    "y_threshold_factor": 0.4,
    "y_threshold_min": 2.0,
    "y_threshold_max": 15.0,

    # 列检测
    "align_tolerance": 4.0,
    "gap_factor": 0.3,
    "gap_min": 10.0,

    # 表格区域
    "table_min_width_ratio": 0.3,
    "table_min_height": 20.0,
    "density_grid": 10,
    "density_threshold": 0.8,

    # 单元格分配
    "row_margin_factor": 0.2,

    # 置信度
    "confidence_col_weight": 0.35,
    "confidence_empty_weight": 0.25,
    "confidence_num_weight": 0.25,
    "confidence_line_bonus": 0.15,

    # 过滤
    "financial_keywords": [
        "万元", "元", "百万", "十亿", "%", "比率",
        "资产", "负债", "收入", "利润", "现金", "股东",
        "资本", "充足率", "率", "额", "数"
    ],
    "min_text_length": 50,

    # pdfplumber降级
    "pdfplumber_min_words": 20,
    "pdfplumber_min_row_words": 3,
}

# 评分权重
WEIGHT_CONTEXT = 0.50      # 上下文文本匹配
WEIGHT_DATA_FINGERPRINT = 0.30  # 数据指纹匹配
WEIGHT_POSITION_PRIOR = 0.20    # 位置先验

# DP 距离惩罚系数
DP_DISTANCE_ALPHA = 0.002

# 局部精修搜索窗口
LOCAL_REFINE_WINDOW = 5


# ============================================================
# 辅助工具函数
# ============================================================

def _merge_consecutive(indices):
    """合并连续整数索引为 [(start, end), ...]"""
    if not indices:
        return []
    indices = sorted(set(indices))
    ranges = []
    start = indices[0]
    end = indices[0]
    for i in indices[1:]:
        if i == end + 1:
            end = i
        else:
            ranges.append((start, end))
            start = i
            end = i
    ranges.append((start, end))
    return ranges


def _detect_table_region_by_text(words, page_width, page_height, cfg=None):
    """无框表格区域检测（文本密度法）。

    Args:
        words: fitz word 列表，每项含 x0/y0/x1/y1/text
        page_width, page_height: 页面尺寸
        cfg: 可选配置字典，默认使用 V2_CONFIG

    Returns:
        [(x0, y0, x1, y1), ...] 表格区域矩形列表
    """
    if cfg is None:
        cfg = V2_CONFIG

    if not words or len(words) < 20:
        return []

    grid_rows = cfg["density_grid"]
    grid_cols = cfg["density_grid"]
    cell_h = page_height / grid_rows
    cell_w = page_width / grid_cols

    density = [[0] * grid_cols for _ in range(grid_rows)]
    for w in words:
        col = int((w["x0"] + w["x1"]) / 2 / cell_w)
        row = int((w["y0"] + w["y1"]) / 2 / cell_h)
        if 0 <= row < grid_rows and 0 <= col < grid_cols:
            density[row][col] += 1

    row_density = [sum(density[r]) for r in range(grid_rows)]
    avg = sum(row_density) / max(len(row_density), 1)
    avg = max(avg, 3)

    table_row_indices = [
        r for r in range(grid_rows)
        if row_density[r] > avg * cfg["density_threshold"]
    ]

    if not table_row_indices:
        return []

    table_row_ranges = _merge_consecutive(table_row_indices)

    regions = []
    for start, end in table_row_ranges:
        y0 = start * cell_h
        y1 = (end + 1) * cell_h
        regions.append((0, y0, page_width, y1))

    return regions


def _char_ngrams(text, n=2):
    """字符级 n-gram 分词，解决中文无空格 split() 全段=1个 token 的问题。

    对中英文混合文本通用：
      - 中文："资产负债表" → bigram → {"资产", "产负", "负债", "债表"}
      - 英文："hello" → bigram → {"he", "el", "ll", "lo"}
      - 数字/符号：作为普通字符处理，与前后字组合

    Args:
        text: str
        n: n-gram 的 n（默认 2）

    Returns:
        set of str: n-gram token 集合（已 lower、去空格）
    """
    text = text.strip()
    if len(text) < n:
        return {text.lower()} if text else set()

    text = re.sub(r'\s+', ' ', text)
    result = set()
    for i in range(len(text) - n + 1):
        token = text[i:i + n]
        if not token.strip():
            continue
        result.add(token.lower())
    return result


# ============================================================
# V2-Lite 区域扫描
# ============================================================

def _scan_table_regions_lite(context, cfg=None):
    """V2-Lite: 轻量扫描所有页面的表格区域坐标。

    不重组表格数据，只记录每页检测到的表格区域位置和区域内文本。
    利用 context.get_words() 缓存，速度极快（<1秒/200页）。

    Args:
        context: PDFContext 实例
        cfg: 可选配置字典

    Returns:
        [{page: int(1-based), regions: [(x0, y0, x1, y1, region_text), ...]}]
    """
    if cfg is None:
        cfg = V2_CONFIG

    total = context.page_count
    result = []

    for pn in range(total):
        try:
            page = context.get_page(pn)
        except Exception:
            result.append({"page": pn + 1, "regions": []})
            continue

        page_rect = page.rect
        words = context.get_words(pn)

        if not words:
            result.append({"page": pn + 1, "regions": []})
            continue

        full_text = " ".join(w["text"] for w in words)
        if not any(kw in full_text for kw in cfg["financial_keywords"]):
            result.append({"page": pn + 1, "regions": []})
            continue
        if len(full_text) < cfg["min_text_length"]:
            result.append({"page": pn + 1, "regions": []})
            continue

        table_regions = _detect_table_region_by_text(
            words, page_rect.width, page_rect.height, cfg
        )

        regions_info = []
        region_contexts = []
        REGION_CTX_MARGIN = 100  # 表格上方区域级上下文提取的 margin（pt）

        for (rx0, ry0, rx1, ry1) in table_regions:
            # ---- 表格区域文字 ----
            region_words = [
                w for w in words
                if rx0 <= w["x0"] <= rx1 and ry0 <= w["y0"] <= ry1
                and w["text"].strip()
            ]
            region_text = " ".join(w["text"] for w in region_words)
            if not region_text:
                regions_info.append((rx0, ry0, rx1, ry1, ""))
                region_contexts.append("")
                continue
            regions_info.append((rx0, ry0, rx1, ry1, region_text))

            # ---- 区域级上下文：表格上方非表格区域的描述文字 ----
            ctx_y0 = max(0, ry0 - REGION_CTX_MARGIN)
            ctx_words = [
                w for w in words
                if ctx_y0 <= w["y0"] <= ry0
                and rx0 * 0.5 <= w["x0"] <= rx1 * 1.5  # 允许上下文横向略宽
                and w["text"].strip()
            ]
            # 排除落在任何表格区域内的词
            ctx_filtered = []
            for w in ctx_words:
                inside = False
                for (tx0, ty0, tx1, ty1) in table_regions:
                    if (tx0 * 0.9 <= w["x0"] <= tx1 * 1.1
                            and ty0 * 0.9 <= w["y0"] <= ty1 * 1.1):
                        inside = True
                        break
                if not inside:
                    ctx_filtered.append(w)
            ctx_filtered.sort(key=lambda w: (w["y0"], w["x0"]))
            region_ctx = " ".join(w["text"] for w in ctx_filtered)
            region_contexts.append(region_ctx)

        result.append({
            "page": pn + 1,
            "regions": regions_info,
            "region_contexts": region_contexts
        })

    total_regions = sum(len(r["regions"]) for r in result)
    pages_with_regions = sum(1 for r in result if r["regions"])
    print(
        f"  [V2-Lite] 扫描完成: {pages_with_regions}/{total} 页有表格, "
        f"共 {total_regions} 个区域"
    )
    return result


# ============================================================
# 数据指纹构建
# ============================================================

def _build_table_data_fingerprint(table_data):
    """从表格数据行（跳过表头）中提取唯一数据指纹。

    策略：
    1. 跳过前2行（通常是表头/子表头）
    2. 收集所有非空、长度≥3 的单元格值
    3. 优先取数值型（最唯一，如 12,345,678 几乎不可能在其他页重复）
    4. 补足文本型（≥5 字更有区分度）
    5. 最多 25 个指纹片段

    Args:
        table_data: list of list，表格数据

    Returns:
        set of str: 指纹片段集合（已 lower）
    """
    fingerprints = set()
    numeric_values = []
    text_values = []

    start_row = 2 if len(table_data) > 3 else 0

    for row in table_data[start_row:]:
        for cell in row:
            if not cell:
                continue
            s = str(cell).strip()
            if not s or len(s) < 3:
                continue

            cleaned = (
                s.replace(",", "").replace("%", "")
                .replace("\u2030", "").replace("(", "-").replace(")", "")
            )
            try:
                float(cleaned)
                numeric_values.append(s)
                continue
            except ValueError:
                pass

            if len(s) >= 5:
                text_values.append(s)

    seen = set()
    for v in numeric_values:
        if v not in seen:
            fingerprints.add(v.lower())
            seen.add(v)
        if len(fingerprints) >= 25:
            break

    if len(fingerprints) < 25:
        for v in text_values:
            if v not in seen:
                fingerprints.add(v.lower())
                seen.add(v)
            if len(fingerprints) >= 25:
                break

    return fingerprints


def _build_table_header_text(table_data):
    """从表格表头行（前2行或前1行）中提取文本，用作上下文替代。

    当 ctx_text 为空时，表头文本（如"资产负债表"、"主要财务指标"）
    可以和 PDF 页的非表格区域文字匹配，提供有区分度的信号。

    Args:
        table_data: list of list，表格数据

    Returns:
        str: 表头文本拼接（换行分隔），可能为空
    """
    if not table_data:
        return ""

    header_rows = table_data[:2] if len(table_data) > 3 else table_data[:1]
    lines = []
    for row in header_rows:
        cells = [str(c).strip() for c in row if c and str(c).strip()]
        if cells:
            lines.append(" ".join(cells))

    return "\n".join(lines)


# 常见数值黑名单（区分度极低的数值，在数据指纹匹配中降权）
_COMMON_NUMERIC_BLACKLIST = {
    "0", "0.0", "0.00", "1", "1.0", "1.00",
    "100", "100.0", "100.00", "100%",
    "10", "10.0", "50", "50.0",
}

# 模块级：指纹词在各候选页上的出现频率（预计算后使用）
_FP_PAGE_FREQ = {}

# 指纹频率阈值：出现在超过此数量的候选页上 → 区分度过低，大幅降权
FP_FREQ_THRESHOLD = 5


def _precompute_fp_page_freq(results, candidate_pages, full_page_texts):
    """预计算所有表格指纹词在各候选页上的出现次数。

    一个数值出现在超过 FP_FREQ_THRESHOLD 页说明它是通用数据（如0、100%），
    在相邻页区分上没有什么价值，应在评分中降权。

    结果存入模块级 _FP_PAGE_FREQ，供 _score_data_fingerprint 使用。
    """
    global _FP_PAGE_FREQ
    _FP_PAGE_FREQ.clear()

    for ti in range(len(results)):
        table_data = results[ti].get("data", [])
        fps = _build_table_data_fingerprint(table_data)
        for fp in fps:
            fp_key = fp.lower()
            if fp_key in _FP_PAGE_FREQ:
                continue  # 已计算过
            count = 0
            fp_stripped = str(fp).strip().replace(",", "")
            for pn in candidate_pages:
                pt = full_page_texts.get(pn, "").lower()
                # 用原始指纹和去逗号版本都尝试匹配
                if fp.lower() in pt or fp_stripped.lower() in pt:
                    count += 1
            _FP_PAGE_FREQ[fp_key] = count


# ============================================================
# 页面文本提取
# ============================================================

def _extract_page_context_texts(context, region_map):
    """提取每页的非表格区域文本，用于上下文文本匹配。

    对每一页：
      1. 获取该页所有 words
      2. 排除落在任何 V2-Lite 表格区域内的 words
      3. 将剩余 words 按阅读顺序拼接为页级上下文文本

    Args:
        context: PDFContext 实例
        region_map: _scan_table_regions_lite 的返回值
                    [{page: int, regions: [(x0,y0,x1,y1,text), ...]}, ...]

    Returns:
        dict: {page_num(1-based): context_text_str}
    """
    total = context.page_count
    page_regions = {}
    for info in region_map:
        pn = info["page"]
        rects = [(r[0], r[1], r[2], r[3]) for r in info["regions"]]
        page_regions[pn] = rects

    page_contexts = {}

    for pn in range(1, total + 1):
        try:
            words = context.get_words(pn - 1)
        except Exception:
            page_contexts[pn] = ""
            continue

        if not words:
            page_contexts[pn] = ""
            continue

        rects = page_regions.get(pn, [])

        filtered = []
        for w in words:
            wx0, wy0, wx1, wy1 = w["x0"], w["y0"], w["x1"], w["y1"]
            inside = False
            for (rx0, ry0, rx1, ry1) in rects:
                if (wx0 >= rx0 * 0.8 and wy0 >= ry0 * 0.8 and
                        wx1 <= rx1 * 1.2 and wy1 <= ry1 * 1.2):
                    inside = True
                    break
            if not inside and w["text"].strip():
                filtered.append(w)

        if not filtered:
            page_contexts[pn] = ""
            continue

        filtered.sort(key=lambda w: (w["y0"], w["x0"]))
        page_contexts[pn] = " ".join(w["text"] for w in filtered)

    return page_contexts


def _extract_full_page_texts(context):
    """提取每页的完整文本（含表格区域），用于表格数据指纹匹配。

    Args:
        context: PDFContext 实例

    Returns:
        dict: {page_num(1-based): full_text_str}
    """
    total = context.page_count
    full_texts = {}

    for pn in range(1, total + 1):
        try:
            words = context.get_words(pn - 1)
        except Exception:
            full_texts[pn] = ""
            continue

        if not words:
            full_texts[pn] = ""
            continue

        words_sorted = sorted(words, key=lambda w: (w["y0"], w["x0"]))
        full_texts[pn] = " ".join(w["text"] for w in words_sorted if w["text"].strip())

    return full_texts


# ============================================================
# 评分函数
# ============================================================

def _score_context_text(ctx_text, page_text, page_has_table_region=None):
    """增强版上下文评分，引入页面位置信号。

    page_has_table_region: 该页是否有 V2-Lite 检测到的表格区域
      - 有表格区域 + 标题精确匹配 → 高分（0.85~0.95）
      - 无表格区域 + 标题精确匹配 → 降分（0.40~0.50）
        （标题可能是页眉/目录/引用，不是该页的真实表格）
    """
    if not ctx_text or not ctx_text.strip():
        return 0.0
    if not page_text or not page_text.strip():
        return 0.0

    ctx_lines = [l.strip() for l in ctx_text.strip().splitlines() if l.strip()]
    page_lower = page_text.lower()

    # 精确匹配（3 字符起）
    best_exact_len = 0
    for line in ctx_lines:
        if len(line) >= 3 and line.lower() in page_lower:
            best_exact_len = max(best_exact_len, len(line))

    if best_exact_len >= 3:
        base = min(0.95, 0.7 + 0.03 * best_exact_len)
        # 位置信号：该页有表格区域 → 不降分；无表格区域 → 大幅降分
        if page_has_table_region is False:
            base *= 0.5
        return base

    # 模糊匹配（bigram Jaccard）
    ctx_tokens = _char_ngrams(ctx_text, 2)
    pt_tokens = _char_ngrams(page_text, 2)
    if not ctx_tokens or not pt_tokens:
        return 0.0
    common = ctx_tokens & pt_tokens
    if len(common) < 2:
        return 0.0
    jacc = len(common) / len(ctx_tokens | pt_tokens)
    if jacc < 0.08:
        return 0.0
    base = min(0.85, jacc + 0.02 * len(common))
    if page_has_table_region is False:
        base *= 0.5
    return base


def _build_region_context_index(region_map):
    """从增强版 region_map 构建区域级上下文索引。

    region_map 中每项新增了 'region_contexts' 字段（与 'regions' 列表等长），
    每个元素是表格区域上方的描述性文字（如章节标题、表格标题等）。

    Returns:
        dict: {page_num(1-based): [region_ctx_0, region_ctx_1, ...]}
    """
    index = {}
    for info in region_map:
        pn = info["page"]
        index[pn] = info.get("region_contexts", [])
    return index


def _score_context_text_region(ctx_text, region_contexts, has_region=None):
    """区域级上下文匹配：将 docx 表格上下文与每页各区域的上下文逐一匹配。

    取该页所有区域中最佳匹配得分。相比页级上下文匹配：
    - 页级：整页非表格文字混在一起 → 多个表格的上下文互相干扰
    - 区域级：每区域上下文独立匹配 → 能精确区分同页不同表格

    如果没有区域级上下文可用（region_contexts 为空），返回 0.0 让调用方 fallback。

    Args:
        ctx_text: docx 表格的上下文文字
        region_contexts: 该页所有区域的上下文列表（来自 _build_region_context_index）
        has_region: 该页是否有 V2-Lite 表格区域（可选）

    Returns:
        float: 最佳匹配得分 [0.0, 0.95]
    """
    if not ctx_text or not ctx_text.strip():
        return 0.0

    # 筛选有效的区域上下文（非空）
    valid_rcs = [rc for rc in region_contexts if rc and rc.strip()]
    if not valid_rcs:
        return 0.0

    ctx_lines = [l.strip() for l in ctx_text.strip().splitlines() if l.strip()]

    best_score = 0.0

    for rc in valid_rcs:
        rc_lower = rc.lower()

        # 精确匹配（3 字符起）
        best_exact_len = 0
        for line in ctx_lines:
            if len(line) >= 3 and line.lower() in rc_lower:
                best_exact_len = max(best_exact_len, len(line))

        if best_exact_len >= 3:
            base = min(0.95, 0.7 + 0.03 * best_exact_len)
            if has_region is False:
                base *= 0.5
            best_score = max(best_score, base)
            continue

        # 模糊匹配（bigram Jaccard）
        ctx_tokens = _char_ngrams(ctx_text, 2)
        rc_tokens = _char_ngrams(rc, 2)
        if not ctx_tokens or not rc_tokens:
            continue
        common = ctx_tokens & rc_tokens
        if len(common) < 2:
            continue
        jacc = len(common) / len(ctx_tokens | rc_tokens)
        if jacc < 0.08:
            continue
        base = min(0.85, jacc + 0.02 * len(common))
        if has_region is False:
            base *= 0.5
        best_score = max(best_score, base)

    return best_score


def _score_data_fingerprint(table_data, full_page_text):
    """表格数据指纹与完整页面文本的匹配评分。

    使用精确子串匹配 + 数值优先策略：
    1. 从表格数据行中提取数值型和文本型指纹词
    2. 数值型指纹词在页面文本中做精确子串匹配（区分度极高）
       - 常见数值（如0, 1, 100）降权：命中仅计0.3，未命中计-0.3
    3. 文本型指纹词用 bigram Jaccard 匹配
    4. 综合：数值命中比例 × 0.7 + 文本相似度 × 0.3
    """
    if not table_data or not full_page_text:
        return 0.0

    # 分离数值型和文本型指纹
    numeric_fps = []   # (value_lower, is_common)
    text_fps = []
    start_row = 2 if len(table_data) > 3 else 0

    for row in table_data[start_row:]:
        for cell in row:
            if not cell:
                continue
            s = str(cell).strip()
            if not s or len(s) < 3:
                continue
            cleaned = (
                s.replace(",", "").replace("%", "")
                .replace("\u2030", "").replace("(", "-").replace(")", "")
            )
            try:
                float(cleaned)
                is_common = cleaned in _COMMON_NUMERIC_BLACKLIST
                numeric_fps.append((s.lower(), is_common))
                continue
            except ValueError:
                pass
            if len(s) >= 4:
                text_fps.append(s.lower())

    if not numeric_fps and not text_fps:
        return 0.0

    page_lower = full_page_text.lower()
    total_score = 0.0

    # 数值型精确子串匹配 — 区分度最高
    # 常见数值降权：区分度低的数值命中不计高分，未命中则扣分
    if numeric_fps:
        unique_fps = [fp for fp, is_c in numeric_fps if not is_c][:20]
        common_fps = [fp for fp, is_c in numeric_fps if is_c][:5]

        # 独特数值匹配 — 按频率加权
        # 如果某个数值在太多页上都出现，说明区分度极低，命中也不给高分
        unique_hits = 0.0
        has_low_freq_fp = False  # 是否至少有一个低频（区分度高）的指纹词
        for fp in unique_fps:
            freq = _FP_PAGE_FREQ.get(fp, 1)
            if freq <= FP_FREQ_THRESHOLD:
                has_low_freq_fp = True
                if fp in page_lower:
                    unique_hits += 1.0  # 低频命中：全权重
            else:
                # 高频数值：即使命中也只给降权分
                if fp in page_lower:
                    unique_hits += 0.3
        unique_count = max(len(unique_fps), 1)
        unique_ratio = unique_hits / unique_count
        # 如果没有低频指纹词，说明所有数值都太通用，整体降权
        if not has_low_freq_fp:
            unique_ratio *= 0.5

        # 常见数值匹配（低权重，主要起排除作用）
        common_hits = 0
        for fp in common_fps:
            if fp in page_lower:
                common_hits += 1
        common_count = max(len(common_fps), 1)
        common_ratio = common_hits / common_count

        # 综合数值得分：独特数值为主，常见数值为辅
        if unique_fps:
            numeric_score = min(0.95, unique_ratio * 1.0 + common_ratio * 0.2)
        else:
            # 只有常见数值，低置信度
            numeric_score = min(0.30, common_ratio * 0.4)
        total_score += 0.7 * numeric_score

    # 文本型 bigram Jaccard 匹配
    if text_fps:
        fp_tokens = set()
        for t in text_fps[:10]:
            fp_tokens |= _char_ngrams(t, 2)
        pt_tokens = _char_ngrams(full_page_text, 2)
        if fp_tokens and pt_tokens:
            common = fp_tokens & pt_tokens
            if len(common) >= 2:
                jacc = len(common) / len(fp_tokens | pt_tokens)
                text_score = min(0.85, jacc + 0.01 * len(common))
                total_score += 0.3 * text_score
    elif numeric_fps:
        # 只有数值指纹时，数值占比提升到 1.0
        total_score = min(0.95, (total_score / 0.7))  # 重新归一化

    return min(0.95, total_score)


def _compute_position_prior(docx_page, pdf_page, total_pages):
    """位置先验：docx 原始页码与 PDF 页码的距离衰减。

    修正版：σ = max(5, total_pages * 0.03)，有效范围扩大到 25 页。
    """
    if docx_page <= 0 or pdf_page <= 0:
        return 0.0
    dist = abs(docx_page - pdf_page)
    if dist > 25:
        return 0.0
    if dist == 0:
        return 0.15
    sigma = max(5.0, total_pages * 0.03)
    return 0.15 * math.exp(-(dist ** 2) / (2 * sigma ** 2))


# ============================================================
# 上下文锚定：DP 之前先锁定有唯一上下文命中的表
# ============================================================

def _build_page_ngram_freq(page_contexts, n=4):
    """预计算页级文本中每个 n-gram 出现在哪些页中。

    用于识别页眉/页脚类高频文本：
    - 出现在 >3 页的 n-gram → 页眉/页脚污染，在锚定中不可信
    - 出现在 ≤3 页的 n-gram → 有区分度的内容，可信锚定信号

    Args:
        page_contexts: {page_num: context_text_str}
        n: n-gram 的 n（默认 4，中文 4 字有区分度）

    Returns:
        (ngram_pages, high_freq_ngrams)
        - ngram_pages: {ngram: set of page_nums}
        - high_freq_ngrams: set of ngrams appearing in >3 pages
    """
    from collections import defaultdict
    ngram_pages = defaultdict(set)

    for pn, text in page_contexts.items():
        if not text:
            continue
        text_clean = re.sub(r'\s+', '', text.lower())
        seen_in_page = set()
        for i in range(len(text_clean) - n + 1):
            ngram = text_clean[i:i + n]
            if ngram not in seen_in_page:
                ngram_pages[ngram].add(pn)
                seen_in_page.add(ngram)

    high_freq = {
        ng for ng, pages in ngram_pages.items()
        if len(pages) > 3
    }
    return ngram_pages, high_freq


def _text_has_distinctive_content(text, high_freq_ngrams, n=4, min_ratio=0.5):
    """检查文本中是否有足够多的区分性内容（非页眉/页脚）。

    页眉文本（如"某某股份有限公司年度报告"）几乎全部由高频 n-gram 组成，
    比例接近 1.0；有区分度的标题（如"应收利息情况表"）则大部分是低频 n-gram。

    Args:
        text: 待检查的文本
        high_freq_ngrams: 高频 n-gram 集合（页眉/页脚）
        n: n-gram 的 n
        min_ratio: 低频 n-gram 最低比例阈值

    Returns:
        (has_distinct_content, distinct_ratio)
    """
    if not text:
        return False, 0.0

    text_clean = re.sub(r'\s+', '', text.lower())
    if len(text_clean) < n:
        return False, 0.0

    total = 0
    distinct = 0
    for i in range(len(text_clean) - n + 1):
        ngram = text_clean[i:i + n]
        total += 1
        if ngram not in high_freq_ngrams:
            distinct += 1

    ratio = distinct / max(total, 1)
    return ratio >= min_ratio, ratio


# 上下文锚定搜索窗口（以 docx 原始页码为中心的 ±N 页）
ANCHOR_SEARCH_WINDOW = 8


def _find_unique_context_hit(ctx_text, table_data, region_context_index,
                              page_contexts, high_freq_ngrams, total_pages,
                              docx_page=0, search_window=8,
                              min_match_len_l1=4, min_match_len_l2=4):
    """在滑动窗口内为单个表查找上下文唯一命中的页码。

    滑动窗口策略（关键优化）：
    - 以 docx 原始页码为中心 ±search_window 页范围内搜索
    - 窗口内唯一命中 → 可信锚定（pdf2docx 偏差通常 ≤8页）
    - 全局搜索"唯一命中"不可靠：36页文档中任一3字短语恰好出现在
      某页区域上下文的概率不低，不能作为锚定依据

    两层匹配策略：
    Layer 1 — 区域级上下文（天然无页眉/页脚）：
        在窗口内 region_contexts 中搜索 ctx 关键行
    Layer 2 — 页级上下文（n-gram 频率过滤）：
        在窗口内 page_contexts 中搜索，同时过滤高频 n-gram

    Args:
        docx_page: docx 原始页码（0 表示未知，此时搜索全范围）
        search_window: 搜索窗口半径
        min_match_len_l1: L1 匹配最小字长
        min_match_len_l2: L2 匹配最小字长

    Returns:
        (layer, page_num) 如果窗口内唯一命中
        (None, None) 如果无法唯一确定
    """
    # 计算搜索范围
    if docx_page > 0:
        page_start = max(1, docx_page - search_window)
        page_end = min(total_pages, docx_page + search_window)
    else:
        page_start, page_end = 1, total_pages

    # 提取关键行（用 L1 最小字长）
    ctx_lines = []
    if ctx_text and ctx_text.strip():
        ctx_lines = [l.strip() for l in ctx_text.strip().splitlines()
                     if l.strip() and len(l.strip()) >= min_match_len_l1]

    # 上下文为空时用表头文本
    if not ctx_lines:
        header = _build_table_header_text(table_data)
        if header and header.strip():
            ctx_lines = [l.strip() for l in header.strip().splitlines()
                         if l.strip() and len(l.strip()) >= min_match_len_l1]

    if not ctx_lines:
        return None, None

    # ---- Layer 1: 区域级上下文（窗口内搜索） ----
    region_hits = set()
    for pn in range(page_start, page_end + 1):
        rcs = region_context_index.get(pn, [])
        for rc in rcs:
            if not rc or not rc.strip():
                continue
            rc_lower = rc.lower()
            for line in ctx_lines:
                if len(line) >= min_match_len_l1 and line.lower() in rc_lower:
                    region_hits.add(pn)
                    break
            else:
                continue
            break

    if len(region_hits) == 1:
        return 1, region_hits.pop()

    # ---- Layer 2: 页级上下文（窗口内 + n-gram 过滤） ----
    ctx_lines_l2 = [l for l in ctx_lines if len(l) >= min_match_len_l2]
    if not ctx_lines_l2:
        if ctx_text and ctx_text.strip():
            ctx_lines_l2 = [ctx_text.strip()]

    if not ctx_lines_l2:
        return None, None

    ctx_full = " ".join(ctx_lines_l2)
    has_dist, ratio = _text_has_distinctive_content(ctx_full, high_freq_ngrams)
    if not has_dist:
        return None, None

    page_hits = set()
    for pn in range(page_start, page_end + 1):
        pt = page_contexts.get(pn, "")
        if not pt:
            continue
        pt_lower = pt.lower()

        for line in ctx_lines_l2:
            if len(line) < min_match_len_l2:
                continue
            line_has_dist, _ = _text_has_distinctive_content(line, high_freq_ngrams)
            if not line_has_dist:
                continue
            if line.lower() in pt_lower:
                page_hits.add(pn)
                break

    if len(page_hits) == 1 and ratio >= 0.5:
        return 2, page_hits.pop()

    return None, None


# 锚定时匹配行最小长度（太短容易产生假阳性唯一命中）
MIN_ANCHOR_MATCH_LEN_L1 = 5   # L1 区域匹配最小字长
MIN_ANCHOR_MATCH_LEN_L2 = 4   # L2 页级匹配最小字长


def _context_anchor_pass(results, region_context_index, page_contexts,
                          page_has_region, high_freq_ngrams, total_pages, logger):
    """上下文锚定主过程：滑动窗口内找唯一上下文命中并锁定页码。

    核心优化：对每张表以 docx 原始页码为中心 ±ANCHOR_SEARCH_WINDOW 页
    的滑动窗口内搜索上下文唯一命中，而非全局搜索。

    校验：
    1. 目标页必须有 V2-Lite 表格区域
    2. 锚定结果之间不严重破坏单调性

    Returns:
        (anchored, remaining_indices)
    """
    num_tables = len(results)
    rejected_no_region = 0
    rejected_monotonic = 0

    # 第一遍：收集所有候选锚定（滑动窗口内搜索）
    candidates = {}
    for ti in range(num_tables):
        ctx_text = results[ti].get("context_text", "")
        table_data = results[ti].get("data", [])
        docx_page = results[ti].get("page", 0)

        layer, page = _find_unique_context_hit(
            ctx_text, table_data, region_context_index,
            page_contexts, high_freq_ngrams, total_pages,
            docx_page=docx_page,
            search_window=ANCHOR_SEARCH_WINDOW,
            min_match_len_l1=MIN_ANCHOR_MATCH_LEN_L1,
            min_match_len_l2=MIN_ANCHOR_MATCH_LEN_L2,
        )

        if layer is not None and page is not None:
            candidates[ti] = (layer, page, docx_page)

    # 第二遍：校验，过滤假阳性锚定
    anchored = {}
    layer1_count = 0
    layer2_count = 0

    for ti, (layer, page, docx_page) in sorted(candidates.items()):
        # 校验1：目标页必须有 V2-Lite 表格区域
        if not page_has_region.get(page, False):
            logger.info("  拒绝[L%d无区域]: 表%d → P%d (目标页无V2-Lite表格区域)",
                         layer, ti + 1, page)
            rejected_no_region += 1
            continue

        # 通过校验 → 接受锚定
        anchored[ti] = (layer, page)
        if layer == 1:
            layer1_count += 1
            logger.info("  锚定[L1区域]: 表%d → P%d (docx原始P%d, 窗口[%d,%d])",
                         ti + 1, page, docx_page,
                         max(1, docx_page - ANCHOR_SEARCH_WINDOW),
                         min(total_pages, docx_page + ANCHOR_SEARCH_WINDOW))
        else:
            layer2_count += 1
            logger.info("  锚定[L2页级]: 表%d → P%d (docx原始P%d, 窗口[%d,%d])",
                         ti + 1, page, docx_page,
                         max(1, docx_page - ANCHOR_SEARCH_WINDOW),
                         min(total_pages, docx_page + ANCHOR_SEARCH_WINDOW))

    # 校验2：已接受锚定表之间的单调性冲突（取消偏移更大的）
    anchored_tis = sorted(anchored.keys())
    violation_found = True
    while violation_found:
        violation_found = False
        for i in range(1, len(anchored_tis)):
            ti_prev = anchored_tis[i - 1]
            ti_cur = anchored_tis[i]
            prev_page = anchored[ti_prev][1]
            cur_page = anchored[ti_cur][1]

            if cur_page < prev_page:
                prev_offset = abs(prev_page - results[ti_prev].get("page", 0))
                cur_offset = abs(cur_page - results[ti_cur].get("page", 0))
                drop_ti = ti_cur if cur_offset >= prev_offset else ti_prev

                layer_dropped = anchored[drop_ti][0]
                logger.info("  取消[L%d单调冲突]: 表%d → P%d (与表%d(P%d)颠倒)",
                             layer_dropped, drop_ti + 1, anchored[drop_ti][1],
                             ti_prev + 1 if drop_ti == ti_cur else ti_cur + 1,
                             anchored[ti_prev if drop_ti == ti_cur else ti_cur][1])
                rejected_monotonic += 1
                del anchored[drop_ti]
                anchored_tis = sorted(anchored.keys())
                violation_found = True
                break

    remaining = [ti for ti in range(num_tables) if ti not in anchored]

    total_rejected = rejected_no_region + rejected_monotonic
    logger.info("上下文锚定: L1=%d, L2=%d, 总计=%d/%d, 拒绝=%d, 剩余=%d 进DP",
                 layer1_count, layer2_count, len(anchored), num_tables,
                 total_rejected, len(remaining))

    if anchored:
        print(f"  [docx] 上下文锚定: L1={layer1_count}, L2={layer2_count}, "
              f"拒绝={total_rejected}, 共 {len(anchored)}/{num_tables} 表锁定")

    return anchored, remaining


# ============================================================
# 锚定分段：将未锚定表划分为以锚点页为边界的区间
# ============================================================

def _build_anchor_segments(anchored, remaining_indices, num_tables,
                            total_pages, logger):
    """根据已锚定表将未锚定表划分为多个段，每段候选页范围被锚点约束。

    例如：锚定表 T3→P8, T8→P15, T20→P25
    → 段1: T1-T2, 候选 P[1, 8]
    → 段2: T4-T7, 候选 P[8, 15]
    → 段3: T9-T19, 候选 P[15, 25]
    → 段4: T21-T73, 候选 P[25, total_pages]

    Returns:
        [(table_indices_in_segment, page_start, page_end), ...]
        按顺序排列的段列表，段内 table_indices 保持原文档顺序
    """
    if not anchored:
        # 没有锚定表 → 一个段覆盖全部
        seg = (remaining_indices, 1, total_pages)
        logger.info("无锚定表，单段: 表%s → P1-P%d",
                     f"{remaining_indices[0]+1}-{remaining_indices[-1]+1}"
                     if remaining_indices else "无",
                     total_pages)
        return [seg] if remaining_indices else []

    # 锚定表按表序号排序
    anchored_sorted = sorted(anchored.items())  # [(ti, (layer, page)), ...]

    segments = []
    ri_idx = 0  # remaining_indices 的游标

    # 段0：第一个锚定表之前的表
    first_anchor_ti, (_, first_anchor_page) = anchored_sorted[0]
    pre_tis = [ti for ti in remaining_indices if ti < first_anchor_ti]
    if pre_tis:
        segments.append((pre_tis, 1, first_anchor_page))

    # 中间段：每对相邻锚定表之间的表
    for a in range(len(anchored_sorted) - 1):
        ti_prev, (_, page_prev) = anchored_sorted[a]
        ti_next, (_, page_next) = anchored_sorted[a + 1]
        mid_tis = [ti for ti in remaining_indices
                   if ti_prev < ti < ti_next]
        if mid_tis:
            segments.append((mid_tis, page_prev, page_next))

    # 段末：最后一个锚定表之后的表
    last_anchor_ti, (_, last_anchor_page) = anchored_sorted[-1]
    post_tis = [ti for ti in remaining_indices if ti > last_anchor_ti]
    if post_tis:
        segments.append((post_tis, last_anchor_page, total_pages))

    logger.info("锚定分段: %d个锚点 → %d个DP段 (范围约束)",
                 len(anchored), len(segments))

    return segments


# ============================================================
# 全局 DP 分配（修正版：正确的前缀最大 O(P) 优化）
# ============================================================

def _global_dp_assign(score_matrix, candidate_pages, total_pages, num_tables, logger):
    """全局 DP：单调约束下找总分最大分配。O(T × P) 复杂度。

    关键修正：用 dp[ti-1][pj] + α·pj 的前缀最大值 - α·pi 来计算，
    这保证了单调约束 pj <= pi 下的全局最优。

    Args:
        score_matrix: T × P 评分矩阵
        candidate_pages: 候选页码列表
        total_pages: 总页数
        num_tables: 表格总数
        logger: 日志器

    Returns:
        (result_pages, result_scores): 分配页码列表和对应得分列表
    """
    if num_tables == 0:
        return [], []

    num_candidates = len(candidate_pages)
    ALPHA = DP_DISTANCE_ALPHA

    dp = [[-1e9] * num_candidates for _ in range(num_tables)]
    prev = [[-1] * num_candidates for _ in range(num_tables)]

    # 初始化：第一张表
    for pi in range(num_candidates):
        dp[0][pi] = score_matrix[0][pi]

    # 递推：利用 dp[ti-1][pj] + ALPHA*pj 的前缀最大值
    for ti in range(1, num_tables):
        best_prefix = -1e9    # max_{pj <= pi}(dp[ti-1][pj] + ALPHA*pj)
        best_prefix_pi = -1   # 对应的 pj 索引

        for pi in range(num_candidates):
            # 更新前缀最大值
            val = dp[ti - 1][pi] + ALPHA * pi
            if val > best_prefix:
                best_prefix = val
                best_prefix_pi = pi

            if best_prefix > -1e9:
                dp[ti][pi] = score_matrix[ti][pi] + best_prefix - ALPHA * pi
                prev[ti][pi] = best_prefix_pi

    # 回溯
    best_last_pi = max(range(num_candidates), key=lambda pi: dp[-1][pi])
    assignment = [0] * num_tables
    cur = best_last_pi
    for ti in range(num_tables - 1, -1, -1):
        assignment[ti] = cur
        if ti > 0:
            cur = prev[ti][cur]

    result_pages = [candidate_pages[assignment[ti]] for ti in range(num_tables)]
    result_scores = [score_matrix[ti][assignment[ti]] for ti in range(num_tables)]

    # 日志
    for ti in range(num_tables):
        logger.debug(
            "DP 表%d → P%d (得分=%.4f)",
            ti + 1, result_pages[ti], result_scores[ti]
        )

    logger.info(
        "全局DP: %d表, %d候选页, 最优总分=%.4f",
        num_tables, num_candidates, sum(result_scores)
    )

    return result_pages, result_scores


# ============================================================
# DP 后合理性验证
# ============================================================

def _post_dp_sanity_check(results, region_map, total_pages, logger):
    """DP 分配后的合理性验证。

    检查：
    1. 分配到某页的表，该页是否真的有 V2-Lite 检测到的表格区域？
       没有表格区域的页 → 标记为"悬空表"
    2. 悬空表尝试移动到相邻的有区域页
       但只移动低分表（得分 < 0.15），有信号的高分表说明 V2-Lite 漏检了该页
    3. 搜索方向优先考虑 docx 原始页码方向，减少方向性偏差
    4. 修正后标记 _sanity_fixed=True，防止后续局部精修再次漂移
    """
    num_tables = len(results)

    # 构建 页号 → 是否有表格区域 的映射
    page_has_region = {}
    for info in region_map:
        pn = info["page"]
        page_has_region[pn] = len(info["regions"]) > 0

    fixes = 0

    for ti in range(num_tables):
        page = results[ti]["page"]
        has_region = page_has_region.get(page, False)
        dp_score = results[ti].get("_dp_score", 0.0)

        # 只移动低分悬空表：得分 >= 0.15 说明有强烈信号指向该页，
        # V2-Lite 可能漏检了该页的表格区域，不应覆盖 DP 结果
        if not has_region and dp_score < 0.15:
            # 确定搜索方向优先级：优先向 docx 原始页码方向搜索
            docx_page = results[ti].get("_docx_original_page", 0)
            if docx_page > page:
                primary_dir = 1   # docx 页码更大，优先向后搜
            elif docx_page < page:
                primary_dir = -1  # docx 页码更小，优先向前搜
            else:
                primary_dir = 1   # 默认向后搜

            for offset in range(1, 6):
                candidates = [page + offset * primary_dir,
                              page - offset * primary_dir]
                for candidate in candidates:
                    if not (1 <= candidate <= total_pages):
                        continue
                    if not page_has_region.get(candidate, False):
                        continue
                    # 检查单调性
                    if ti > 0 and candidate < results[ti - 1]["page"]:
                        continue
                    if ti < num_tables - 1 and candidate > results[ti + 1]["page"]:
                        continue
                    old_page = page
                    results[ti]["page"] = candidate
                    results[ti]["_sanity_fixed"] = True  # 标记已修正
                    fixes += 1
                    logger.info(
                        "合理性验证: 表%d 悬空P%d→P%d (P%d无表格区域, 得分=%.4f)",
                        ti + 1, old_page, candidate, old_page, dp_score
                    )
                    break
                else:
                    continue
                break  # 已修正，跳出 offset 循环

    if fixes:
        print(f"  [docx] 合理性验证: 修正 {fixes} 个悬空表")

    return results


# ============================================================
# 局部精修
# ============================================================

def _local_refine(results, page_contexts, full_page_texts, region_map,
                   region_context_index, total_pages, logger):
    """低分表的局部精修：在 ±5 页邻居内重新评分。

    对 DP 分配得分低于阈值的表，扩大搜索窗口重新评分，
    如果找到更高分的页码且满足单调约束，则修正。

    改进：
    - 上下文为空时用表头文本替代 + 动态权重
    - 最小改善阈值：得分提升不足 0.01 时不移动，避免振荡
    - 使用区域级上下文匹配（精确区分同页不同表格）
    跳过已被 sanity check 修正的表（_sanity_fixed=True），
    防止连锁漂移。
    """
    num_tables = len(results)

    # 构建 页号 → 是否有表格区域 的映射
    page_has_region = {}
    for info in region_map:
        pn = info["page"]
        page_has_region[pn] = len(info["regions"]) > 0

    # 低分阈值
    LOW_SCORE_THRESHOLD = 0.10
    # 最小改善阈值：避免边际改善引发后续步骤振荡
    MIN_IMPROVEMENT = 0.01

    refined = 0

    for ti in range(num_tables):
        # 跳过已被 sanity check 修正的表，避免连锁漂移
        if results[ti].get("_sanity_fixed", False):
            continue

        current_page = results[ti]["page"]
        current_score = results[ti].get("_dp_score", 0.0)

        if current_score >= LOW_SCORE_THRESHOLD:
            continue

        # 搜索 ±5 页窗口
        win_start = max(1, current_page - LOCAL_REFINE_WINDOW)
        win_end = min(total_pages, current_page + LOCAL_REFINE_WINDOW)

        docx_page = results[ti].get("_docx_original_page", current_page)
        ctx_text = results[ti].get("context_text", "")
        table_data = results[ti].get("data", [])

        # 上下文为空时，用表头文本作为替代上下文
        has_real_ctx = bool(ctx_text and ctx_text.strip())
        if not has_real_ctx:
            ctx_text = _build_table_header_text(table_data)

        # 动态权重
        if has_real_ctx:
            w_ctx, w_fp, w_prior = WEIGHT_CONTEXT, WEIGHT_DATA_FINGERPRINT, WEIGHT_POSITION_PRIOR
        else:
            w_ctx, w_fp, w_prior = 0.40, 0.40, 0.20

        best_page = current_page
        best_score = current_score

        for pn in range(win_start, win_end + 1):
            # 检查单调性
            if ti > 0 and pn < results[ti - 1]["page"]:
                continue
            if ti < num_tables - 1 and pn > results[ti + 1]["page"]:
                continue

            pt_ctx = page_contexts.get(pn, "")
            pt_full = full_page_texts.get(pn, "")
            has_region = page_has_region.get(pn)

            # 优先区域级上下文匹配
            region_ctxs = region_context_index.get(pn, [])
            ctx_score = _score_context_text_region(ctx_text, region_ctxs, has_region)
            if ctx_score == 0.0 and pt_ctx:
                ctx_score = _score_context_text(ctx_text, pt_ctx, has_region)

            fp_score = _score_data_fingerprint(table_data, pt_full)
            prior_score = _compute_position_prior(docx_page, pn, total_pages)

            total = (
                w_ctx * ctx_score
                + w_fp * fp_score
                + w_prior * prior_score
            )

            if total > best_score + MIN_IMPROVEMENT:
                best_score = total
                best_page = pn

        if best_page != current_page:
            old_page = current_page
            results[ti]["page"] = best_page
            results[ti]["_dp_score"] = best_score
            refined += 1
            logger.info(
                "局部精修: 表%d P%d→P%d (得分 %.4f→%.4f)",
                ti + 1, old_page, best_page, current_score, best_score
            )

    if refined:
        print(f"  [docx] 局部精修: 修正 {refined} 个低分表")

    return results


# ============================================================
# 同页拥挤修正 + 零信号表插值
# ============================================================

# 同页最大合理表格数（超过此数触发拥挤修正）
MAX_TABLES_PER_PAGE = 4


def _congestion_fix_and_interpolation(results, region_map, region_context_index,
                                       full_page_texts, page_contexts,
                                       total_pages, zero_signal_tables,
                                       docx_pages_valid, logger):
    """同页拥挤修正 + 零信号表插值兜底。

    问题：当许多表评分极低（零信号）时，DP 会把它们堆积到同一页。
    修正策略：
    1. 找出同页表数 > MAX_TABLES_PER_PAGE 的"拥挤页"
    2. 对拥挤页上的低分表，按得分排序只保留 TOP-K，其余分散到相邻有区域页
       搜索范围扩大到 ±8 页，优先向 docx 原始页码方向搜索
       **新增**：分散时必须检查目标页匹配度，避免把表塞到信号极低的页面
    3. 对于无法通过信号区分的零信号表，按前后有信号锚点做均匀插值
    4. 插值后做同页零信号表二次分散（避免多个零信号表插值到同一页）
    """
    num_tables = len(results)
    if num_tables == 0:
        return results

    # 构建 页号 → 是否有表格区域 的映射
    page_has_region = {}
    for info in region_map:
        pn = info["page"]
        page_has_region[pn] = len(info["regions"]) > 0

    # ---- 拥挤修正 ----
    # 按页分组表格索引
    page_tables = {}
    for ti in range(num_tables):
        p = results[ti]["page"]
        if p not in page_tables:
            page_tables[p] = []
        page_tables[p].append(ti)

    congested_pages = {p for p, tis in page_tables.items()
                       if len(tis) > MAX_TABLES_PER_PAGE}

    if congested_pages:
        logger.info("拥挤页: %s (每页>%d个表)",
                     sorted(congested_pages), MAX_TABLES_PER_PAGE)

        congestion_fixes = 0

        for cpage in sorted(congested_pages):
            tis = page_tables[cpage]
            # 按得分降序排列，保留 TOP MAX_TABLES_PER_PAGE，其余需要分散
            tis_sorted = sorted(tis, key=lambda ti: results[ti].get("_dp_score", 0.0),
                                reverse=True)
            keep = tis_sorted[:MAX_TABLES_PER_PAGE]
            disperse = tis_sorted[MAX_TABLES_PER_PAGE:]

            for ti in disperse:
                score = results[ti].get("_dp_score", 0.0)
                # 确定搜索方向优先级：优先向 docx 原始页码方向搜索
                docx_page = results[ti].get("_docx_original_page", 0)
                if docx_page > cpage:
                    primary_dir = 1
                elif docx_page < cpage:
                    primary_dir = -1
                else:
                    primary_dir = 1

                # 搜索范围扩大到 ±8 页
                moved = False
                for offset in range(1, 9):
                    candidates = [cpage + offset * primary_dir,
                                  cpage - offset * primary_dir]
                    for candidate in candidates:
                        if not (1 <= candidate <= total_pages):
                            continue
                        # 目标页必须有表格区域
                        if not page_has_region.get(candidate, False):
                            continue
                        # 目标页不能也是拥挤页
                        if candidate in congested_pages:
                            continue
                        # 目标页已有表数不超过 MAX_TABLES_PER_PAGE
                        target_count = len(page_tables.get(candidate, []))
                        if target_count >= MAX_TABLES_PER_PAGE:
                            continue
                        # 检查单调性
                        if ti > 0 and candidate < results[ti - 1]["page"]:
                            continue
                        if ti < num_tables - 1 and candidate > results[ti + 1]["page"]:
                            continue

                        # === 新增：检查目标页匹配度，避免把表塞到信号极低的页面 ===
                        table_data = results[ti].get("data", [])
                        ctx_text_ti = results[ti].get("context_text", "")
                        if not ctx_text_ti or not ctx_text_ti.strip():
                            ctx_text_ti = _build_table_header_text(table_data)

                        # 数据指纹匹配
                        fp_candidate = _score_data_fingerprint(
                            table_data, full_page_texts.get(candidate, "")
                        )
                        # 区域级上下文匹配
                        region_ctxs = region_context_index.get(candidate, [])
                        ctx_candidate = _score_context_text_region(
                            ctx_text_ti, region_ctxs, None
                        )
                        if ctx_candidate == 0.0:
                            ctx_candidate = _score_context_text(
                                ctx_text_ti, page_contexts.get(candidate, ""), None
                            )

                        # 综合目标页匹配度（权重与主评分一致）
                        target_match = 0.50 * ctx_candidate + 0.50 * fp_candidate

                        # 门槛：目标页匹配度过低时跳过该候选页
                        # - 比值门槛：目标匹配度不低于原页得分的 30%
                        # - 绝对门槛：不低于 0.03（防止把表塞到完全无关的页）
                        MIN_TARGET_RATIO = 0.30
                        MIN_TARGET_ABS = 0.03
                        if score > 0 and target_match < max(score * MIN_TARGET_RATIO, MIN_TARGET_ABS):
                            continue

                        old_page = results[ti]["page"]
                        results[ti]["page"] = candidate
                        results[ti]["_dp_score"] = 0.0
                        # 更新 page_tables 映射
                        page_tables.setdefault(candidate, []).append(ti)
                        if ti in page_tables.get(old_page, []):
                            page_tables[old_page].remove(ti)
                        congestion_fixes += 1
                        logger.info(
                            "拥挤修正: 表%d P%d→P%d (原得分=%.4f)",
                            ti + 1, old_page, candidate, score
                        )
                        moved = True
                        break
                    if moved:
                        break

        if congestion_fixes:
            print(f"  [docx] 拥挤修正: 分散 {congestion_fixes} 个表")

    # ---- 零信号表插值 ----
    if not zero_signal_tables:
        return results

    # 找有信号锚点（得分 >= 0.10 的表）
    anchor_indices = []
    for ti in range(num_tables):
        if ti in zero_signal_tables:
            continue
        score = results[ti].get("_dp_score", 0.0)
        if score >= 0.10:
            anchor_indices.append(ti)

    if len(anchor_indices) < 2:
        logger.info("零信号表插值: 锚点不足(%d个, 需要>=2)，跳过",
                     len(anchor_indices))
        return results

    # 对零信号表做线性插值
    interpolated = 0
    zero_sorted = sorted(zero_signal_tables)

    for ti in zero_sorted:
        # 找前后最近的锚点
        prev_anchor = None
        next_anchor = None
        for ai in anchor_indices:
            if ai < ti:
                prev_anchor = ai
            elif ai > ti:
                next_anchor = ai
                break

        if prev_anchor is not None and next_anchor is not None:
            # 线性插值
            prev_page = results[prev_anchor]["page"]
            next_page = results[next_anchor]["page"]
            # 确保插值方向合理：如果前锚点页码 > 后锚点页码，
            # 说明有非单调情况，取两者的中间偏前
            if prev_page > next_page:
                estimated = prev_page
            else:
                ratio = (ti - prev_anchor) / max(1, next_anchor - prev_anchor)
                estimated = round(prev_page + (next_page - prev_page) * ratio)
        elif prev_anchor is not None:
            estimated = results[prev_anchor]["page"]
        elif next_anchor is not None:
            estimated = results[next_anchor]["page"]
        else:
            continue

        estimated = max(1, min(estimated, total_pages))
        old_page = results[ti]["page"]
        if estimated != old_page:
            results[ti]["page"] = estimated
            interpolated += 1
            prev_info = (f"表{prev_anchor+1}P{results[prev_anchor]['page']}"
                         if prev_anchor is not None else "无前锚点")
            next_info = (f"表{next_anchor+1}P{results[next_anchor]['page']}"
                         if next_anchor is not None else "无后锚点")
            logger.info(
                "零信号插值: 表%d P%d→P%d (%s, %s)",
                ti + 1, old_page, estimated, prev_info, next_info
            )

    if interpolated:
        print(f"  [docx] 零信号插值: 修正 {interpolated} 个表")

    # ---- 零信号表二次分散：插值后同页零信号表过多时分散 ----
    zero_page_tables = {}
    for ti in zero_sorted:
        p = results[ti]["page"]
        if p not in zero_page_tables:
            zero_page_tables[p] = []
        zero_page_tables[p].append(ti)

    # 同页零信号表超过 2 个时，尝试分散到前后有区域的页
    ZERO_PER_PAGE_LIMIT = 2
    secondary_fixes = 0

    for page, tis in zero_page_tables.items():
        if len(tis) <= ZERO_PER_PAGE_LIMIT:
            continue

        # 保留前 ZERO_PER_PAGE_LIMIT 个（按表序号排序，保留最靠前的）
        tis_sorted_by_idx = sorted(tis)
        keep = tis_sorted_by_idx[:ZERO_PER_PAGE_LIMIT]
        disperse = tis_sorted_by_idx[ZERO_PER_PAGE_LIMIT:]

        for ti in disperse:
            # 尝试分散到相邻有区域且同页零信号表不多的页
            moved = False
            for offset in range(1, 6):
                for candidate in [page + offset, page - offset]:
                    if not (1 <= candidate <= total_pages):
                        continue
                    if not page_has_region.get(candidate, False):
                        continue
                    # 目标页零信号表数不超过限制
                    target_zero_count = len(zero_page_tables.get(candidate, []))
                    if target_zero_count >= ZERO_PER_PAGE_LIMIT:
                        continue
                    # 检查单调性
                    if ti > 0 and candidate < results[ti - 1]["page"]:
                        continue
                    if ti < num_tables - 1 and candidate > results[ti + 1]["page"]:
                        continue

                    old_page = results[ti]["page"]
                    results[ti]["page"] = candidate
                    # 更新零信号表映射
                    zero_page_tables.setdefault(candidate, []).append(ti)
                    if ti in zero_page_tables.get(old_page, []):
                        zero_page_tables[old_page].remove(ti)
                    secondary_fixes += 1
                    logger.info(
                        "零信号二次分散: 表%d P%d→P%d",
                        ti + 1, old_page, candidate
                    )
                    moved = True
                    break
                if moved:
                    break

    if secondary_fixes:
        print(f"  [docx] 零信号二次分散: 修正 {secondary_fixes} 个表")

    return results


# ============================================================
# 单调性 + 边界约束
# ============================================================

def _phase6_monotonic_and_boundary(results, total_pages, num_tables, logger=None):
    """【铁律】单调递增 + 页数边界约束

    核心约束：
    1. results 列表顺序 = 文档物理出现顺序，绝不打乱
    2. results[i].page >= results[i-1].page（单调非递减，同页可重叠）
    3. 所有页码 ∈ [1, total_pages]（不能超出 PDF 总页数）
    """
    monotonic_fixes = 0
    for i in range(1, num_tables):
        prev_page = results[i - 1].get("page", 0)
        cur_page = results[i].get("page", 0)
        if cur_page < prev_page:
            results[i]["page"] = prev_page
            monotonic_fixes += 1
            print(
                f"  [docx] 单调递增修正: 表{i+1} P{cur_page}→P{prev_page}"
            )

    # 页数边界约束
    for r in results:
        r["page"] = max(1, min(r["page"], total_pages))

    if monotonic_fixes > 0 and logger:
        logger.warning("阶段6 单调递增修正: %d个表", monotonic_fixes)


# ============================================================
# 旧方案 fallback 辅助函数（保留兼容）
# ============================================================

MIN_COMMON = 2
HIGH_CONF_JACCARD = 0.08
BBOX_MIN_CONF = 0.02
XY_OVERLAP_THRESHOLD = 0.05


def _can_move_to(ti, target_page, results, total_pages, num_tables):
    """单调约束下 ti 能否移到 target_page"""
    if target_page < 1 or target_page > total_pages:
        return False
    if ti > 0 and results[ti - 1]["page"] > target_page:
        return False
    if ti < num_tables - 1 and results[ti + 1]["page"] < target_page:
        return False
    return True


def _has_valid_bbox(ti, matches):
    """XY 矩形是否有效"""
    return (matches[ti] is not None
            and matches[ti][1] >= BBOX_MIN_CONF
            and matches[ti][3] >= 0 and matches[ti][4] > matches[ti][3]
            and matches[ti][5] >= 0 and matches[ti][6] > matches[ti][5])


def _rect_overlap_ratio(ti_a, ti_b, matches):
    """计算两表 XY 矩形重叠面积 / min(各自面积)。多栏页面返回 0。"""
    x0_a, y0_a = matches[ti_a][5], matches[ti_a][3]
    x1_a, y1_a = matches[ti_a][6], matches[ti_a][4]
    x0_b, y0_b = matches[ti_b][5], matches[ti_b][3]
    x1_b, y1_b = matches[ti_b][6], matches[ti_b][4]

    ox0, ox1 = max(x0_a, x0_b), min(x1_a, x1_b)
    oy0, oy1 = max(y0_a, y0_b), min(y1_a, y1_b)
    if ox0 >= ox1 or oy0 >= oy1:
        return 0.0

    overlap_area = (ox1 - ox0) * (oy1 - oy0)
    area_a = (x1_a - x0_a) * (y1_a - y0_a)
    area_b = (x1_b - x0_b) * (y1_b - y0_b)
    min_area = min(area_a, area_b)
    return overlap_area / min_area if min_area > 0 else 0.0


def _try_v2_correction(ti, matches, results, total_pages, num_tables):
    """尝试把 ti 移到 V2-Lite 原始匹配页（单调约束下）"""
    if matches[ti] is None:
        return None
    v2_page = matches[ti][0]
    cur_page = results[ti]["page"]
    if v2_page == cur_page:
        return None
    if _can_move_to(ti, v2_page, results, total_pages, num_tables):
        return v2_page
    if v2_page > cur_page:
        for p in range(cur_page + 1, v2_page + 1):
            if _can_move_to(ti, p, results, total_pages, num_tables):
                return p
    else:
        for p in range(cur_page - 1, v2_page - 1, -1):
            if _can_move_to(ti, p, results, total_pages, num_tables):
                return p
    return None


def _phase5_xy_overlap_and_v2_correction(results, matches, total_pages, num_tables, logger=None):
    """阶段5：Bbox XY 矩形重叠检测 + V2-Lite 页码大跨跳修正（旧方案用）"""
    v2_page_fixes = 0
    for ti in range(num_tables):
        if matches[ti] is None:
            continue
        v2_page = matches[ti][0]
        cur_page = results[ti]["page"]
        if abs(v2_page - cur_page) <= 1:
            continue
        new_page = _try_v2_correction(ti, matches, results, total_pages, num_tables)
        if new_page is not None and new_page != cur_page:
            old = cur_page
            results[ti]["page"] = new_page
            v2_page_fixes += 1
            ctx_review = results[ti].get("context_text", "")[:30]
            print(
                f"  [docx] Phase5 V2页码修正: 表{ti+1} P{old}→P{new_page}"
                f" (V2原始=P{v2_page}, Δ={abs(v2_page - old)}) [{ctx_review}]"
            )
    if v2_page_fixes:
        print(f"  [docx] Phase5 V2页码修正: {v2_page_fixes} 个")
        if logger:
            logger.info("阶段5 V2页码大跨跳修正: %d个", v2_page_fixes)

    xy_conflicts = 0
    max_iters = num_tables * 3
    iters = 0
    idx = 1

    move_pairs = {ti: set() for ti in range(num_tables)}
    locked_tables = set()

    while idx < num_tables and iters < max_iters:
        iters += 1

        if results[idx]["page"] != results[idx - 1]["page"]:
            idx += 1
            continue

        if not _has_valid_bbox(idx - 1, matches) or not _has_valid_bbox(idx, matches):
            idx += 1
            continue

        ti_prev, ti_cur = idx - 1, idx

        if ti_prev not in locked_tables:
            if matches[ti_prev] is not None and results[ti_prev]["page"] != matches[ti_prev][0]:
                idx += 1
                continue
        if ti_cur not in locked_tables:
            if matches[ti_cur] is not None and results[ti_cur]["page"] != matches[ti_cur][0]:
                idx += 1
                continue

        y0_prev, y1_prev = matches[ti_prev][3], matches[ti_prev][4]
        x0_prev, x1_prev = matches[ti_prev][5], matches[ti_prev][6]
        y0_cur, y1_cur = matches[ti_cur][3], matches[ti_cur][4]
        x0_cur, x1_cur = matches[ti_cur][5], matches[ti_cur][6]

        if y1_prev <= y0_cur:
            idx += 1
            continue

        overlap = _rect_overlap_ratio(ti_prev, ti_cur, matches)

        if overlap < XY_OVERLAP_THRESHOLD:
            idx += 1
            continue

        P = results[ti_prev]["page"]
        sc_prev = matches[ti_prev][1]
        sc_cur = matches[ti_cur][1]

        move_ti = ti_prev if sc_prev <= sc_cur else ti_cur
        cur_pg = results[move_ti]["page"]

        new_page = _try_v2_correction(move_ti, matches, results, total_pages, num_tables)
        if new_page is None:
            old_pg = results[move_ti]["page"]
            if P < total_pages and _can_move_to(move_ti, P + 1, results, total_pages, num_tables):
                new_page = P + 1
            elif P > 1 and _can_move_to(move_ti, P - 1, results, total_pages, num_tables):
                new_page = P - 1

        if new_page is None:
            locked_tables.add(move_ti)
            idx += 1
            continue

        pair = (cur_pg, new_page)
        reverse_pair = (new_page, cur_pg)
        if reverse_pair in move_pairs[move_ti]:
            locked_tables.add(move_ti)
            idx += 1
            continue

        old_page = results[move_ti]["page"]
        results[move_ti]["page"] = new_page
        move_pairs[move_ti].add(pair)

        xy_conflicts += 1
        direction = "→" if new_page > old_page else "←"
        print(
            f"  [docx] Phase5 XY重叠修正: 表{move_ti+1} P{old_page}{direction}P{new_page}"
            f" (重叠率={overlap:.1%},"
            f" xy[{x0_prev:.0f},{x1_prev:.0f}/{y0_prev:.0f},{y1_prev:.0f}]"
            f" vs [{x0_cur:.0f},{x1_cur:.0f}/{y0_cur:.0f},{y1_cur:.0f}],"
            f" s={min(sc_prev, sc_cur):.3f})"
        )
        idx = max(1, idx - 1)

    if iters >= max_iters:
        print(f"  [docx] ⚠ Phase5 XY重叠检测达到迭代上限({max_iters})，强制停止")
    if xy_conflicts:
        print(f"  [docx] Phase5 XY重叠修正: {xy_conflicts} 个")


def _anchor_interpolation(results, page_fingerprints, matches, num_tables,
                          total_pages, logger=None):
    """阶段2.5：用高置信度锚点表驱动中间零分/低分表的均匀分布（旧方案用）"""
    anchors = []
    for ti in range(num_tables):
        if matches[ti] is not None and matches[ti][1] >= HIGH_CONF_JACCARD:
            anchors.append((ti, results[ti]["page"]))

    if len(anchors) < 2:
        if logger:
            logger.info("阶段2.5 锚点插值: 锚点不足(%d个, 需要>=2)，跳过", len(anchors))
        return

    page_to_idx = {pn: pi for pi, (pn, _) in enumerate(page_fingerprints)}
    page_idx_to_num = [pn for pn, _ in page_fingerprints]

    interpolated = 0

    for a in range(len(anchors) - 1):
        left_ti, left_page = anchors[a]
        right_ti, right_page = anchors[a + 1]

        left_idx = page_to_idx.get(left_page)
        right_idx = page_to_idx.get(right_page)
        if left_idx is None or right_idx is None:
            continue

        middle_tables = list(range(left_ti + 1, right_ti))
        if not middle_tables:
            continue

        idx_span = right_idx - left_idx
        ti_span = right_ti - left_ti

        for ti in middle_tables:
            if matches[ti] is not None and matches[ti][1] >= HIGH_CONF_JACCARD:
                continue

            old_page = results[ti]["page"]
            ratio = (ti - left_ti) / ti_span
            target_idx = left_idx + round(idx_span * ratio)
            target_idx = max(0, min(target_idx, len(page_idx_to_num) - 1))
            new_page = page_idx_to_num[target_idx]
            new_page = max(1, min(new_page, total_pages))

            if new_page != old_page:
                results[ti]["page"] = new_page
                interpolated += 1
                if matches[ti] is not None:
                    m = matches[ti]
                    matches[ti] = (new_page,) + m[1:]

    if interpolated > 0:
        print(
            f"  [docx] 锚点插值: {interpolated} 个表在 "
            f"{len(anchors)} 个锚点间重新分配"
        )


# ============================================================
# 新方案：修正版全局DP分配
# ============================================================

def _assign_by_global_dp(results, page_contexts, full_page_texts, region_map,
                          total_pages, logger):
    """修正版全局DP分配方案（含上下文锚定优化）。

    流程：
      0. 预计算 n-gram 频率 → 上下文锚定（两层策略锁定唯一命中的表）
      1. 构建评分矩阵（仅未锚定表）：上下文(0.50) + 数据指纹(0.30) + 位置先验(0.20)
      2. 全局 DP 分配（仅剩余表，正确的前缀最大 O(P) 优化）
      3. 合理性验证（悬空表修正）
      4. 局部精修（低分表 ±5 页重评分）
      5. 同页拥挤修正 + 零信号表插值兜底
      6. 单调性 + 边界约束
    """
    num_tables = len(results)
    if num_tables == 0:
        return results

    # 构建 页号 → 是否有表格区域 的映射
    page_has_region = {}
    for info in region_map:
        pn = info["page"]
        page_has_region[pn] = len(info["regions"]) > 0

    # 构建区域级上下文索引：{page: [region_ctx_0, region_ctx_1, ...]}
    region_context_index = _build_region_context_index(region_map)

    # 检测 docx 原始页码是否有效（不全为同一值）
    docx_pages = [r.get("page", 0) for r in results]
    docx_pages_valid = len(set(docx_pages)) > 1

    # 候选页：1 ~ total_pages（用于指纹频率预计算）
    candidate_pages = list(range(1, total_pages + 1))

    # ---- 步骤0：预计算 n-gram 频率 + 上下文锚定 ----
    logger.info("预计算页级 n-gram 频率（用于过滤页眉/页脚）")
    _, high_freq_ngrams = _build_page_ngram_freq(page_contexts)
    logger.info("高频 n-gram（页眉/页脚污染）: %d 个 (>3页出现)", len(high_freq_ngrams))

    logger.info("执行上下文锚定：滑动窗口+两层策略锁定唯一命中的表")
    anchored, remaining_indices = _context_anchor_pass(
        results, region_context_index, page_contexts,
        page_has_region, high_freq_ngrams, total_pages, logger
    )

    # 保存 docx 原始页码（所有表，供位置先验和局部精修使用）
    for ti in range(num_tables):
        results[ti]["_docx_original_page"] = docx_pages[ti]

    # 先写入锚定表的页码（高置信度满分）
    for ti, (layer, page) in anchored.items():
        results[ti]["_dp_score"] = 1.0
        results[ti]["_anchored"] = True

    # ---- 如果所有表都被锚定，跳过 DP ----
    if not remaining_indices:
        logger.info("所有 %d 表已通过上下文锚定锁定，跳过 DP 评分矩阵构建", num_tables)
        for ti, (layer, page) in anchored.items():
            old_page = results[ti]["page"]
            results[ti]["page"] = page
            if page != old_page:
                logger.info("  锚定 表%d: docx原始P%d → 锚定P%d", ti + 1, old_page, page)
        print(f"  [docx] 全部 {num_tables} 表由上下文锚定锁定，无需DP")

        # 仍需单调性 + 边界约束
        _phase6_monotonic_and_boundary(results, total_pages, num_tables, logger)

        # 清理临时字段
        for r in results:
            r.pop("_dp_score", None)
            r.pop("_docx_original_page", None)
            r.pop("_sanity_fixed", None)
            r.pop("_anchored", None)
            r.pop("_ctx_is_header", None)
        return results

    # ---- 步骤1：预计算指纹词频率（用于区分度评分） ----
    _precompute_fp_page_freq(results, candidate_pages, full_page_texts)
    high_freq_count = sum(1 for v in _FP_PAGE_FREQ.values() if v > FP_FREQ_THRESHOLD)
    logger.info("指纹频率预计算: %d个唯一指纹, %d个高频(>%d页)",
                 len(_FP_PAGE_FREQ), high_freq_count, FP_FREQ_THRESHOLD)

    # ---- 步骤2：按锚定表分段，逐段构建评分矩阵 + DP ----
    # 关键优化：锚定表作为硬边界，未锚定表的 DP 搜索范围被限制在相邻锚点之间，
    # 而非全局 36 页。这大幅减少相邻页串位的概率。
    segments = _build_anchor_segments(anchored, remaining_indices, num_tables,
                                       total_pages, logger)
    logger.info("锚定分段DP: %d个段, %d剩余表", len(segments), len(remaining_indices))

    ZERO_SIGNAL_THRESHOLD = 0.05
    zero_signal_tables = set()
    dp_changes = 0

    for seg_idx, (seg_tis, seg_page_start, seg_page_end) in enumerate(segments):
        if not seg_tis:
            continue

        seg_candidates = list(range(seg_page_start, seg_page_end + 1))
        seg_num_candidates = len(seg_candidates)
        seg_num_tables = len(seg_tis)

        logger.debug("  段%d: 表%s → 候选P%d-P%d (%d候选 × %d表)",
                      seg_idx + 1,
                      f"{seg_tis[0]+1}-{seg_tis[-1]+1}" if len(seg_tis) > 1 else str(seg_tis[0]+1),
                      seg_page_start, seg_page_end,
                      seg_num_candidates, seg_num_tables)

        # 构建该段的评分矩阵
        seg_score_matrix = []
        for ti in seg_tis:
            docx_page = results[ti].get("page", 0)
            ctx_text = results[ti].get("context_text", "")
            table_data = results[ti].get("data", [])

            has_real_ctx = bool(ctx_text and ctx_text.strip())
            if not has_real_ctx:
                ctx_text = _build_table_header_text(table_data)

            if has_real_ctx:
                w_ctx, w_fp, w_prior = WEIGHT_CONTEXT, WEIGHT_DATA_FINGERPRINT, WEIGHT_POSITION_PRIOR
            else:
                w_ctx, w_fp, w_prior = 0.40, 0.40, 0.20
                results[ti]["_ctx_is_header"] = True

            row = []
            for pn in seg_candidates:
                pt_ctx = page_contexts.get(pn, "")
                pt_full = full_page_texts.get(pn, "")
                has_region = page_has_region.get(pn)

                region_ctxs = region_context_index.get(pn, [])
                ctx_score = _score_context_text_region(ctx_text, region_ctxs, has_region)
                if ctx_score == 0.0 and pt_ctx:
                    ctx_score = _score_context_text(ctx_text, pt_ctx, has_region)

                fp_score = _score_data_fingerprint(table_data, pt_full)
                prior_score = _compute_position_prior(docx_page, pn, total_pages)

                total_score = (
                    w_ctx * ctx_score + w_fp * fp_score + w_prior * prior_score
                )
                row.append(total_score)

            seg_score_matrix.append(row)

        # 日志：段内每张表 top5
        for ri, ti in enumerate(seg_tis):
            row = seg_score_matrix[ri]
            if seg_num_candidates <= 6:
                # 候选少时打印全部
                score_str = ", ".join(
                    f"P{seg_candidates[p]}:{row[p]:.4f}" for p in range(seg_num_candidates)
                )
            else:
                top_scores = sorted(
                    [(row[p], p) for p in range(seg_num_candidates)], reverse=True
                )[:5]
                score_str = ", ".join(
                    f"P{seg_candidates[p]}:{s:.4f}" for s, p in top_scores if s > 0
                )
            if score_str:
                logger.debug("  表%d[段%d] 得分: %s", ti + 1, seg_idx + 1, score_str)

        # 段内零信号检测
        for ri, ti in enumerate(seg_tis):
            if max(seg_score_matrix[ri]) < ZERO_SIGNAL_THRESHOLD:
                zero_signal_tables.add(ti)

        # 段内 DP 分配
        seg_pages, seg_scores = _global_dp_assign(
            seg_score_matrix, seg_candidates, total_pages, seg_num_tables, logger
        )

        # 写入段内 DP 结果
        for ri, ti in enumerate(seg_tis):
            old_page = results[ti]["page"]
            results[ti]["page"] = seg_pages[ri]
            results[ti]["_dp_score"] = seg_scores[ri]
            if seg_pages[ri] != old_page:
                dp_changes += 1
                logger.info(
                    "DP[段%d] 表%d: docx原始P%d → DP修正P%d (得分=%.4f)",
                    seg_idx + 1, ti + 1, old_page, seg_pages[ri], seg_scores[ri]
                )

    if zero_signal_tables:
        logger.info("零信号表: %d/%d (得分全 < %.2f)",
                     len(zero_signal_tables), len(remaining_indices), ZERO_SIGNAL_THRESHOLD)

    # 写入锚定表的页码（锚定表得分 = 1.0，后续精修不会动它们）
    for ti, (layer, page) in anchored.items():
        old_page = results[ti]["page"]
        results[ti]["page"] = page
        if page != old_page:
            dp_changes += 1
            logger.info(
                "锚定 表%d: docx原始P%d → 锚定P%d (L%d, 得分=1.0)",
                ti + 1, old_page, page, layer
            )

    if dp_changes:
        print(f"  [docx] 全局DP修正了 {dp_changes} 个表的页码")

    # ---- 步骤5：合理性验证 ----
    # 锚定表得分=1.0，不会被移动（sanity check 阈值 0.15）
    logger.info("合理性验证")
    results = _post_dp_sanity_check(results, region_map, total_pages, logger)

    # ---- 步骤6：局部精修 ----
    # 锚定表得分=1.0，不会被移动（local refine 阈值 0.10）
    logger.info("局部精修 (低分表 ±%d页)", LOCAL_REFINE_WINDOW)
    results = _local_refine(
        results, page_contexts, full_page_texts, region_map,
        region_context_index, total_pages, logger
    )

    # ---- 步骤7：同页拥挤修正 + 零信号表插值 ----
    # 锚定表得分最高(1.0)，拥挤修正中永远被保留不移走
    results = _congestion_fix_and_interpolation(
        results, region_map, region_context_index,
        full_page_texts, page_contexts, total_pages,
        zero_signal_tables, docx_pages_valid, logger
    )

    # ---- 步骤8：单调性 + 边界约束 ----
    _phase6_monotonic_and_boundary(results, total_pages, num_tables, logger)

    # 统计（在清理临时字段前收集）
    anchored_count = len(anchored)
    dp_scores_remaining = [
        results[ti].get("_dp_score", 0) for ti in remaining_indices
    ]
    high_conf = anchored_count + sum(1 for s in dp_scores_remaining if s >= 0.15)
    mid_conf = sum(1 for s in dp_scores_remaining if 0.05 <= s < 0.15)
    low_conf = sum(1 for s in dp_scores_remaining if s < 0.05)

    # 清理临时字段
    for r in results:
        r.pop("_dp_score", None)
        r.pop("_docx_original_page", None)
        r.pop("_sanity_fixed", None)
        r.pop("_anchored", None)
        r.pop("_ctx_is_header", None)

    print(
        f"  [docx] 页码分配: 锚定={anchored_count}, 高置信度={high_conf - anchored_count}, "
        f"中等={mid_conf}, 低置信度={low_conf} / {num_tables}"
    )
    logger.info("=== 分段DP完毕: 锚定=%d 高=%d 中=%d 低=%d / %d ===",
                anchored_count, high_conf - anchored_count, mid_conf, low_conf, num_tables)

    return results


# ============================================================
# 旧方案 fallback：6阶段DP流水线
# ============================================================

def _assign_docx_pages_legacy(results, context, logger=None):
    """旧版6阶段DP流水线（新方案的 fallback）。

    阶段0：V2-Lite 扫描
    阶段1：构建数据指纹
    阶段2：滑动窗口 DP
    阶段2.5：锚点插值
    阶段3：匹配质量汇总
    阶段4：邻居插值兜底
    阶段5：XY 重叠检测 + V2 修正
    阶段6：单调性 + 边界约束
    """
    if not results or not context:
        return results

    total_pages = context.page_count
    if total_pages <= 1:
        return results

    num_tables = len(results)

    if logger is None:
        logger = _get_dp_logger(context)
    logger.info("PDF总页数=%d, 待分配表格数=%d", total_pages, num_tables)
    docx_pages = [r.get("page", 0) for r in results]
    logger.info("docx原始页码范围: P%d ~ P%d", min(docx_pages), max(docx_pages))

    # ===== 阶段0：V2-Lite 扫描 =====
    region_map = _scan_table_regions_lite(context)

    all_regions = []
    for page_info in region_map:
        page_num = page_info["page"]
        for region in page_info["regions"]:
            rx0, ry0, rx1, ry1, region_text = region
            region_tokens = set(
                t.lower() for t in region_text.split() if len(t) >= 2
            )
            if region_tokens:
                all_regions.append(
                    (page_num, region_tokens, rx0, ry0, rx1, ry1)
                )

    pages_with_regions = sum(1 for r in region_map if r["regions"])
    total_regions = sum(len(r["regions"]) for r in region_map)
    logger.info("阶段0 V2-Lite扫描: %d/%d 页有表格区域, 共 %d 个区域",
                pages_with_regions, len(region_map), total_regions)

    if not all_regions:
        print("  [docx] V2-Lite 未检测到任何表格区域，保留 docx 原始页码")
        return results

    # ===== 阶段1：为每个 docx 表格构建数据指纹 =====
    table_fingerprints = []
    for r in results:
        rows = r.get("data", [])
        fp = _build_table_data_fingerprint(rows)
        table_fingerprints.append(fp)

    for ti in range(num_tables):
        fp = table_fingerprints[ti]
        docx_page = results[ti].get("page", 0)
        ctx = results[ti].get("context_text", "")[:40]
        logger.debug("阶段1 表%d: docx原始P%d, 指纹词数=%d, ctx=%s",
                      ti + 1, docx_page, len(fp), ctx)

    # ===== 阶段2：滑动窗口 DP =====
    page_fingerprints = []
    pages_with_regions_set = set()

    for page_info in region_map:
        page_num = page_info["page"]
        regions = page_info["regions"]
        if not regions:
            continue
        all_tokens = set()
        for region in regions:
            _rx0, _ry0, _rx1, _ry1, region_text = region
            region_tokens = set(
                t.lower() for t in region_text.split() if len(t) >= 2
            )
            all_tokens |= region_tokens
        if all_tokens:
            page_fingerprints.append((page_num, all_tokens))
            pages_with_regions_set.add(page_num)

    empty_pages_added = 0
    for pn in range(1, total_pages + 1):
        if pn not in pages_with_regions_set:
            page_fingerprints.append((pn, set()))
            empty_pages_added += 1

    page_fingerprints.sort(key=lambda x: x[0])

    if empty_pages_added > 0:
        print(
            f"  [docx] 候选页补全: 新增 {empty_pages_added} 个无表格区域页, "
            f"共 {len(page_fingerprints)} 个候选页"
        )
        logger.info(
            "阶段2 候选页补全: V2区域=%d, 空通道页=%d, 总计=%d",
            len(pages_with_regions_set), empty_pages_added, len(page_fingerprints)
        )

    if not page_fingerprints:
        print("  [docx] V2-Lite 未检测到任何表格区域，保留 docx 原始页码")
        return results

    num_candidate_pages = len(page_fingerprints)
    print(
        f"  [docx] 滑动窗口DP: {num_tables}张表 → "
        f"{num_candidate_pages}个候选页"
    )

    candidate_page_map = {pi: pn for pi, (pn, _) in enumerate(page_fingerprints)}
    logger.info("阶段2 候选页映射: %s", candidate_page_map)

    # 预计算 score 矩阵
    score_matrix = []
    for ti, fp in enumerate(table_fingerprints):
        row = []
        if not fp:
            row = [0.0] * num_candidate_pages
        else:
            for pi, (_pn, page_tokens) in enumerate(page_fingerprints):
                common = fp & page_tokens
                common_count = len(common)
                if common_count < MIN_COMMON:
                    row.append(0.0)
                else:
                    union = fp | page_tokens
                    if not union:
                        row.append(0.0)
                    else:
                        jaccard = common_count / len(union)
                        row.append(jaccard + 0.015 * common_count)
        score_matrix.append(row)

    logger.info("阶段2 score矩阵: %d×%d", num_tables, num_candidate_pages)
    for ti in range(num_tables):
        row = score_matrix[ti]
        top_scores = sorted(
            [(row[pi], pi) for pi in range(num_candidate_pages)],
            reverse=True
        )[:5]
        score_str = ", ".join(
            f"P{page_fingerprints[p][0]}:{s:.4f}" for s, p in top_scores if s > 0
        )
        if score_str:
            logger.debug("  表%d top5得分: %s", ti + 1, score_str)

    # 滑动窗口 DP
    WINDOW_SIZE = 4
    WINDOW_STEP = 2
    DISTANCE_PENALTY = V2_CONFIG.get("distance_penalty", 0.002)

    assigned_page_idx = [-1] * num_tables
    committed_lower = 0

    for start_ti in range(0, num_tables, WINDOW_STEP):
        end_ti = min(start_ti + WINDOW_SIZE, num_tables)
        window_len = end_ti - start_ti

        candidate_start = committed_lower
        candidate_end = num_candidate_pages

        if candidate_start >= candidate_end:
            last_pi = candidate_end - 1
            for ti in range(start_ti, num_tables):
                assigned_page_idx[ti] = last_pi
            break

        num_cols = candidate_end - candidate_start

        dp = [[-1e9] * num_cols for _ in range(window_len)]
        prev_dp = [[-1] * num_cols for _ in range(window_len)]

        for k in range(window_len):
            ti = start_ti + k
            for p in range(candidate_start, candidate_end):
                col = p - candidate_start
                cur_score = score_matrix[ti][p]

                if k == 0:
                    dp[k][col] = cur_score
                    prev_dp[k][col] = -1
                else:
                    best_prev_score = -1e9
                    best_prev_p = -1
                    for q in range(candidate_start, p + 1):
                        q_col = q - candidate_start
                        if dp[k - 1][q_col] > best_prev_score:
                            best_prev_score = dp[k - 1][q_col]
                            best_prev_p = q
                    if best_prev_score > -1e9:
                        page_dist = (candidate_start + col) - best_prev_p
                        dp[k][col] = (cur_score + best_prev_score
                                      - DISTANCE_PENALTY * page_dist)
                        prev_dp[k][col] = best_prev_p

        last_k = window_len - 1
        best_total = -1e9
        best_last_p = candidate_start
        for p in range(candidate_start, candidate_end):
            col = p - candidate_start
            if dp[last_k][col] > best_total:
                best_total = dp[last_k][col]
                best_last_p = p

        window_assignment = [-1] * window_len
        cur_p = best_last_p
        for k in range(window_len - 1, -1, -1):
            window_assignment[k] = cur_p
            if k > 0:
                col = cur_p - candidate_start
                cur_p = prev_dp[k][col]

        commit_count = min(WINDOW_STEP, window_len)
        for k in range(commit_count):
            ti = start_ti + k
            assigned_page_idx[ti] = window_assignment[k]
            committed_lower = window_assignment[k]

        win_pages = [page_fingerprints[window_assignment[k]][0] for k in range(window_len)]
        logger.debug(
            "阶段2 窗口[%d:%d]: 候选页范围[%d,%d), DP最优总分=%.4f, 路径=%s",
            start_ti, end_ti, candidate_start, candidate_end, best_total, win_pages
        )

    # 区域级匹配
    matches = [None] * num_tables

    for ti in range(num_tables):
        pi = assigned_page_idx[ti]
        if pi < 0:
            continue

        page_num = page_fingerprints[pi][0]
        fp = table_fingerprints[ti]

        best_score = 0.0
        best_common = 0
        best_y0 = best_y1 = best_x0 = best_x1 = 0.0

        for r_page_num, region_tokens, rx0, ry0, rx1, ry1 in all_regions:
            if r_page_num != page_num:
                continue
            if not fp:
                continue

            common = fp & region_tokens
            common_count = len(common)
            if common_count < MIN_COMMON:
                continue
            union = fp | region_tokens
            if not union:
                continue

            jaccard = common_count / len(union)
            combined = jaccard + 0.015 * common_count

            if combined > best_score:
                best_score = combined
                best_common = common_count
                best_y0, best_y1 = ry0, ry1
                best_x0, best_x1 = rx0, rx1

        if best_score > 0:
            matches[ti] = (page_num, best_score, best_common,
                           best_y0, best_y1, best_x0, best_x1)
        else:
            fallback = None
            for r_page_num, _rt, rx0, ry0, rx1, ry1 in all_regions:
                if r_page_num == page_num:
                    fallback = (rx0, ry0, rx1, ry1)
                    break
            if fallback:
                matches[ti] = (page_num, 0.0, 0,
                               fallback[1], fallback[3], fallback[0], fallback[2])
            else:
                matches[ti] = (page_num, 0.0, 0, 0.0, 0.0, 0.0, 0.0)

    dp_page_changes = 0
    for ti in range(num_tables):
        if assigned_page_idx[ti] >= 0:
            old_page = results[ti].get("page", 0)
            new_page = page_fingerprints[assigned_page_idx[ti]][0]
            results[ti]["page"] = new_page
            if new_page != old_page:
                dp_page_changes += 1
                m = matches[ti] if ti < len(matches) else None
                if m:
                    mp, ms, mc, _y0, _y1, _x0, _x1 = m
                    logger.info(
                        "阶段2 表%d: docx原始P%d → DP修正P%d | J=%.4f common=%d",
                        ti + 1, old_page, new_page, ms, mc
                    )
                else:
                    logger.info(
                        "阶段2 表%d: docx原始P%d → DP修正P%d (区域兜底)",
                        ti + 1, old_page, new_page
                    )

    if dp_page_changes > 0:
        print(f"  [docx] 滑动窗口DP 修正了 {dp_page_changes} 个表的页码")
    logger.info("阶段2 DP合计修正了 %d/%d 个表的页码", dp_page_changes, num_tables)

    # ===== 阶段2.5：锚点插值 =====
    _anchor_interpolation(
        results, page_fingerprints, matches, num_tables,
        total_pages, logger
    )

    # ===== 阶段3：DP 匹配质量汇总 =====
    dp_high = 0
    dp_mid = 0
    dp_low = 0

    for ti in range(num_tables):
        if matches[ti] is None:
            ctx_review = results[ti].get("context_text", "")[:50]
            print(f"  [docx] 表{ti+1}: 无匹配 [{ctx_review}]")
            continue

        matched_page, score, common_cnt, _y0, _y1, _x0, _x1 = matches[ti]
        ctx_review = results[ti].get("context_text", "")[:40]

        if score >= HIGH_CONF_JACCARD:
            dp_high += 1
            status = "高置信度"
        elif score >= 0.02:
            dp_mid += 1
            status = "中等置信度"
        else:
            dp_low += 1
            status = "低置信度"

        print(
            f"  [docx] {status}: 表{ti+1} → P{matched_page}"
            f" (J={score:.3f}, 公共词={common_cnt}) [{ctx_review}]"
        )

    print(f"  [docx] DP匹配质量: 高={dp_high}, 中={dp_mid}, 低={dp_low}")
    logger.info("阶段3 DP匹配质量: 高=%d, 中=%d, 低=%d/%d",
                dp_high, dp_mid, dp_low, num_tables)

    # ===== 阶段4：未匹配表格用邻居插值 =====
    high_conf_indices = {
        ti for ti in range(num_tables)
        if matches[ti] is not None and matches[ti][1] >= HIGH_CONF_JACCARD
    }
    weak_indices = {
        ti for ti in range(num_tables)
        if matches[ti] is not None and matches[ti][1] < HIGH_CONF_JACCARD
    }
    all_matched = high_conf_indices | weak_indices

    if high_conf_indices:
        known = sorted([(i, results[i]["page"]) for i in all_matched])
        unassigned = [i for i in range(num_tables) if i not in all_matched]

        if unassigned and known:
            print(
                f"  [docx] 插值兜底: {len(unassigned)} 个表格通过邻居推算页码..."
            )
            logger.info("阶段4 插值兜底: %d个未匹配表格", len(unassigned))
            for ui in unassigned:
                prev_page = 0
                prev_idx = 0
                next_page = 0

                for ki, kp in known:
                    if ki < ui:
                        prev_page = kp
                        prev_idx = ki
                    elif ki > ui:
                        next_page = kp
                        break

                if prev_page and next_page:
                    if prev_page == next_page:
                        estimated = prev_page
                    else:
                        next_idx = min(k for k, _ in known if k > ui)
                        ratio = (ui - prev_idx) / max(1, next_idx - prev_idx)
                        estimated = round(
                            prev_page + (next_page - prev_page) * ratio
                        )
                elif prev_page:
                    estimated = prev_page
                elif next_page:
                    estimated = next_page
                else:
                    continue

                old_page = results[ui].get("page", 0)
                results[ui]["page"] = max(1, min(estimated, total_pages))
                if results[ui]["page"] != old_page:
                    print(
                        f"  [docx] 插值: 表{ui+1} P{old_page}"
                        f"→P{results[ui]['page']}"
                    )
    else:
        print(
            f"  [docx] 全部{num_tables}个表格无法匹配，保留 docx 原始页码"
        )

    # ===== 阶段5：XY 重叠检测 + V2 修正 =====
    _phase5_xy_overlap_and_v2_correction(results, matches, total_pages, num_tables, logger)

    # ===== 阶段6：单调性 + 边界约束 =====
    _phase6_monotonic_and_boundary(results, total_pages, num_tables, logger)

    hc = len(high_conf_indices)
    wc = len(weak_indices)
    nm = num_tables - hc - wc
    print(
        f"  [docx] 页码分配: 高置信度={hc}, 弱匹配={wc}, "
        f"未匹配={nm} / {num_tables}"
    )
    logger.info("=== DP完毕: 高=%d 弱=%d 未匹配=%d / %d ===",
                hc, wc, nm, num_tables)
    return results


# ============================================================
# 主入口：修正版全局DP优先，旧方案 fallback
# ============================================================

def assign_docx_pages(results, context):
    """为 docx 表格分配 PDF 页码。

    优先使用修正版全局DP方案（上下文+数据指纹+位置先验 评分矩阵
    + 全局DP + 合理性验证 + 局部精修），
    失败时回退到旧版6阶段DP流水线。

    Args:
        results: 表格结果列表，每项含 "data"、"page"、"context_text" 等字段
        context: PDFContext 实例

    Returns:
        results: 页码已修正的结果列表（原地修改）
    """
    if not results or not context:
        return results

    total_pages = context.page_count
    if total_pages <= 1:
        return results

    num_tables = len(results)
    logger = _get_dp_logger(context)
    logger.info("===== 修正版全局DP方案 =====")

    # ---- 尝试新方案 ----
    try:
        # 步骤0：V2-Lite 扫描
        region_map = _scan_table_regions_lite(context)
        logger.info("V2-Lite 扫描完成")

        # 步骤1：提取每页非表格区域文本
        page_contexts = _extract_page_context_texts(context, region_map)
        ctx_count = sum(1 for v in page_contexts.values() if v.strip())
        logger.info("页级上下文 %d/%d 页有文本", ctx_count, total_pages)

        # 步骤1b：提取每页完整文本
        full_page_texts = _extract_full_page_texts(context)
        full_count = sum(1 for v in full_page_texts.values() if v.strip())
        logger.info("完整页文本 %d/%d 页有数据", full_count, total_pages)

        # 步骤2：修正版全局DP分配
        results = _assign_by_global_dp(
            results, page_contexts, full_page_texts, region_map,
            total_pages, logger
        )
        logger.info("修正版全局DP完成: %d 张表", num_tables)

    except Exception as exc:
        logger.warning("修正版全局DP异常: %s，回退到旧方案", exc)
        print(f"  [docx] 修正版全局DP异常，回退到旧方案: {exc}")
        results = _assign_docx_pages_legacy(results, context, logger)

    return results
