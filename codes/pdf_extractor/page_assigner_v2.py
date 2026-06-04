# ============================================================
# 新方案：上下文文本定位页码（替代旧版6阶段DP流水线）
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
    # 构建页号 → 表格区域列表 的映射（加速排除判断）
    page_regions = {}
    for info in region_map:
        pn = info["page"]
        rects = [(r[0], r[1], r[2], r[3]) for r in info["regions"]]
        page_regions[pn] = rects

    page_contexts = {}

    for pn in range(1, total + 1):
        try:
            words = context.get_words(pn - 1)  # fitz 页号 0-based
        except Exception:
            page_contexts[pn] = ""
            continue

        if not words:
            page_contexts[pn] = ""
            continue

        # 获取该页的表格区域
        rects = page_regions.get(pn, [])

        # 排除落在任何表格区域内的 words
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

        # 按 (y0, x0) 排序，拼接为文本
        filtered.sort(key=lambda w: (w["y0"], w["x0"]))
        page_contexts[pn] = " ".join(w["text"] for w in filtered)

    return page_contexts


def _estimate_first_table_range(total_pages, num_tables):
    """估算第一个表的合理搜索范围（避免全局搜索）。"""
    if num_tables == 0:
        return 1, min(20, total_pages)

    density = total_pages / num_tables
    upper = int(density * 2.5)
    upper = min(upper, int(total_pages * 0.1))
    upper = max(upper, 10)
    return 1, min(upper, total_pages)


def _match_context_text(context_text, page_contexts, search_start, search_end):
    """将 docx 表格的上下文文本匹配到 PDF 页。

    匹配策略（分级）：
      1. 精确匹配：context_text 中任意一行在页文本中出现
      2. 模糊匹配：Jaccard 相似度（对 context_text 分词后计算）
      3. 返回最佳匹配页号和置信度

    Args:
        context_text: str, docx 表格的上下文文本（可能含换行）
        page_contexts: {page_num: str}
        search_start: 起始搜索页号（含）
        search_end: 结束搜索页号（含）

    Returns:
        (best_page, confidence) 或 (None, 0.0)
    """
    if not context_text or not context_text.strip():
        return None, 0.0

    # 将 context_text 拆分为行，便于精确匹配
    ctx_lines = [line.strip() for line in context_text.strip().splitlines() if line.strip()]
    # 同时构建一个"锚定片段"：取最长且有区分度的行（>=10个字符）
    anchor_lines = [l for l in ctx_lines if len(l) >= 10]
    if not anchor_lines:
        anchor_lines = [l for l in ctx_lines if len(l) >= 5]
    if not anchor_lines:
        anchor_lines = [context_text.strip()[:50]]

    best_page = None
    best_score = 0.0
    match_type = "none"

    for pn in range(search_start, search_end + 1):
        if pn not in page_contexts:
            continue
        pt = page_contexts[pn]
        if not pt:
            continue

        pt_lower = pt.lower()

        # --- 1. 精确匹配（任意 anchor_line 出现在页文本中）---
        for al in anchor_lines:
            if len(al) < 8:
                continue
            if al.lower() in pt_lower:
                best_page = pn
                best_score = 1.0
                match_type = "exact"
                break  # 该页已命中，不需要再试其他 anchor_line

        if match_type == "exact" and best_page == pn:
            continue  # 已精确命中，跳过模糊匹配

        # --- 2. 模糊匹配（Jaccard）---
        ctx_tokens = set(t.lower() for t in context_text.split() if len(t) >= 2)
        pt_tokens = set(t.lower() for t in pt.split() if len(t) >= 2)
        if not ctx_tokens or not pt_tokens:
            continue

        common = ctx_tokens & pt_tokens
        common_cnt = len(common)
        if common_cnt < 3:
            continue

        union = ctx_tokens | pt_tokens
        if not union:
            continue

        jacc = common_cnt / len(union)
        score = jacc + 0.01 * common_cnt

        if score > best_score and jacc >= 0.2:
            best_score = score
            best_page = pn

    return best_page, best_score


def _fallback_table_data_match(table_data, page_contexts, start_p, end_p):
    """兜底：用表格数据构建指纹，与页文本做 Jaccard 匹配。

    当上下文文本为空或匹配失败时调用。
    """
    if not table_data or not table_data[0]:
        return None, 0.0

    # 构建数据指纹（与 _build_table_data_fingerprint 逻辑一致）
    fp = set()
    start_row = 2 if len(table_data) > 3 else 0
    for row in table_data[start_row:]:
        for cell in row:
            s = str(cell).strip()
            if len(s) >= 3:
                fp.add(s.lower())

    if not fp:
        return None, 0.0

    best_page = None
    best_score = 0.0

    for pn in range(start_p, end_p + 1):
        if pn not in page_contexts:
            continue
        pt = page_contexts[pn]
        if not pt:
            continue

        pt_tokens = set(t.lower() for t in pt.split() if len(t) >= 2)
        common = fp & pt_tokens
        common_cnt = len(common)
        if common_cnt < 2:
            continue

        union = fp | pt_tokens
        if not union:
            continue

        jacc = common_cnt / len(union)
        score = jacc + 0.015 * common_cnt

        if score > best_score:
            best_score = score
            best_page = pn

    return best_page, best_score


def _sliding_window_verify(results, page_contexts, total_pages, window_size, logger):
    """滑动窗口验证：每 WINDOW 张表做一次局部联合检查。

    如果发现窗口内某张表的页码与邻居冲突（如 [P10, P10, P50]），
    则对该窗口重新做联合匹配。
    """
    num_tables = len(results)
    verified = 0

    for start in range(0, num_tables, window_size - 1):  # 重叠1张，保证连续性
        end = min(start + window_size, num_tables)
        pages = [results[i].get("page", 0) for i in range(start, end)]

        is_ok = True
        for k in range(1, len(pages)):
            if pages[k] - pages[k - 1] > 10:  # 单步跳变 >10 页视为异常
                is_ok = False
                break

        if is_ok:
            continue

        # 异常 → 对窗口内所有表重新联合匹配
        logger.info("窗口[%d:%d] 检测到页码跳变 %s，重新匹配", start, end, pages)
        prev_page = results[start - 1].get("page", 1) if start > 0 else 1

        for ti in range(start, end):
            win_start = max(1, prev_page - 2)
            win_end = min(total_pages, prev_page + 8)  # 验证时适当扩大范围

            ctx = results[ti].get("context_text", "")
            matched_page, score = _match_context_text(
                ctx, page_contexts, win_start, win_end
            )

            if matched_page is None:
                matched_page = prev_page

            if matched_page < prev_page:
                matched_page = prev_page

            results[ti]["page"] = matched_page
            prev_page = matched_page
            verified += 1

    if verified > 0:
        logger.info("滑动窗口验证: 修正了 %d 个表的页码", verified)


def match_tables_by_context_text(results, page_contexts, total_pages, logger):
    """核心新逻辑：按顺序 + 局部滑动窗口，用上下文文本定位页码。

    流程：
      1. 估算第一个表的搜索范围，定位第一个锚点
      2. 顺序处理每张表，搜索范围 = [prev_page - 2, prev_page + 5]
      3. 上下文匹配失败时用表格数据指纹兜底
      4. 单调性保证：当前表页码不低于前一表

    Args:
        results: 表格结果列表（含 context_text 字段）
        page_contexts: {page_num: str} 由 _extract_page_context_texts 生成
        total_pages: PDF 总页数
        logger: 日志记录器

    Returns:
        results（原地修改 page 字段）
    """
    num_tables = len(results)
    if num_tables == 0:
        return results

    # ---- 步骤1：定位第一个表 ----
    start_p, end_p = _estimate_first_table_range(total_pages, num_tables)
    logger.info("新方案 步骤1: 第一个表搜索范围 P%d~P%d", start_p, end_p)

    first_page, first_score = _match_context_text(
        results[0].get("context_text", ""),
        page_contexts, start_p, end_p
    )

    if first_page is None:
        # 第一个表精确+模糊均失败 → 扩大范围到前 30% 页
        logger.warning("第一个表匹配失败，扩大搜索范围 P1~P%d", int(total_pages * 0.3))
        first_page, first_score = _match_context_text(
            results[0].get("context_text", ""),
            page_contexts, 1, int(total_pages * 0.3)
        )

    if first_page is None:
        # 仍失败 → 用 docx 原始页码作为兜底
        logger.warning("第一个表仍无法匹配，使用 docx 原始页码 P%d", results[0].get("page", 1))
        first_page = results[0].get("page", 1)

    results[0]["page"] = first_page
    logger.info("新方案 锚点: 表1 → P%d (置信度=%.4f)", first_page, first_score)

    # ---- 步骤2：顺序匹配剩余表 ----
    WINDOW = 3  # 滑动窗口大小
    for ti in range(1, num_tables):
        prev_page = results[ti - 1].get("page", 1)

        # 搜索范围：[max(1, prev-2), min(total, prev+5)]
        win_start = max(1, prev_page - 2)
        win_end = min(total_pages, prev_page + 5)

        ctx = results[ti].get("context_text", "")
        matched_page, score = _match_context_text(
            ctx, page_contexts, win_start, win_end
        )

        if matched_page is None:
            # 上下文文本匹配失败 → 用表格数据指纹兜底
            logger.debug("表%d 上下文匹配失败，尝试数据指纹兜底", ti + 1)
            matched_page, score = _fallback_table_data_match(
                results[ti].get("data", []),
                page_contexts, win_start, win_end
            )

        if matched_page is None:
            # 仍失败 → 沿用上一表的页码
            matched_page = prev_page
            score = 0.0
            logger.debug("表%d 完全匹配失败，沿用前表页码 P%d", ti + 1, prev_page)

        # 单调性保证：当前表页码不能低于前一表
        if matched_page < prev_page:
            matched_page = prev_page
            logger.debug("表%d 匹配页 P%d < 前页 P%d，修正为 P%d",
                         ti + 1, matched_page, prev_page, prev_page)

        results[ti]["page"] = matched_page
        logger.debug("新方案: 表%d → P%d (置信度=%.4f)", ti + 1, matched_page, score)

    # ---- 步骤3：滑动窗口局部联合验证（每 WINDOW 张表）----
    logger.info("新方案 步骤3: 滑动窗口(%d表)局部联合验证", WINDOW)
    _sliding_window_verify(results, page_contexts, total_pages, WINDOW, logger)

    return results
