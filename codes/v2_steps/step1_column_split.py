# -*- coding: utf-8 -*-
"""
Step 1: 表格线感知列切分

核心能力:
- 从 PyMuPDF get_drawings() 提取竖线/横线
- 三指令融合列检测（线条锚点 + 文本对齐聚簇 + gap 兜底）
- 单词网格填充 → 2D table_data
- 置信度评分

从 processor.py 迁移而来，消除 self.V2_CONFIG 依赖。
"""

import statistics
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any

from .models import PipelineContext
from .config import V2Config


class Step1ColumnSplit:
    """表格线感知列切分（V2 Step 1）

    独立于 PDFProcessor，所有配置通过参数传入。
    默认配置从 V2Config.STEP1_DEFAULTS 获取。
    """

    @classmethod
    def execute(cls, ctx: PipelineContext, config: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """主入口：处理单页

        Args:
            ctx: PipelineContext（含 page/words/drawings/page_rect）
            config: 步骤配置字典，None 则用默认

        Returns:
            结果列表：每个 item 为 {"page","type","data","confidence",...}
        """
        if config is None:
            config = V2Config.STEP1_DEFAULTS

        page_num = ctx.page_num
        page = ctx.page
        page_rect = ctx.page_rect
        words = ctx.words
        drawings = ctx.drawings

        results: List[Dict] = []

        # 1. 表格区域定位
        table_regions = cls._detect_table_region(
            drawings, page_rect.width, page_rect.height, config)
        if not table_regions:
            table_regions = cls._detect_table_region_by_text(
                words, page_rect.width, page_rect.height, config)
        if not table_regions:
            print(f"  [V2] 第{page_num}页: 未检测到表格区域，跳过")
            return results

        # 2. ContentSegmenter 段切分离
        from codes.content_segmenter.segmenter import ContentSegmenter
        from codes.content_segmenter.segment_logger import SegmentLogger

        segmenter = ContentSegmenter()
        pdf_stem = Path(ctx.pdf_path).stem if ctx.pdf_path else "unknown"

        for region in table_regions:
            rx0, ry0, rx1, ry1 = region
            region_words = [w for w in words
                            if rx0 <= w["x0"] <= rx1 and ry0 <= w["y0"] <= ry1]

            if len(region_words) < 3:
                continue

            # ---- ContentSegmenter 分割 ----
            def _word_getter(w):
                return (w["x0"], w["x1"], w["y0"], w["y1"], w["text"])

            seg_result = segmenter.segment_region(
                text_items=region_words,
                page_width=page_rect.width,
                page_height=page_rect.height,
                page_number=page_num,
                region_bbox=region,
                item_getter=_word_getter,
            )

            # 记录优化前后对比数据
            try:
                SegmentLogger.log_page_diff(
                    pdf_name=pdf_stem,
                    page_number=page_num,
                    before_regions=[{
                        "bbox": [round(rx0, 2), round(ry0, 2), round(rx1, 2), round(ry1, 2)],
                        "item_count": len(region_words),
                        "text_preview": " ".join(w["text"] for w in region_words[:20]),
                    }],
                    after_segment=seg_result,
                    page_size={"width": round(page_rect.width, 2), "height": round(page_rect.height, 2)},
                )
            except Exception:
                pass

            # 按分割结果分别处理
            paragraph_found_count = 0
            for sr in seg_result.regions:
                srx0, sry0, srx1, sry1 = sr.x0, sr.y0, sr.x1, sr.y1

                if sr.is_paragraph:
                    paragraph_found_count += 1
                    para_result = cls._extract_paragraph(sr, words, page_num)
                    if para_result:
                        results.append(para_result)
                    continue

                # 表格子区域
                sub_region_words = [w for w in words
                                    if rx0 <= w["x0"] <= rx1 and sry0 <= w["y0"] <= sry1]

                if len(sub_region_words) < 3:
                    continue

                # 提取上下文文本
                context_text = cls._extract_context_text(
                    words, srx0, sry0, srx1, sry1)

                # 3. 行边界
                row_bounds = cls._detect_horizontal_lines(
                    page_rect.width, sub_region_words, drawings, config)
                if len(row_bounds) < 2:
                    continue

                # 4. 列边界
                col_bounds = cls._detect_vertical_lines(
                    page_rect.width, sub_region_words, drawings, config)
                if len(col_bounds) < 3:
                    continue

                # 5. 网格填充（原始数据，未规范化）
                table_data = cls._assign_words_to_grid(
                    sub_region_words, row_bounds, col_bounds, config)
                if not table_data or len(table_data) < 2:
                    continue

                # 6. 置信度（基于原始网格）
                has_border = bool([d for d in drawings if d.get("direction") in ("h", "v")])
                confidence = cls._compute_table_confidence(
                    table_data, has_border, words, config)

                results.append({
                    "page": page_num,
                    "type": "table",
                    "data": table_data,
                    "text": " ".join(w["text"] for w in words),
                    "extractor": "v2_position_based",
                    "confidence": confidence,
                    "rows": len(table_data),
                    "cols": len(col_bounds) - 1,
                    "has_border": has_border,
                    "context_text": context_text,
                    # 保存内部数据供 Step 2 使用
                    "_row_bounds": row_bounds,
                    "_col_bounds": col_bounds,
                })

            if paragraph_found_count > 0:
                seg_info = seg_result.to_dict()
                print(f"  [V2] 第{page_num}页: 内容分割 → "
                      f"{seg_info['table_regions']}个表格 + {seg_info['paragraph_regions']}个段落")

        return results

    # ================================================================
    # 段落提取
    # ================================================================

    @classmethod
    def _extract_paragraph(cls, sr, words, page_num: int = 0) -> Optional[Dict]:
        """从分割段提取段落文本"""
        para_words = [w for w in words
                      if sr.x0 <= w["x0"] <= sr.x1 and sr.y0 <= w["y0"] <= sr.y1]
        para_words.sort(key=lambda w: (w["y0"], w["x0"]))

        lines = []
        cur_line = []
        cur_y = None
        for w in para_words:
            if cur_y is None or abs(w["y0"] - cur_y) <= 5.0:
                cur_line.append(w["text"])
                if cur_y is None:
                    cur_y = w["y0"]
            else:
                if cur_line:
                    lines.append(" ".join(cur_line))
                cur_line = [w["text"]]
                cur_y = w["y0"]
        if cur_line:
            lines.append(" ".join(cur_line))

        para_text = "\n".join(lines).strip()
        if para_text and len(para_text) >= 3:
            return {
                "page": page_num,
                "type": "paragraph",
                "data": para_text,
                "text": para_text,
                "extractor": "v2_segmenter",
                "confidence": getattr(sr, "confidence", 0.5),
                "rows": len(lines),
                "cols": 1,
                "bbox": [round(sr.x0, 2), round(sr.y0, 2), round(sr.x1, 2), round(sr.y1, 2)],
            }
        return None

    @classmethod
    def _extract_context_text(cls, words, x0, y0, x1, y1, margin: float = 100.0) -> str:
        """提取表格区域上方的上下文文本"""
        context_words = [
            w for w in words
            if w["y1"] <= y0 and w["y1"] >= y0 - margin
            and w["x0"] >= x0 - 20 and w["x1"] <= x1 + 20
        ]
        context_words.sort(key=lambda w: w["y0"])
        return " ".join(w["text"] for w in context_words).strip()

    # ================================================================
    # 表格区域检测
    # ================================================================

    @staticmethod
    def _detect_table_region(drawings, page_width, page_height,
                             config: Dict[str, Any]) -> List[Tuple[float, float, float, float]]:
        """从 drawing 中检测表格外框区域"""
        rectangles = [
            d for d in drawings
            if d["type"] == "rect"
            and d["x1"] - d["x0"] > page_width * config["table_min_width_ratio"]
            and d["y1"] - d["y0"] > config["table_min_height"]
        ]

        h_lines = [
            d for d in drawings
            if d["type"] == "line" and d["direction"] == "h"
            and d["x1"] - d["x0"] > page_width * config["table_min_width_ratio"]
        ]
        v_lines = [
            d for d in drawings
            if d["type"] == "line" and d["direction"] == "v"
            and d["y1"] - d["y0"] > config["table_min_height"]
        ]

        regions = []

        for rect in rectangles:
            regions.append((rect["x0"], rect["y0"], rect["x1"], rect["y1"]))

        if len(h_lines) >= 2 and len(v_lines) >= 2:
            x0 = min(l["x0"] for l in v_lines)
            x1 = max(l["x1"] for l in v_lines)
            y0 = min(l["y0"] for l in h_lines)
            y1 = max(l["y1"] for l in h_lines)
            if (x1 - x0 > page_width * config["table_min_width_ratio"]
                    and y1 - y0 > config["table_min_height"]):
                if not any(Step1ColumnSplit._has_overlap((x0, y0, x1, y1), [r]) for r in regions):
                    regions.append((x0, y0, x1, y1))

        return regions

    @staticmethod
    def _has_overlap(rect: Tuple[float, float, float, float],
                     regions: List[Tuple[float, float, float, float]]) -> bool:
        """检测两个区域是否重叠"""
        rx0, ry0, rx1, ry1 = rect
        for gx0, gy0, gx1, gy1 in regions:
            if not (rx1 <= gx0 or rx0 >= gx1 or ry1 <= gy0 or ry0 >= gy1):
                return True
        return False

    @staticmethod
    def _detect_table_region_by_text(words, page_width, page_height,
                                     config: Dict[str, Any]) -> List[Tuple[float, float, float, float]]:
        """无框表格区域检测（文本密度法）"""
        if not words or len(words) < 20:
            return []

        grid_rows = config["density_grid"]
        grid_cols = config["density_grid"]
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
            if row_density[r] > avg * config["density_threshold"]
        ]

        if not table_row_indices:
            return []

        table_row_ranges = Step1ColumnSplit._merge_consecutive(table_row_indices)

        regions = []
        for start, end in table_row_ranges:
            y0 = start * cell_h
            y1 = (end + 1) * cell_h
            regions.append((0, y0, page_width, y1))

        return regions

    @staticmethod
    def _merge_consecutive(indices: List[int]) -> List[Tuple[int, int]]:
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

    # ================================================================
    # 行边界检测
    # ================================================================

    @staticmethod
    def _detect_horizontal_lines(page_width: float, words: List[dict],
                                 page_drawings: List[dict],
                                 config: Dict[str, Any]) -> List[Tuple[float, float]]:
        """检测行边界"""
        h_lines = sorted(set(
            d["y0"] for d in page_drawings
            if d["type"] == "line" and d["direction"] == "h"
            and d["x1"] - d["x0"] > page_width * config["table_min_width_ratio"]
        ))

        if len(h_lines) >= 2:
            row_bounds = []
            for i in range(len(h_lines) - 1):
                row_bounds.append((h_lines[i], h_lines[i + 1]))
            # 验证绘图横线产生的行区间是否合理：
            # 如果平均行高超过 50pt，横线很可能是区域边框而非表格行线
            # → 回退到动态阈值分组
            total_height = h_lines[-1] - h_lines[0]
            avg_row_h = total_height / len(row_bounds) if row_bounds else 0
            if avg_row_h <= 50:
                return row_bounds
            # else: fall through to dynamic threshold

        # 无水平线或绘图横线不合理 → 动态阈值分组
        y_threshold = Step1ColumnSplit._compute_dynamic_y_threshold(words, config)
        rows = Step1ColumnSplit._group_words_into_rows(words, y_threshold)

        row_bounds = []
        for row_words in rows:
            if row_words:
                y_top = min(w["y0"] for w in row_words)
                y_bot = max(w["y1"] for w in row_words)
                row_bounds.append((y_top, y_bot))

        return row_bounds

    @staticmethod
    def _compute_dynamic_y_threshold(words: List[dict],
                                     config: Dict[str, Any]) -> float:
        """动态计算行分组阈值"""
        if not words or len(words) < 3:
            return 5.0

        y_positions = sorted(set(w["y0"] for w in words if w["text"].strip()))
        if len(y_positions) < 5:
            return 5.0

        gaps = []
        for i in range(len(y_positions) - 1):
            gap = y_positions[i + 1] - y_positions[i]
            if 0.5 < gap < 50:
                gaps.append(gap)

        if len(gaps) < 3:
            return 5.0

        median_gap = statistics.median(gaps)
        threshold = median_gap * config["y_threshold_factor"]
        return max(config["y_threshold_min"],
                   min(config["y_threshold_max"], threshold))

    @staticmethod
    def _group_words_into_rows(words: List[dict],
                               y_threshold: float) -> List[List[dict]]:
        """按 y 坐标对 words 进行行分组"""
        if not words:
            return []

        sorted_words = sorted(words, key=lambda w: w["y0"])
        rows = []
        current_row = [sorted_words[0]]
        current_y = sorted_words[0]["y0"]

        for w in sorted_words[1:]:
            if abs(w["y0"] - current_y) <= y_threshold:
                current_row.append(w)
                current_y = (current_y + w["y0"]) / 2
            else:
                rows.append(sorted(current_row, key=lambda ww: ww["x0"]))
                current_row = [w]
                current_y = w["y0"]

        if current_row:
            rows.append(sorted(current_row, key=lambda ww: ww["x0"]))

        return rows

    # ================================================================
    # 列边界检测
    # ================================================================

    @classmethod
    def _detect_vertical_lines(cls, page_width: float, words: List[dict],
                               page_drawings: List[dict],
                               config: Dict[str, Any]) -> List[float]:
        """检测列边界（v2.1规格：三指令融合 + 线条锚点增强）

        Returns: [x0, x1, x2, ...] 列分割线位置
        """
        # 提取并去重垂直线
        raw_v_lines = sorted(set(
            d["x0"] for d in page_drawings
            if d["type"] == "line" and d["direction"] == "v"
        ))
        v_lines = cls._merge_nearby_lines(raw_v_lines, config["line_merge_tolerance"])

        inner_lines = [x for x in v_lines
                       if page_width * 0.05 < x < page_width * 0.95]

        # 指令1：垂直线直接切分
        min_line_count = config.get("column_line_min_count", 2)
        if len(v_lines) >= min_line_count and len(inner_lines) >= 1:
            boundaries = sorted(set([0] + v_lines + [page_width]))
            if len(boundaries) >= 3:
                return boundaries

        anchor_lines = inner_lines[:]

        # 指令2：文本对齐聚簇
        x0_list = [w["x0"] for w in words if w["text"].strip()]
        x1_list = [w["x1"] for w in words if w["text"].strip()]

        if x0_list:
            left_aligns = cls._cluster_1d(x0_list, config["align_tolerance"])
            right_aligns = cls._cluster_1d(x1_list, config["align_tolerance"])
            all_aligns = sorted(set(left_aligns + right_aligns))

            if anchor_lines:
                all_aligns = cls._fuse_line_anchors_with_aligns(
                    all_aligns, anchor_lines, config["align_tolerance"])

            if len(all_aligns) >= 3:
                return all_aligns

        # 指令3：gap检测（兜底）
        all_x = sorted(set(x0_list + x1_list))
        if len(all_x) < 3:
            return [0, page_width]

        gaps = []
        gap_positions = []
        for i in range(len(all_x) - 1):
            gap = all_x[i + 1] - all_x[i]
            if gap > 0:
                gaps.append(gap)
                gap_positions.append((all_x[i], all_x[i + 1]))

        if not gaps:
            return [0, page_width]

        median_gap = statistics.median(gaps)
        stdev_gap = statistics.stdev(gaps) if len(gaps) >= 2 else median_gap * 0.5
        gap_threshold = max(
            median_gap + stdev_gap * config["gap_factor"], config["gap_min"])

        boundaries = [0]
        for (left, right), gap in zip(gap_positions, gaps):
            if gap > gap_threshold:
                boundaries.append((left + right) / 2)

        if anchor_lines:
            boundaries = cls._fuse_line_anchors_with_aligns(
                boundaries, anchor_lines, config["gap_min"])
        else:
            boundaries.append(page_width)

        return sorted(set(boundaries))

    # ================================================================
    # 线条辅助方法
    # ================================================================

    @staticmethod
    def _merge_nearby_lines(lines: List[float], tolerance: float) -> List[float]:
        """合并容差范围内的邻近竖线"""
        if not lines:
            return []
        sorted_lines = sorted(lines)
        merged = [sorted_lines[0]]
        for x in sorted_lines[1:]:
            if x - merged[-1] <= tolerance:
                merged[-1] = round((merged[-1] + x) / 2, 1)
            else:
                merged.append(x)
        return merged

    @staticmethod
    def _fuse_line_anchors_with_aligns(aligns: List[float], anchors: List[float],
                                       tolerance: float) -> List[float]:
        """将线条锚点融合到对齐点/边界列表中"""
        if not anchors:
            return sorted(set(aligns))

        result = list(aligns)
        result_set = set(result)

        for anchor in anchors:
            if result:
                nearest = min(result, key=lambda x: abs(x - anchor))
                if abs(nearest - anchor) <= tolerance * 2:
                    if nearest in result_set:
                        idx = result.index(nearest)
                        result[idx] = anchor
                        result_set.discard(nearest)
                        result_set.add(anchor)
                else:
                    if anchor not in result_set:
                        result.append(anchor)
                        result_set.add(anchor)

        return sorted(set(result))

    @staticmethod
    def _cluster_1d(values: List[float], tolerance: float = 4) -> List[float]:
        """一维坐标聚簇，找出文本对齐位置"""
        if not values:
            return []

        sorted_vals = sorted(values)
        clusters = []
        current_cluster = [sorted_vals[0]]

        for v in sorted_vals[1:]:
            if v - current_cluster[-1] <= tolerance:
                current_cluster.append(v)
            else:
                if len(current_cluster) >= 3:
                    clusters.append(sum(current_cluster) / len(current_cluster))
                current_cluster = [v]

        if len(current_cluster) >= 3:
            clusters.append(sum(current_cluster) / len(current_cluster))

        return clusters

    # ================================================================
    # 网格填充
    # ================================================================

    @staticmethod
    def _assign_words_to_grid(words: List[dict],
                              row_bounds: List[Tuple[float, float]],
                              col_bounds: List[float],
                              config: Dict[str, Any]) -> List[List[str]]:
        """将 words 分配到行列网格中（重叠面积法）"""
        n_rows = len(row_bounds)
        n_cols = len(col_bounds) - 1

        if n_rows == 0 or n_cols == 0:
            return []

        grid = [[[] for _ in range(n_cols)] for _ in range(n_rows)]

        for w in words:
            wx0, wy0, wx1, wy1 = w["x0"], w["y0"], w["x1"], w["y1"]
            text = w["text"]

            if not text.strip():
                continue

            # 行分配
            row_idx = None
            center_y = (wy0 + wy1) / 2
            margin = (row_bounds[0][1] - row_bounds[0][0]) * config["row_margin_factor"]
            for r, (y_top, y_bot) in enumerate(row_bounds):
                if (y_top - margin) <= center_y <= (y_bot + margin):
                    row_idx = r
                    break

            # 列分配：重叠面积法
            col_idx = None
            max_overlap = 0
            for c in range(n_cols):
                col_left = col_bounds[c]
                col_right = col_bounds[c + 1]
                overlap = max(0.0, min(wx1, col_right) - max(wx0, col_left))
                if overlap > max_overlap:
                    max_overlap = overlap
                    col_idx = c

            # 兜底：最近列中心
            if col_idx is None:
                center_x = (wx0 + wx1) / 2
                min_dist = float('inf')
                for c in range(n_cols):
                    col_center = (col_bounds[c] + col_bounds[c + 1]) / 2
                    dist = abs(center_x - col_center)
                    if dist < min_dist:
                        min_dist = dist
                        col_idx = c

            if row_idx is not None and col_idx is not None:
                grid[row_idx][col_idx].append(text)

        # 合并单元格文本
        result = []
        for r in range(n_rows):
            row_data = []
            for c in range(n_cols):
                cell_texts = grid[r][c]
                if cell_texts:
                    row_data.append(" ".join(cell_texts))
                else:
                    row_data.append("")
            result.append(row_data)

        return result

    # ================================================================
    # 规范化
    # ================================================================

    @staticmethod
    def _normalize_table_columns(table_data: List[List[str]]) -> List[List[str]]:
        """规范化表格：所有行补齐到相同列数，剔除首尾全空行。
        
        与 processor._normalize_table_columns 行为一致。
        """
        if not table_data or not isinstance(table_data, list):
            return table_data
        if len(table_data) == 0:
            return table_data

        max_cols = max((len(row) for row in table_data if row), default=0)
        if max_cols == 0:
            return table_data

        def _is_empty_row(row):
            if not row:
                return True
            return all(cell is None or str(cell).strip() == "" for cell in row)

        # 补齐列数
        normalized = []
        for row in table_data:
            if not row:
                row = []
            while len(row) < max_cols:
                row.append("")
            row = row[:max_cols]
            normalized.append(row)

        # 剔除首尾全空行
        start_idx = 0
        while start_idx < len(normalized) and _is_empty_row(normalized[start_idx]):
            start_idx += 1

        end_idx = len(normalized)
        while end_idx > start_idx and _is_empty_row(normalized[end_idx - 1]):
            end_idx -= 1

        return normalized[start_idx:end_idx]

    # ================================================================
    # 置信度评分
    # ================================================================

    @staticmethod
    def _compute_table_confidence(table_data: List[List[str]],
                                   has_border: bool,
                                   page_words: List[dict],
                                   config: Dict[str, Any]) -> float:
        """计算表格提取结果的置信度"""
        if not table_data or len(table_data) < 2:
            return 0.0

        scores = []

        # 因子1: 列数一致性
        col_counts = [len(row) for row in table_data if row]
        if col_counts and len(col_counts) >= 2:
            mean_cols = statistics.mean(col_counts)
            cv = statistics.stdev(col_counts) / mean_cols if mean_cols > 0 else 1.0
            col_consistency = max(0.0, 1.0 - cv * 2)
            scores.append((col_consistency, config["confidence_col_weight"]))
        else:
            scores.append((0.5, config["confidence_col_weight"]))

        # 因子2: 空值率
        total_cells = sum(len(row) for row in table_data)
        empty_cells = sum(1 for row in table_data for cell in row if not str(cell).strip())
        empty_ratio = empty_cells / max(total_cells, 1)
        if empty_ratio < 0.05:
            empty_score = 0.7
        elif empty_ratio > 0.5:
            empty_score = 0.3
        else:
            empty_score = 1.0 - empty_ratio
        scores.append((empty_score, config["confidence_empty_weight"]))

        # 因子3: 数值占比
        def _is_numeric(text):
            text = str(text).strip().replace(",", "").replace("(", "-").replace(")", "")
            if not text:
                return False
            try:
                float(text)
                return True
            except Exception:
                if text.endswith("%"):
                    try:
                        float(text[:-1])
                        return True
                    except Exception:
                        return False
                return False

        numeric_count = sum(1 for row in table_data for cell in row
                            if _is_numeric(str(cell).strip()))
        numeric_ratio = numeric_count / max(total_cells, 1)
        numeric_score = min(numeric_ratio * 2, 1.0) if numeric_ratio < 0.5 else 1.0
        scores.append((numeric_score, config["confidence_num_weight"]))

        # 加权综合
        weighted_sum = sum(s * w for s, w in scores)
        weighted_total = sum(w for _, w in scores)
        confidence = weighted_sum / weighted_total
        if has_border:
            confidence += config["confidence_line_bonus"]

        return min(1.0, max(0.0, confidence))
