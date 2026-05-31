# -*- coding: utf-8 -*-
"""
页码分配模块 — 为 docx 通道提取的表格分配正确的 PDF 页码。

独立于 processor.py，方便单独测试和优化。
核心流水线（7阶段）：
  阶段0：V2-Lite 扫描所有页面 → 表格区域坐标 + 文本
  阶段1：为每个 docx 表格构建数据指纹（跳过表头，取唯一数据值）
  阶段2：Jaccard 相似度匹配（docx 表 ↔ V2-Lite 区域）+ 滑动窗口 DP
  阶段2.5：锚点插值 — 高置信度表为锚点，中间零分表沿候选页梯度均匀分布
  阶段3：根据置信度汇总匹配质量
  阶段4：未匹配表格用邻居插值兜底
  阶段5：V2-Lite bbox 同页 XY 矩形重叠检测 + 大跨跳修正
  阶段6：单调递增约束 + 页数边界约束
"""

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
        # 与 get_pdf_cache_dir 保持一致
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

    logger.info("=== DP 调试日志 ===")
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

    # 滑动窗口DP — 候选页距离惩罚
    # 防止DP在无信号区盲目跳变（如P290→P365跳75位），
    # 只有高分匹配(>0.15)才能克服远距离跳变的代价。
    "distance_penalty": 0.002,

    # pdfplumber降级
    "pdfplumber_min_words": 20,
    "pdfplumber_min_row_words": 3,
}

# Jaccard 匹配阈值
MIN_COMMON = 2           # 最少公共词数
HIGH_CONF_JACCARD = 0.08 # 高置信度阈值

# Bbox 检测阈值
BBOX_MIN_CONF = 0.02         # bbox 参与检测的最低 Jaccard 分数
XY_OVERLAP_THRESHOLD = 0.05  # 重叠率<5% 视为不重叠（多栏/边距误差）


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

    # 合并连续行
    table_row_ranges = _merge_consecutive(table_row_indices)

    # 只检测上下边界（行），左右边界交给列检测处理
    regions = []
    for start, end in table_row_ranges:
        y0 = start * cell_h
        y1 = (end + 1) * cell_h
        regions.append((0, y0, page_width, y1))

    return regions


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

        # 金融关键词 + 文本长度过滤（与 V2 一致）
        full_text = " ".join(w["text"] for w in words)
        if not any(kw in full_text for kw in cfg["financial_keywords"]):
            result.append({"page": pn + 1, "regions": []})
            continue
        if len(full_text) < cfg["min_text_length"]:
            result.append({"page": pn + 1, "regions": []})
            continue

        # 检测表格区域（无框表格：纯文本密度法）
        table_regions = _detect_table_region_by_text(
            words, page_rect.width, page_rect.height, cfg
        )

        regions_info = []
        for (rx0, ry0, rx1, ry1) in table_regions:
            # 提取区域内文本并清洗
            region_words = [
                w for w in words
                if rx0 <= w["x0"] <= rx1 and ry0 <= w["y0"] <= ry1
                and w["text"].strip()
            ]
            region_text = " ".join(w["text"] for w in region_words)
            if region_text:
                regions_info.append((rx0, ry0, rx1, ry1, region_text))

        result.append({"page": pn + 1, "regions": regions_info})

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
        set of str: 指纹片段集合
    """
    fingerprints = set()
    numeric_values = []
    text_values = []

    # 如果行数 ≤ 2，也把头行包含进来（小表没有数据行）
    start_row = 2 if len(table_data) > 3 else 0

    for row in table_data[start_row:]:
        for cell in row:
            if not cell:
                continue
            s = str(cell).strip()
            if not s or len(s) < 3:
                continue

            # 数值型检测（带千分位、百分号、括号负号）
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

            # 文本型（≥5 字更有区分度）
            if len(s) >= 5:
                text_values.append(s)

    # 优先数值，后补文本
    seen = set()
    for v in numeric_values:
        if v not in seen:
            fingerprints.add(v)
            seen.add(v)
        if len(fingerprints) >= 25:
            break

    if len(fingerprints) < 25:
        for v in text_values:
            if v not in seen:
                fingerprints.add(v)
                seen.add(v)
            if len(fingerprints) >= 25:
                break

    return fingerprints


# ============================================================
# 阶段5-6 辅助函数
# ============================================================

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
    """尝试把 ti 移到 V2-Lite 原始匹配页（单调约束下），不可行返回 None"""
    if matches[ti] is None:
        return None
    v2_page = matches[ti][0]
    cur_page = results[ti]["page"]
    if v2_page == cur_page:
        return None
    # 尽量一步跳到 V2 原始页
    if _can_move_to(ti, v2_page, results, total_pages, num_tables):
        return v2_page
    # 逐步靠近
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
    """阶段5：Bbox XY 矩形重叠检测 + V2-Lite 页码大跨跳修正

    原则：
      1. 绝不重排序。只在原始相邻关系上检测 XY 矩形是否真正重叠。
      2. 同一页两表的 X 和 Y 矩形区域都有重叠（重叠率≥5%）才算冲突。
      3. 多栏布局（Y有重叠但X不重叠）不视为冲突。
      4. 先做 V2-Lite 原始页码大跨跳修正（解决 Phase1 粗分配偏差）。
      5. 冲突时优先移低分表到 V2 原始页；无冲突的也尝试 V2 页微调。
      6. 单调性约束 + 表格顺序绝对不变。
      7. 【铁律】跨页 bbox 禁用：表被移离 V2 匹配页后，其 bbox 来自旧页，
         不能再用于新页上的 XY 重叠比较，否则产生虚假重叠导致死循环。
    """
    # ---- 5.0 V2-Lite 原始页码大跨跳修正 ----
    # Phase1 线性插值可能把表分到离 V2 原页很远的位置，
    # 这里把 Δ>1 的表尝试拉回 V2 原始页（受单调约束）
    v2_page_fixes = 0
    for ti in range(num_tables):
        if matches[ti] is None:
            continue
        v2_page = matches[ti][0]
        cur_page = results[ti]["page"]
        if abs(v2_page - cur_page) <= 1:
            continue
        new_page = _try_v2_correction(
            ti, matches, results, total_pages, num_tables
        )
        if new_page is not None and new_page != cur_page:
            old = cur_page
            results[ti]["page"] = new_page
            v2_page_fixes += 1
            ctx_preview = results[ti].get("context_text", "")[:30]
            print(
                f"  [docx] Phase5 V2页码修正: 表{ti+1} P{old}→P{new_page}"
                f" (V2原始=P{v2_page}, Δ={abs(v2_page - old)}) [{ctx_preview}]"
            )
    if v2_page_fixes:
        print(f"  [docx] Phase5 V2页码修正: {v2_page_fixes} 个")
        if logger:
            logger.info("阶段5 V2页码大跨跳修正: %d个", v2_page_fixes)

    # ---- 5.1 同页表格 XY 矩形重叠检测（从上到下） ----
    xy_conflicts = 0
    max_iters = num_tables * 3
    iters = 0
    idx = 1

    # 振荡检测：记录每张表的 (from→to) 移动对，反向对出现即锁死
    move_pairs = {ti: set() for ti in range(num_tables)}
    locked_tables = set()  # 已被锁定的表，不再参与 XY 移动

    while idx < num_tables and iters < max_iters:
        iters += 1

        # 不同页 → 跳过
        if results[idx]["page"] != results[idx - 1]["page"]:
            idx += 1
            continue

        # 两表都需有效 bbox
        if not _has_valid_bbox(idx - 1, matches) or not _has_valid_bbox(idx, matches):
            idx += 1
            continue

        ti_prev, ti_cur = idx - 1, idx

        # 【铁律】跨页 bbox 禁用：
        # 如果任一张表当前页码 ≠ 其 V2 原始匹配页，
        # 说明该表已被移动过，其 bbox 来自旧页，不能用于新页比较。
        if ti_prev not in locked_tables:
            if matches[ti_prev] is not None and results[ti_prev]["page"] != matches[ti_prev][0]:
                idx += 1
                continue
        if ti_cur not in locked_tables:
            if matches[ti_cur] is not None and results[ti_cur]["page"] != matches[ti_cur][0]:
                idx += 1
                continue

        # 获取两表的 XY 坐标
        y0_prev, y1_prev = matches[ti_prev][3], matches[ti_prev][4]
        x0_prev, x1_prev = matches[ti_prev][5], matches[ti_prev][6]
        y0_cur, y1_cur = matches[ti_cur][3], matches[ti_cur][4]
        x0_cur, x1_cur = matches[ti_cur][5], matches[ti_cur][6]

        # 无 Y 重叠（prev 完全在 cur 上方）→ 无冲突
        if y1_prev <= y0_cur:
            idx += 1
            continue

        # Y 有重叠，检查 XY 矩形重叠率
        overlap = _rect_overlap_ratio(ti_prev, ti_cur, matches)

        # 重叠率 < 阈值 → 可能是多栏布局或贴边误差，不视为冲突
        if overlap < XY_OVERLAP_THRESHOLD:
            idx += 1
            continue

        # === XY 矩形确实重叠 ===
        P = results[ti_prev]["page"]
        sc_prev = matches[ti_prev][1]
        sc_cur = matches[ti_cur][1]

        # 优先移低分表
        move_ti = ti_prev if sc_prev <= sc_cur else ti_cur

        cur_pg = results[move_ti]["page"]

        new_page = _try_v2_correction(
            move_ti, matches, results, total_pages, num_tables
        )
        if new_page is None:
            # fallback: ±1 页
            old_pg = results[move_ti]["page"]
            if P < total_pages and _can_move_to(move_ti, P + 1, results, total_pages, num_tables):
                new_page = P + 1
            elif P > 1 and _can_move_to(move_ti, P - 1, results, total_pages, num_tables):
                new_page = P - 1

        if new_page is None:
            locked_tables.add(move_ti)  # 无可移位置，锁定
            idx += 1
            continue

        # 【铁律】振荡检测：(cur→new) 的反向对 (new→cur) 是否已存在
        pair = (cur_pg, new_page)
        reverse_pair = (new_page, cur_pg)
        if reverse_pair in move_pairs[move_ti]:
            print(
                f"  [docx] Phase5 振荡锁定: 表{move_ti+1} P{cur_pg}"
                f"⇄P{new_page} 反向移动对已存在，锁定"
            )
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
        # 回溯重检（移动后可能影响前一个相邻对）
        idx = max(1, idx - 1)

    if iters >= max_iters:
        print(
            f"  [docx] ⚠ Phase5 XY重叠检测达到迭代上限"
            f"({max_iters})，强制停止"
        )
        if logger:
            logger.warning("阶段5 XY重叠检测达到迭代上限(%d)", max_iters)
    if xy_conflicts:
        print(f"  [docx] Phase5 XY重叠修正: {xy_conflicts} 个")
        if logger:
            logger.info("阶段5 XY重叠修正: %d个", xy_conflicts)


# ============================================================
# 阶段2.5：锚点插值
# ============================================================

def _anchor_interpolation(results, page_fingerprints, matches, num_tables,
                          total_pages, logger=None):
    """阶段2.5：用高置信度锚点表驱动中间零分/低分表的均匀分布。

    核心思想：
      1. 找出所有高置信度匹配的表作为"锚点"（Jaccard >= HIGH_CONF_JACCARD）
      2. 相邻锚点之间的非锚点表按线性插值均匀分配到中间候选页
      3. 锚点表本身不移动
      4. 首锚点之前和末锚点之后的表保持 DP 原分配

    这解决了滑动窗口 DP 的棘轮效应：
      零分表不再全部挤在 committed_lower 页面，
      而是沿锚点间的候选页梯度均匀摊开。

    Args:
        results: DP 初分配后的表格结果列表
        page_fingerprints: 候选页列表 [(page_num, token_set), ...]
        matches: 区域匹配结果 [(page, score, common, y0, y1, x0, x1), None, ...]
        num_tables: 表格总数
        total_pages: PDF 总页数
        logger: DP 调试日志器

    Returns:
        None（原地修改 results["page"] 和 matches[ti] 中的 v2_page 字段）

    副作用说明：
        插值时同步更新 matches[ti] 的第0元素（v2_page）为插值页码，
        防止阶段5把插值结果当作"大跨跳（|Δ|>1）"误修正回旧页。
    """
    # 1. 识别锚点表
    anchors = []  # [(table_index, page_number), ...]
    for ti in range(num_tables):
        if matches[ti] is not None and matches[ti][1] >= HIGH_CONF_JACCARD:
            anchors.append((ti, results[ti]["page"]))

    if len(anchors) < 2:
        if logger:
            logger.info("阶段2.5 锚点插值: 锚点不足(%d个, 需要>=2)，跳过",
                        len(anchors))
        return

    # 2. 构建 page_fingerprints 的页码→索引映射（快速查找）
    page_to_idx = {pn: pi for pi, (pn, _) in enumerate(page_fingerprints)}

    # 3. 构建索引→页码数组（快速取值）
    page_idx_to_num = [pn for pn, _ in page_fingerprints]

    interpolated = 0

    # 4. 逐对锚点处理
    for a in range(len(anchors) - 1):
        left_ti, left_page = anchors[a]
        right_ti, right_page = anchors[a + 1]

        # 查找锚点页码在 page_fingerprints 中的索引
        left_idx = page_to_idx.get(left_page)
        right_idx = page_to_idx.get(right_page)
        if left_idx is None or right_idx is None:
            continue

        # 中间表的范围（不包含锚点表本身）
        middle_tables = list(range(left_ti + 1, right_ti))
        if not middle_tables:
            continue

        idx_span = right_idx - left_idx
        ti_span = right_ti - left_ti

        for ti in middle_tables:
            # 跳过已经是高置信度的表（理论上不应出现，但安全处理）
            if matches[ti] is not None and matches[ti][1] >= HIGH_CONF_JACCARD:
                continue

            old_page = results[ti]["page"]

            # 线性插值：按表序号占比映射到候选页索引
            ratio = (ti - left_ti) / ti_span
            target_idx = left_idx + round(idx_span * ratio)
            # 边界保护
            target_idx = max(0, min(target_idx, len(page_idx_to_num) - 1))
            new_page = page_idx_to_num[target_idx]

            # 页数边界约束
            new_page = max(1, min(new_page, total_pages))

            if new_page != old_page:
                results[ti]["page"] = new_page
                interpolated += 1
                # 同步更新 matches 中的 v2_page，防止阶段5把插值结果
                # 当作"大跨跳"误修正回旧页（matches[ti] 是 tuple，需重建）
                if matches[ti] is not None:
                    m = matches[ti]
                    matches[ti] = (new_page,) + m[1:]

        # 记录锚点对信息（供日志输出）
        mid_count = len(middle_tables)
        if mid_count > 0 and logger:
            logger.debug(
                "阶段2.5 锚点对 表%d(P%d)→表%d(P%d): %d个中间表, idx跨%d位",
                left_ti + 1, left_page, right_ti + 1, right_page,
                mid_count, idx_span
            )

    # ---- 日志 ----
    if interpolated > 0:
        print(
            f"  [docx] 锚点插值: {interpolated} 个表在 "
            f"{len(anchors)} 个锚点间重新分配"
        )

    if logger:
        logger.info("阶段2.5 锚点插值: %d个锚点, %d个表重新分配",
                    len(anchors), interpolated)


def _phase6_monotonic_and_boundary(results, total_pages, num_tables, logger=None):
    """阶段6：【铁律】单调递增 + 页数边界约束

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

    # 页数边界约束：不超出 PDF 总页数
    for r in results:
        r["page"] = max(1, min(r["page"], total_pages))

    if monotonic_fixes > 0 and logger:
        logger.warning("阶段6 单调递增修正: %d个表", monotonic_fixes)


# ============================================================
# 主入口：6 阶段页码分配流水线
# ============================================================

def assign_docx_pages(results, context):
    """基于 V2-Lite 物理坐标匹配的页码验证与分配。

    Args:
        results: 表格结果列表，每项含 "data"（表格数据行）、"page"（当前页码）、
                 "context_text"（上下文文本前缀）等字段
        context: PDFContext 实例

    Returns:
        results: 页码已修正的结果列表（原地修改）

    架构：
        阶段0：V2-Lite 扫描所有页面，获得准确的表格区域坐标（页码+bbox）和文本
        阶段1：为每个 docx 表格构建数据指纹（跳过表头，取唯一数据值）
        阶段2：每个 docx 表与所有 V2-Lite 区域做 Jaccard 相似度匹配 + 滑动窗口 DP
        阶段2.5：高置信度锚点表驱动零分/低分表均匀分布到中间候选页
        阶段3：匹配质量汇总（高/中/低置信度）
        阶段4：未匹配表格用邻居插值兜底
        阶段5：V2-Lite bbox 同页 XY 冲突检测（替代不可靠的文本估测）
        阶段6：单调递增约束 + 页数边界约束
    """
    if not results or not context:
        return results

    total_pages = context.page_count
    if total_pages <= 1:
        return results

    num_tables = len(results)

    # ---- 初始化 DP 日志 ----
    logger = _get_dp_logger(context)
    logger.info("PDF总页数=%d, 待分配表格数=%d", total_pages, num_tables)
    docx_pages = [r.get("page", 0) for r in results]
    logger.info("docx原始页码范围: P%d ~ P%d", min(docx_pages), max(docx_pages))
    logger.info("窗口大小=%d, 步长=%d, 高置信阈值=%.3f, 最小公共词=%d",
                4, 2, HIGH_CONF_JACCARD, MIN_COMMON)

    # ===== 阶段0：V2-Lite 扫描 =====
    region_map = _scan_table_regions_lite(context)

    # 构建所有区域的统一索引（保留 bbox 信息，含 X 坐标供 XY 矩形重叠检测）
    # all_regions = [(page, token_set, x0, y0, x1, y1), ...]
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

    # ---- 日志：V2-Lite 扫描汇总 ----
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
        fp_lower = {v.lower() for v in fp}
        table_fingerprints.append(fp_lower)

    # ---- 日志：每张表的指纹摘要 ----
    for ti in range(num_tables):
        fp = table_fingerprints[ti]
        docx_page = results[ti].get("page", 0)
        ctx = results[ti].get("context_text", "")[:40]
        logger.debug("阶段1 表%d: docx原始P%d, 指纹词数=%d, ctx=%s",
                      ti + 1, docx_page, len(fp), ctx)

    # ===== 阶段2：滑动窗口 DP 页码分配 =====
    # 核心思路：
    #   1. pdf2docx 表格序号 = 物理出现顺序 → 页码必须单调非递减
    #   2. 相邻 4 张表放在同一窗口同时决策 → 防止 Jaccard 分数"偷跑"到同一页
    #   3. 搜索空间覆盖所有 PDF 页面（含无表格区域的空通道页）
    #   4. 步长 2（50% 重叠）→ 保证窗口间不脱节

    # ---- 2a: 构建页级指纹 ----
    # 将所有 PDF 页面纳入候选页列表（含无 V2-Lite 区域的页面）。
    # 无区域页面分配空 token set → score 恒为 0 → 仅作为零分表的通道，
    # 不会被高分表主动选中。这解决了候选页断层导致零分表全部挤在断层两端的灾难。
    page_fingerprints = []  # [(page_num, page_token_set), ...]
    pages_with_regions = set()

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
            pages_with_regions.add(page_num)

    # 补充无 V2-Lite 区域的页面（空 token set，纯通道页）
    empty_pages_added = 0
    for pn in range(1, total_pages + 1):
        if pn not in pages_with_regions:
            page_fingerprints.append((pn, set()))
            empty_pages_added += 1

    # 按页码排序（补充的空页已按顺序追加，但安全起见排一下）
    page_fingerprints.sort(key=lambda x: x[0])

    if empty_pages_added > 0:
        print(
            f"  [docx] 候选页补全: 新增 {empty_pages_added} 个无表格区域页, "
            f"共 {len(page_fingerprints)} 个候选页"
        )
        logger.info(
            "阶段2 候选页补全: V2区域=%d, 空通道页=%d, 总计=%d",
            len(pages_with_regions), empty_pages_added, len(page_fingerprints)
        )

    if not page_fingerprints:
        print("  [docx] V2-Lite 未检测到任何表格区域，保留 docx 原始页码")
        return results

    num_candidate_pages = len(page_fingerprints)
    print(
        f"  [docx] 滑动窗口DP: {num_tables}张表 → "
        f"{num_candidate_pages}个候选页"
    )

    # ---- 日志：候选页映射 ----
    candidate_page_map = {pi: pn for pi, (pn, _) in enumerate(page_fingerprints)}
    logger.info("阶段2 候选页映射: %s", candidate_page_map)

    # ---- 2b: 预计算 score 矩阵 ----
    # score_matrix[ti][pi] = 表 ti 与候选页 pi 的页级 Jaccard 得分
    score_matrix = []
    for ti, fp in enumerate(table_fingerprints):
        row = []
        if not fp:
            # 空指纹表 → 全部 0（DP 会将其塞到邻居旁边）
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

    # ---- 日志：score 矩阵概览（每表 top5 高分候选页） ----
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

    # ---- 2c: 滑动窗口 DP ----
    WINDOW_SIZE = 4
    WINDOW_STEP = 2

    # assigned_page_idx[ti] = pi (index into page_fingerprints) or -1
    assigned_page_idx = [-1] * num_tables
    committed_lower = 0  # 下一窗口的候选页起始索引

    for start_ti in range(0, num_tables, WINDOW_STEP):
        end_ti = min(start_ti + WINDOW_SIZE, num_tables)
        window_len = end_ti - start_ti

        candidate_start = committed_lower
        candidate_end = num_candidate_pages

        if candidate_start >= candidate_end:
            # 候选页已用完 → 剩余表全部塞到最后一页
            last_pi = candidate_end - 1
            for ti in range(start_ti, num_tables):
                assigned_page_idx[ti] = last_pi
            break

        num_cols = candidate_end - candidate_start

        # dp[k][col] = 窗口内前 k 张表，最后一张分到候选页 (candidate_start+col) 的最优总分
        dp = [[-1e9] * num_cols for _ in range(window_len)]
        # prev[k][col] = 该状态下前一张表分到候选页的索引
        prev = [[-1] * num_cols for _ in range(window_len)]

        for k in range(window_len):
            ti = start_ti + k
            for p in range(candidate_start, candidate_end):
                col = p - candidate_start
                cur_score = score_matrix[ti][p]

                if k == 0:
                    dp[k][col] = cur_score
                    prev[k][col] = -1
                else:
                    best_prev_score = -1e9
                    best_prev_p = -1
                    for q in range(candidate_start, p + 1):
                        q_col = q - candidate_start
                        if dp[k - 1][q_col] > best_prev_score:
                            best_prev_score = dp[k - 1][q_col]
                            best_prev_p = q
                    if best_prev_score > -1e9:
                        # 候选页距离惩罚：抑制无信号区的盲目跳变
                        page_dist = (candidate_start + col) - best_prev_p
                        dp[k][col] = (cur_score + best_prev_score
                                      - V2_CONFIG["distance_penalty"] * page_dist)
                        prev[k][col] = best_prev_p

        # 回溯找最优路径
        last_k = window_len - 1
        best_total = -1e9
        best_last_p = candidate_start
        for p in range(candidate_start, candidate_end):
            col = p - candidate_start
            if dp[last_k][col] > best_total:
                best_total = dp[last_k][col]
                best_last_p = p

        # 追溯路径
        window_assignment = [-1] * window_len
        cur_p = best_last_p
        for k in range(window_len - 1, -1, -1):
            window_assignment[k] = cur_p
            if k > 0:
                col = cur_p - candidate_start
                cur_p = prev[k][col]

        # 提交：非最后窗口提交前 WINDOW_STEP 张，最后窗口提交全部
        commit_count = min(WINDOW_STEP, window_len)
        for k in range(commit_count):
            ti = start_ti + k
            assigned_page_idx[ti] = window_assignment[k]
            committed_lower = window_assignment[k]

        # ---- 日志：窗口 DP 路径 ----
        win_pages = [page_fingerprints[window_assignment[k]][0] for k in range(window_len)]
        logger.debug(
            "阶段2 窗口[%d:%d]: 候选页范围[%d,%d), DP最优总分=%.4f, 路径=%s",
            start_ti, end_ti, candidate_start, candidate_end, best_total, win_pages
        )

    # ---- 2d: 区域级匹配（为每张表确定 bbox）----
    # 每张表已确定属于哪一页，现在该页内找最佳 V2-Lite 区域获取坐标
    matches = [None] * num_tables

    for ti in range(num_tables):
        pi = assigned_page_idx[ti]
        if pi < 0:
            continue

        page_num = page_fingerprints[pi][0]
        fp = table_fingerprints[ti]

        # 在该页的所有区域中找最佳匹配
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
            # 兜底：取该页第一个区域作为坐标参考（至少 Y 坐标可用）
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

    # ---- 2e: 将 DP 分配结果写入 results ----
    dp_page_changes = 0
    for ti in range(num_tables):
        if assigned_page_idx[ti] >= 0:
            old_page = results[ti].get("page", 0)
            new_page = page_fingerprints[assigned_page_idx[ti]][0]
            results[ti]["page"] = new_page
            if new_page != old_page:
                dp_page_changes += 1
                # 日志：记录每处页码变动及区域匹配得分
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

    # ===== 阶段2.5：锚点插值（零分表均匀分布）=====
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
            ctx_preview = results[ti].get("context_text", "")[:50]
            print(f"  [docx] 表{ti+1}: 无匹配 [{ctx_preview}]")
            logger.debug("阶段3 表%d: 无匹配 [%s]", ti + 1, ctx_preview)
            continue

        matched_page, score, common_cnt, _y0, _y1, _x0, _x1 = matches[ti]
        ctx_preview = results[ti].get("context_text", "")[:40]

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
            f" (J={score:.3f}, 公共词={common_cnt}) [{ctx_preview}]"
        )

    print(
        f"  [docx] DP匹配质量: 高={dp_high}, 中={dp_mid}, 低={dp_low}"
    )
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
        unassigned = [
            i for i in range(num_tables)
            if i not in all_matched
        ]

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

    # ===== 阶段5：Bbox XY 矩形重叠检测 + V2-Lite 页码大跨跳修正 =====
    _phase5_xy_overlap_and_v2_correction(results, matches, total_pages, num_tables, logger)

    # ===== 阶段6：【铁律】原始顺序单调递增 + 页数边界约束 =====
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
