# -*- coding: utf-8 -*-
"""
处理模块 - PDF处理、LLM识别、Excel导出、工作线程
"""

import os
import time
import traceback
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

from .utils import (
    load_config, TEMP_DIR
)
from .pdf_context import PDFContext

# 提前导入 openpyxl，避免运行时首次导入时 GC 触发 C 扩展 refcount bug
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter


# ============================================================
# PDF处理器
# ============================================================
class PDFProcessor:
    """PDF处理器"""

    # === v2 表格提取算法参数配置 ===
    V2_CONFIG = {
        # 行分组
        "y_threshold_factor": 0.4,       # 动态阈值：中位gap × 因子
        "y_threshold_min": 2.0,          # 最小值
        "y_threshold_max": 15.0,          # 最大值

        # 列检测
        "align_tolerance": 4.0,          # 对齐聚簇容差(pt)
        "gap_factor": 0.3,               # gap阈值：中位gap + stdev × 因子
        "gap_min": 10.0,                 # gap最小值

        # 表格区域
        "table_min_width_ratio": 0.3,    # 表格最小宽度/页宽
        "table_min_height": 20.0,        # 表格最小高度
        "density_grid": 10,              # 文本密度网格数
        "density_threshold": 0.8,        # 密度阈值(×平均值倍数)

        # 单元格分配
        "row_margin_factor": 0.2,        # 行分配允许越界比例

        # 置信度
        "confidence_col_weight": 0.35,   # 列数一致性权重
        "confidence_empty_weight": 0.25, # 空值率权重
        "confidence_num_weight": 0.25,   # 数值占比权重
        "confidence_line_bonus": 0.15,   # 表格线加分

        # 过滤（严格模式：V2宁缺毋滥，漏掉的表格由docx通道补充）
        "financial_keywords": [
            "万元", "元", "百万", "十亿", "%", "比率",
            "资产", "负债", "收入", "利润", "现金", "股东",
            "资本", "充足率", "率", "额", "数"
        ],
        "min_text_length": 50,           # 最小文本长度

        # pdfplumber降级
        "pdfplumber_min_words": 20,      # 单页最低word数
        "pdfplumber_min_row_words": 3,   # 每行最低word数
    }

    def __init__(self):
        self.config = load_config()

    def is_image_pdf(self, pdf_path=None, context=None):
        """检测是否为图片型PDF（扫描件）
        使用 get_text('dict') 检测实际文本块，比字符数判断更可靠；
        采样前5页+中部若干页，避免封面/签章页导致全局误判
        
        Args:
            pdf_path: PDF 文件路径（向后兼容，context 为 None 时使用）
            context: PDFContext 共享上下文（优先使用）
        """
        import fitz  # PyMuPDF

        if context:
            doc = context.doc
            close_doc = False
        else:
            doc = fitz.open(pdf_path)
            close_doc = True

        total = len(doc)
        # 采样页：前5页 + 中部区域（避免仅靠封面判断）
        sample_pages = list(range(min(5, total)))
        if total > 10:
            mid = total // 2
            for p in range(mid - 2, min(mid + 3, total)):
                if p not in sample_pages:
                    sample_pages.append(p)

        image_pages = 0
        text_pages = 0
        details = []

        for page_num in sample_pages:
            page = doc[page_num]
            text_dict = page.get_text("dict")
            blocks = text_dict.get("blocks", [])

            # 统计有实际文本内容的 span 数量（比字符数更可靠）
            text_spans = 0
            total_chars = 0
            for block in blocks:
                if block.get("type") == 0:  # 文本块
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            t = span.get("text", "").strip()
                            if t:
                                total_chars += len(t)
                                text_spans += 1

            images = page.get_images()

            # 真正扫描件：没有任何文本 span 但有图片
            # 有文本 span(>=1)就认为是文本页，不再用字符数阈值（CJK 文本容易误判）
            if text_spans == 0 and len(images) > 0:
                image_pages += 1
                details.append(f"p{page_num+1}=图片")
            elif text_spans > 0:
                text_pages += 1
                details.append(f"p{page_num+1}=文本({text_spans}span/{total_chars}字)")
            else:
                details.append(f"p{page_num+1}=空白")

        if close_doc:
            doc.close()

        result = image_pages > text_pages
        print(f"  [PDF检测] 采样{len(sample_pages)}页: {', '.join(details)}")
        print(f"  [PDF检测] 文本页={text_pages}, 图片页={image_pages} → {'图片型PDF' if result else '文本型PDF'}")
        return result

    def extract_text_tables(self, pdf_path=None, max_pages=None, context=None, progress_callback=None, progress_base=20, skip_drawings=False):
        """提取文本型PDF中的表格，保留位置信息
        
        Args:
            pdf_path: PDF 文件路径（向后兼容）
            max_pages: 最大处理页数
            context:  PDFContext 共享上下文（优先使用）
            progress_callback: callback(value, message) 逐页进度
            progress_base: 进度条起始值（默认20）
            skip_drawings: 跳过 drawings（避免 PyMuPDF 崩溃）
        """
        import fitz

        version = self.config.get("extraction_version", "v2")
        if version == "v2":
            return self._extract_text_tables_v2(pdf_path, max_pages, context, progress_callback, progress_base, skip_drawings)

        # ========== v1 逻辑（原有代码，完全不动）==========
        import re
        import pdfplumber

        if context:
            doc = context.doc
            close_doc = False
        else:
            doc = fitz.open(pdf_path)
            close_doc = True

        total_pages = len(doc)

        if max_pages:
            total_pages = min(max_pages, total_pages)

        results = []

        for page_num in range(total_pages):
            page = doc[page_num]
            page_rect = page.rect

            if progress_callback:
                pct = progress_base + int((page_num + 1) / total_pages * 10)
                progress_callback(pct, f"V1提取表格: 第{page_num + 1}/{total_pages}页...")

            # 方法1: 使用PyMuPDF直接获取页面的完整文本和位置信息（确保不丢失边缘数据）
            try:
                text_dict = page.get_text("dict")
                blocks = text_dict.get("blocks", [])

                page_x0 = page_rect.x0
                page_x1 = page_rect.x1
                page_y0 = page_rect.y0
                page_y1 = page_rect.y1

                words = []
                for block in blocks:
                    if block.get("type") == 0:  # 文本块
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                text = span.get("text", "").strip()
                                if text:
                                    bbox = span.get("bbox", [0, 0, 0, 0])
                                    words.append({
                                        "text": text,
                                        "x0": bbox[0],
                                        "y0": bbox[1],
                                        "x1": bbox[2],
                                        "y1": bbox[3],
                                    })

                if words:
                    full_text = " ".join([w["text"] for w in words])
                    financial_keywords = ["万元", "元", "百万", "十亿", "%", "比率", "资产", "负债", "收入", "利润",
                                         "现金", "股东", "资本", "充足率", "率", "额", "数"]
                    has_financial = any(kw in full_text for kw in financial_keywords)

                    if has_financial and len(full_text) > 50:
                        table_data = self._reconstruct_table_from_blocks_improved(words, page_rect)
                        if table_data and len(table_data) > 1:
                            table_data = self._normalize_table_columns(table_data)
                            results.append({
                                "page": page_num + 1,
                                "type": "table",
                                "data": table_data,
                                "text": full_text,
                                "extractor": "pymupdf_position"
                            })
            except Exception as e:
                print(f"  PyMuPDF位置提取第{page_num + 1}页失败: {e}")

            # 方法2: 使用pdfplumber的表格检测获取行边界
            pdfplumber_page = None
            try:
                with pdfplumber.open(pdf_path) as pdf:
                    if page_num < len(pdf.pages):
                        pdfplumber_page = pdf.pages[page_num]
                        # 新版本pdfplumber直接调用find_tables()，不需要settings参数
                        found_tables = pdfplumber_page.find_tables()
                        all_words = pdfplumber_page.extract_words()

                        if all_words and found_tables:
                            full_text = " ".join([w.get("text", "") for w in all_words])
                            financial_keywords = ["万元", "元", "百万", "十亿", "%", "比率", "资产", "负债", "收入",
                                                 "利润", "现金", "股东", "资本", "充足率", "率", "额", "数"]
                            has_financial = any(kw in full_text for kw in financial_keywords)

                            if has_financial and len(full_text) > 50:
                                table_data = self._reconstruct_table_with_pdfplumber_rows(
                                    pdfplumber_page, found_tables, all_words
                                )
                                if table_data and len(table_data) > 1:
                                    table_data = self._normalize_table_columns(table_data)
                                    results.append({
                                        "page": page_num + 1,
                                        "type": "table",
                                        "data": table_data,
                                        "text": full_text,
                                        "extractor": "pdfplumber_hybrid"
                                    })
            except Exception as e:
                print(f"  pdfplumber提取第{page_num + 1}页失败: {e}")

            # 方法3: 使用PyMuPDF BLOCK模式提取带位置的文本
            if not any(r.get("page") == page_num + 1 and r.get("type") == "table" for r in results):
                text_dict = page.get_text("dict")
                blocks = text_dict.get("blocks", [])

                text_blocks = []
                for block in blocks:
                    if block.get("type") == 0:
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                text = span.get("text", "").strip()
                                if text:
                                    text_blocks.append({
                                        "text": text,
                                        "x0": span.get("bbox", [0, 0, 0, 0])[0],
                                        "y0": span.get("bbox", [0, 0, 0, 0])[1],
                                        "x1": span.get("bbox", [0, 0, 0, 0])[2],
                                        "y1": span.get("bbox", [0, 0, 0, 0])[3],
                                    })

                if text_blocks:
                    full_text = " ".join([b["text"] for b in text_blocks])
                    financial_keywords = ["万元", "元", "百万", "十亿", "%", "比率", "资产", "负债", "收入", "利润",
                                         "现金", "股东", "资本", "充足率"]
                    has_financial = any(kw in full_text for kw in financial_keywords)

                    if has_financial and len(full_text) > 50:
                        table_data = self._reconstruct_table_from_blocks_improved(text_blocks, page_rect)
                        if table_data and len(table_data) > 1:
                            table_data = self._normalize_table_columns(table_data)
                            results.append({
                                "page": page_num + 1,
                                "type": "table",
                                "data": table_data,
                                "text": full_text,
                                "extractor": "position_based"
                            })
                        else:
                            table_data = self._reconstruct_table_from_blocks(text_blocks, page_rect.width)
                            if table_data and len(table_data) > 1:
                                table_data = self._normalize_table_columns(table_data)
                                results.append({
                                    "page": page_num + 1,
                                    "type": "table",
                                    "data": table_data,
                                    "text": full_text,
                                    "extractor": "position_based_fallback"
                                })

        if close_doc:
            doc.close()

        # V1 不再合并同一页的多个表格，每个表格独立保留
        # results = self._merge_tables_on_same_page(results)
        return results

    def _merge_tables_on_same_page(self, results):
        """合并同一页的多个表格"""
        if not results:
            return results

        page_groups = {}
        for table in results:
            page = table.get("page", 0)
            if page not in page_groups:
                page_groups[page] = []
            page_groups[page].append(table)

        merged_results = []
        for page in sorted(page_groups.keys()):
            tables = page_groups[page]

            if len(tables) == 1:
                merged_results.append(tables[0])
            else:
                merged_data = []
                merged_extractors = []

                # 先计算所有表格的最大列数
                all_max_cols = 0
                for table in tables:
                    data = table.get("data", [])
                    if data:
                        all_max_cols = max(all_max_cols, max(len(row) for row in data))

                # 再合并表格
                for i, table in enumerate(tables):
                    data = table.get("data", [])
                    if not data:
                        continue

                    if merged_data:
                        separator_row = ["--- 表格" + str(i) + " ---"] + [""] * (all_max_cols - 1)
                        merged_data.append(separator_row)

                    for row in data:
                        padded_row = list(row) + [None] * (all_max_cols - len(row))
                        merged_data.append(padded_row)

                    merged_extractors.append(table.get("extractor", "unknown"))

                merged_results.append({
                    "page": page,
                    "type": "table",
                    "data": merged_data,
                    "text": "",
                    "extractor": "+".join(merged_extractors)
                })

        return merged_results

    def _normalize_table_columns(self, table_data):
        """规范化表格"""
        if not table_data or not isinstance(table_data, list):
            return table_data

        if len(table_data) == 0:
            return table_data

        max_cols = max((len(row) for row in table_data if row), default=0)

        if max_cols == 0:
            return table_data

        def is_empty_row(row):
            if not row:
                return True
            return all(cell is None or str(cell).strip() == "" for cell in row)

        normalized = []
        for row in table_data:
            if not row:
                row = []
            while len(row) < max_cols:
                row.append(None)
            row = row[:max_cols]
            normalized.append(row)

        start_idx = 0
        while start_idx < len(normalized) and is_empty_row(normalized[start_idx]):
            start_idx += 1

        end_idx = len(normalized)
        while end_idx > start_idx and is_empty_row(normalized[end_idx - 1]):
            end_idx -= 1

        return normalized[start_idx:end_idx]

    def _reconstruct_table_from_blocks(self, text_blocks, page_width):
        """根据文本块位置信息重建表格结构"""
        if not text_blocks:
            return None

        rows = []
        current_row = []
        current_y = None
        y_threshold = 5

        sorted_blocks = sorted(text_blocks, key=lambda b: (round(b["y0"] / y_threshold), b["x0"]))

        for block in sorted_blocks:
            y = round(block["y0"] / y_threshold)
            if current_y is None or abs(y - current_y) <= 1:
                current_row.append(block)
                current_y = y
            else:
                if current_row:
                    rows.append(current_row)
                current_row = [block]
                current_y = y

        if current_row:
            rows.append(current_row)

        table_data = []
        for row in rows:
            sorted_row = sorted(row, key=lambda b: b["x0"])
            row_data = [span["text"] for span in sorted_row]
            row_text = "".join(row_data)
            if len(row_text.strip()) > 0:
                table_data.append(row_data)

        return table_data

    def _reconstruct_table_from_blocks_improved(self, text_blocks, page_rect):
        """改进的表格重建方法"""
        if not text_blocks:
            return None

        if hasattr(page_rect, 'width'):
            page_width = page_rect.width
            page_x0 = page_rect.x0 if hasattr(page_rect, 'x0') else 0
        else:
            page_width = page_rect[2] if len(page_rect) > 2 else page_rect[0]
            page_x0 = page_rect[0] if len(page_rect) > 0 else 0

        all_x0 = [b["x0"] for b in text_blocks]
        all_x1 = [b["x1"] for b in text_blocks]

        if not all_x0:
            return None

        min_x = min(all_x0)
        max_x = max(all_x1)

        x_points = sorted(set(all_x0 + all_x1))
        if len(x_points) < 2:
            return None

        # 计算自适应列边界阈值（基于x坐标分布）
        gaps = []
        for i in range(len(x_points) - 1):
            gap = x_points[i + 1] - x_points[i]
            gaps.append((x_points[i], x_points[i + 1], gap))

        # 自适应阈值：取gap的中位数*1.5，更能适应不同PDF
        if gaps:
            all_gaps = [g[2] for g in gaps if g[2] > 0]
            if all_gaps:
                import statistics
                median_gap = statistics.median(all_gaps)
                gap_threshold = max(median_gap * 1.5, 10)  # 最小10pt
            else:
                gap_threshold = 15
        else:
            gap_threshold = 15

        column_boundaries = []

        for x_start, x_end, gap in gaps:
            if gap > gap_threshold:
                column_boundaries.append((x_start + x_end) / 2)

        if not column_boundaries:
            column_boundaries = [min_x, max_x]
        else:
            column_boundaries = sorted(set(column_boundaries))
            # 确保左右边界包含所有内容
            if column_boundaries[0] > min_x:
                column_boundaries.insert(0, (min_x + column_boundaries[0]) / 2)
            if column_boundaries[-1] < max_x:
                column_boundaries.append((column_boundaries[-1] + max_x) / 2)

        y_threshold = 5
        sorted_blocks = sorted(text_blocks, key=lambda b: b["y0"])

        rows = []
        current_row = []
        current_y = None

        for block in sorted_blocks:
            y = round(block["y0"] / y_threshold)
            if current_y is None or abs(y - current_y) <= 1:
                current_row.append(block)
                current_y = y
            else:
                if current_row:
                    rows.append(current_row)
                current_row = [block]
                current_y = y

        if current_row:
            rows.append(current_row)

        table_data = []
        for row_blocks in rows:
            sorted_row = sorted(row_blocks, key=lambda b: b["x0"])
            row_data = [""] * (len(column_boundaries) - 1)

            for block in sorted_row:
                col_idx = self._find_column_index(block["x0"], block["x1"], column_boundaries)
                if 0 <= col_idx < len(row_data):
                    if row_data[col_idx]:
                        row_data[col_idx] += " " + block["text"]
                    else:
                        row_data[col_idx] = block["text"]

            if any(cell.strip() for cell in row_data):
                table_data.append(row_data)

        return table_data if table_data else None

    def _reconstruct_table_with_pdfplumber_rows(self, pdfplumber_page, pdfplumber_tables, words):
        """使用pdfplumber的表格检测获取精确的行列边界"""
        if not pdfplumber_tables or not words:
            return None

        table_data = []

        for table in pdfplumber_tables:
            table_bbox = table.bbox
            if not table_bbox:
                continue

            table_top = table_bbox[1]
            table_bottom = table_bbox[3]
            table_left = table_bbox[0]
            table_right = table_bbox[2]

            table_rows = table.rows
            if not table_rows:
                continue

            # 获取列数：Row对象是可迭代的，但不支持len()，转换为列表
            first_row = list(table_rows[0]) if table_rows else []
            num_cols = len(first_row)

            for row_cells in table_rows:
                row_data = []

                for cell in row_cells:
                    cell_bbox = cell.bbox
                    if not cell_bbox:
                        row_data.append("")
                        continue

                    cell_left = cell_bbox[0]
                    cell_right = cell_bbox[2]
                    cell_top = cell_bbox[1]
                    cell_bottom = cell_bbox[3]

                    cell_texts = []
                    for w in words:
                        word_x0 = w.get("x0", 0)
                        word_x1 = w.get("x1", 0)
                        word_top = w.get("top", 0)
                        word_bottom = w.get("bottom", 0)
                        word_mid_y = (word_top + word_bottom) / 2

                        if cell_top <= word_mid_y <= cell_bottom:
                            if word_x0 < cell_right and word_x1 > cell_left:
                                cell_texts.append(w)

                    if cell_texts:
                        cell_texts.sort(key=lambda w: w.get("x0", 0))
                        cell_text = " ".join([w.get("text", "") for w in cell_texts])
                    else:
                        cell_text = ""

                    row_data.append(cell_text)

                if any(cell.strip() for cell in row_data):
                    table_data.append(row_data)

        return table_data if table_data else None

    def _detect_column_boundaries_by_spacing(self, text_blocks, page_width):
        """根据文本间距检测列边界"""
        if not text_blocks:
            return [0, page_width]

        x_coords = [b["x0"] for b in text_blocks] + [b["x1"] for b in text_blocks]

        bucket_size = 20
        max_x = max(x_coords) if x_coords else page_width
        buckets = {}

        for x in x_coords:
            bucket = int(x / bucket_size)
            buckets[bucket] = buckets.get(bucket, 0) + 1

        if len(buckets) < 2:
            return [0, page_width]

        avg_density = sum(buckets.values()) / len(buckets)

        gaps = []
        sorted_buckets = sorted(buckets.keys())

        for i in range(len(sorted_buckets) - 1):
            bucket1, bucket2 = sorted_buckets[i], sorted_buckets[i + 1]
            mid_buckets = range(bucket1 + 1, bucket2)
            gap_density = sum(buckets.get(b, 0) for b in mid_buckets)

            if gap_density < avg_density * 0.3:
                gaps.append((bucket1 * bucket_size + bucket_size / 2, bucket2 * bucket_size))

        boundaries = [0]
        for start, end in sorted(gaps, key=lambda x: x[0]):
            boundaries.append((start + end) / 2)
        boundaries.append(page_width)

        if len(boundaries) < 3:
            boundaries = [0, page_width * 0.3, page_width * 0.6, page_width]

        return sorted(set(boundaries))

    def _find_column_index(self, x0, x1, column_boundaries):
        """找到文本块属于哪一列"""
        center_x = (x0 + x1) / 2

        for i in range(len(column_boundaries) - 1):
            if column_boundaries[i] <= center_x < column_boundaries[i + 1]:
                return i

        if center_x < column_boundaries[0]:
            return 0
        elif center_x >= column_boundaries[-1]:
            return len(column_boundaries) - 2
        else:
            min_dist = float('inf')
            closest_col = 0
            for i in range(len(column_boundaries) - 1):
                mid = (column_boundaries[i] + column_boundaries[i + 1]) / 2
                dist = abs(center_x - mid)
                if dist < min_dist:
                    min_dist = dist
                    closest_col = i
            return closest_col

    def _parse_text_to_table(self, text):
        """将文本解析为表格格式"""
        import re

        lines = text.split('\n')
        table_data = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            parts = re.split(r'\s{2,}|\t', line)
            parts = [p.strip() for p in parts if p.strip()]

            if parts and (any(c.isdigit() for p in parts for c in p) or
                         any(kw in line for kw in ["资产", "负债", "收入", "利润", "合计", "小计"])):
                table_data.append(parts)

        return table_data if table_data else [[text]]

    def pdf_to_images(self, pdf_path=None, output_dir=None, context=None):
        """将PDF转换为图片
        
        Args:
            pdf_path: PDF 文件路径（向后兼容）
            output_dir:  输出目录
            context:     PDFContext 共享上下文（优先使用）
        """
        import fitz

        if output_dir is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = Path(TEMP_DIR) / f"pdf_images_{timestamp}"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if context:
            return context.generate_all_llm_images(output_dir)

        # 向后兼容：无 context 时自己打开 PDF
        doc = fitz.open(pdf_path)
        image_paths = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)

            output_path = output_dir / f"page_{page_num + 1}.png"
            pix.save(str(output_path))
            image_paths.append(str(output_path))

        doc.close()
        return image_paths




# ============================================================
# v2 表格提取算法
# ============================================================

    # ---- v2 入口 ----

    def _extract_text_tables_v2(self, pdf_path=None, max_pages=None, context=None, progress_callback=None, progress_base=20, skip_drawings=False):
        """v2 表格提取入口
        
        Args:
            pdf_path: PDF 文件路径（向后兼容）
            max_pages: 最大处理页数
            context:  PDFContext 共享上下文（优先使用）
            progress_callback:  callback(value, message) 用于推送逐页进度
            progress_base: 进度条起始值（默认20，auto模式下为40）
            skip_drawings:  跳过 get_drawings()（auto模式为True，避免PyMuPDF崩溃）
        """
        import fitz
        import statistics

        if context:
            doc = context.doc
            close_doc = False
        else:
            doc = fitz.open(pdf_path)
            close_doc = True

        total_pages = len(doc)
        if max_pages:
            total_pages = min(max_pages, total_pages)

        results = []
        cfg = self.V2_CONFIG

        for page_num in range(total_pages):
            page = doc[page_num]
            page_rect = page.rect

            # 逐页进度回调
            if progress_callback:
                pct = progress_base + int((page_num + 1) / total_pages * 10)
                progress_callback(pct, f"V2扫描: 第{page_num + 1}/{total_pages}页")

            # 1. 提取 words + drawings
            words_raw = page.get_text("words")
            words = []
            for w in words_raw:
                words.append({
                    "x0": w[0], "y0": w[1],
                    "x1": w[2], "y1": w[3],
                    "text": w[4],
                    "baseline": w[3],
                })

            # PyMuPDF get_drawings() 存在 C 扩展 refcount bug(pdf2docx 后崩溃)
            # auto 模式下完全跳过 drawings，仅用纯文本密度检测表格区域
            drawings = []
            if not skip_drawings:
                try:
                    drawings_raw = page.get_drawings()
                    for d in drawings_raw:
                        rect = d["rect"]
                        w = rect.width
                        h = rect.height
                        direction = None
                        if w > h * 5:
                            direction = "h"
                        elif h > w * 5:
                            direction = "v"
                        drawings.append({
                            "type": "line" if (w < h * 0.3 or h < w * 0.3) else "rect",
                            "direction": direction,
                            "x0": rect.x0, "y0": rect.y0,
                            "x1": rect.x1, "y1": rect.y1,
                            "color": d.get("color"),
                            "width": d.get("width", 1),
                            "fill": d.get("fill"),
                        })
                except Exception:
                    print(f"  [V2] 第{page_num+1}页: get_drawings() 失败，使用纯文本检测")

            # 回退：如果 get_text("words") 返回空，尝试 get_text("dict")
            if not words:
                print(f"  [V2] 第{page_num+1}页: get_text('words')返回空，尝试dict回退...")
                words = PDFProcessor._extract_words_from_dict(page)
                if words:
                    print(f"  [V2] 第{page_num+1}页: dict回退成功，提取到{len(words)}个文本片段")
                else:
                    print(f"  [V2] 第{page_num+1}页: dict回退也失败，跳过该页")
                    continue

            # 2. 金融关键词过滤
            full_text = " ".join(w["text"] for w in words)
            if not any(kw in full_text for kw in cfg["financial_keywords"]):
                print(f"  [V2] 第{page_num+1}页: 未匹配金融关键词，跳过 (文本长度={len(full_text)}, 预览={full_text[:60]!r})")
                continue
            if len(full_text) < cfg["min_text_length"]:
                print(f"  [V2] 第{page_num+1}页: 文本长度{len(full_text)}不满足最低{cfg['min_text_length']}要求，跳过")
                continue

            # 3. 表格区域定位
            table_regions = self._detect_table_region(drawings, page_rect.width, page_rect.height)
            if not table_regions:
                table_regions = self._detect_table_region_by_text(words, page_rect.width, page_rect.height)
            if not table_regions:
                print(f"  [V2] 第{page_num+1}页: 未检测到表格区域，跳过")
                continue

            # 4. 为每个表格区域提取数据
            for region in table_regions:
                rx0, ry0, rx1, ry1 = region
                region_words = [w for w in words
                                if rx0 <= w["x0"] <= rx1 and ry0 <= w["y0"] <= ry1]

                if len(region_words) < 3:
                    continue

                # 提取表格区域上方的上下文文本（表格上方 100pt 范围内的文本）
                context_text = PDFProcessor._extract_context_text_from_words(
                    words, rx0, ry0, rx1, ry1, margin=100.0
                )

                # 行边界
                row_bounds = self._detect_horizontal_lines(page, region_words, drawings)
                if len(row_bounds) < 2:
                    continue

                # 列边界
                col_bounds = self._detect_vertical_lines(page, region_words, drawings)
                if len(col_bounds) < 3:
                    continue

                # 网格填充
                table_data = self._assign_words_to_grid(region_words, row_bounds, col_bounds)
                if not table_data or len(table_data) < 2:
                    continue

                # 规范化
                table_data = self._normalize_table_columns(table_data)

                # 置信度
                has_border = bool([d for d in drawings if d["direction"] in ("h", "v")])
                confidence = self._compute_table_confidence(table_data, has_border, words)

                results.append({
                    "page": page_num + 1,
                    "type": "table",
                    "data": table_data,
                    "text": full_text,
                    "extractor": "v2_position_based",
                    "confidence": confidence,
                    "rows": len(table_data),
                    "cols": len(col_bounds) - 1,
                    "has_border": has_border,
                    "context_text": context_text,
                })

        if close_doc:
            doc.close()
        # V2 不再合并同一页的多个表格，每个表格区域独立保留
        # results = self._merge_tables_on_same_page(results)
        return results

    @staticmethod
    def _extract_context_text_from_words(words, rx0, ry0, rx1, ry1, margin=100.0):
        """从 PDF words 中提取表格区域上方的上下文文本。

        Args:
            words: PDF 页面的所有 words 列表 (每个 word 含 x0, y0, x1, y1, text)
            rx0, ry0, rx1, ry1: 表格区域边界
            margin: 表格上方搜索范围（pt），默认 100pt（约 2-3 行文本）

        Returns:
            str: 拼接后的上下文文本，按 y 坐标从上到下排列
        """
        # 筛选表格上方 margin 范围内且 x 方向与表格有重叠的 words
        context_top = max(0, ry0 - margin)
        context_words = [
            w for w in words
            if context_top <= w["y0"] < ry0
            and w["x1"] > rx0 * 0.8  # 允许 x 方向有 20% 外扩容差
            and w["x0"] < rx1 * 1.2
            and w["text"].strip()
        ]

        if not context_words:
            return ""

        # 按 y 坐标升序分组为行
        context_words.sort(key=lambda w: (w["y0"], w["x0"]))
        lines = []
        current_line = []
        current_y = None
        y_tolerance = 5.0  # 同行的 y 容差

        for w in context_words:
            if current_y is None or abs(w["y0"] - current_y) <= y_tolerance:
                current_line.append(w["text"])
                if current_y is None:
                    current_y = w["y0"]
            else:
                lines.append(' '.join(current_line))
                current_line = [w["text"]]
                current_y = w["y0"]

        if current_line:
            lines.append(' '.join(current_line))

        return '\n'.join(lines).strip()

    @staticmethod
    def _extract_words_from_dict(page):
        """当 get_text('words') 返回空时的回退方案
        从 get_text('dict') 的 blocks/lines/spans 中提取文本和坐标
        """
        text_dict = page.get_text("dict")
        blocks = text_dict.get("blocks", [])
        words = []
        for block in blocks:
            if block.get("type") == 0:  # 文本块
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if text:
                            bbox = span.get("bbox", [0, 0, 0, 0])
                            words.append({
                                "x0": bbox[0],
                                "y0": bbox[1],
                                "x1": bbox[2],
                                "y1": bbox[3],
                                "text": text,
                            "baseline": bbox[3],
                        })
        return words

    # ---- docx 表格提取（pdf2docx 通道） ----

    def _extract_tables_via_docx(self, pdf_path=None, context=None, progress_callback=None):
        """通过 pdf2docx 将 PDF 转为 Word，从 Word 表格结构中提取数据。

        全内存操作（BytesIO），不落盘。
        输出格式与 V1/V2 统一。

        Args:
            pdf_path: PDF 文件路径（向后兼容）
            context:  PDFContext 共享上下文（优先使用）
            progress_callback: callback(value, message) 推送进度
        Returns:
            [{page, type, data, extractor, confidence, ...}]
        """
        from io import BytesIO

        if context:
            _pdf_path = context.pdf_path
        else:
            _pdf_path = pdf_path

        print(f"  [docx] 开始 pdf2docx 全内存转换...")
        t0 = time.time()
        total_hint = context.page_count if context else "?"
        if progress_callback:
            progress_callback(22, f"docx: PDF转Word中({total_hint}页,约2-5分钟)...")

        # 步骤1：pdf2docx → 内存 BytesIO
        # 注意：cv.convert() 是阻塞调用，内部无进度回调，此阶段进度条会停留约2-5分钟
        try:
            from pdf2docx import Converter
        except ImportError:
            print(f"  [docx] 错误：未安装 pdf2docx 库，请执行 pip install pdf2docx")
            return []

        import tempfile
        import multiprocessing
        cpu_count = min(multiprocessing.cpu_count(), 6)  # 最多6核，避免资源争抢

        # pdf2docx 多进程模式需要落盘（子进程间通过 JSON 文件交换数据）
        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
            tmp_path = tmp.name

        cv = Converter(_pdf_path)
        cv.convert(
            tmp_path,
            start=0,
            end=None,
            layout=False,           # 流式模式：表格识别更准
            table_deduction=False,  # 保守策略
            multi_processing=True,
            cpu_count=cpu_count,
        )
        cv.close()

        buf = BytesIO()
        with open(tmp_path, 'rb') as f:
            buf.write(f.read())
        os.unlink(tmp_path)
        buf.seek(0)

        elapsed = time.time() - t0
        print(f"  [docx] pdf2docx 转换完成，耗时 {elapsed:.1f}s")
        if progress_callback:
            progress_callback(30, f"docx: 转换完成({elapsed:.0f}s),解析表格...")

        # 步骤2：python-docx 解析
        try:
            from docx import Document
        except ImportError:
            print(f"  [docx] 错误：未安装 python-docx 库，请执行 pip install python-docx")
            return []

        doc = Document(buf)
        buf.close()

        # 步骤3：遍历文档 body 子元素，通过分页符推算每个表格的真实页码
        # 同时收集每个表格上方的段落文本作为上下文
        W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        body = doc.element.body

        # 先扫描所有 body 子元素
        # page_table_map:  {表格XML元素: PDF页码}
        # table_context_map: {表格XML元素: 表格上方段落文本}
        current_page = 1
        page_table_map = {}
        table_context_map = {}
        pending_paragraphs = []  # 收集表格前的段落文本

        def _extract_paragraph_text(child_elem):
            """从 w:p 元素中提取纯文本"""
            texts = []
            for t_elem in child_elem.iter(f'{W}t'):
                if t_elem.text:
                    texts.append(t_elem.text)
            return ''.join(texts).strip()

        for child in body:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag

            if tag == 'p':
                para_text = _extract_paragraph_text(child)
                # 检查段落中是否包含分页符
                has_page_break = False

                # 方式1：w:br type='page'（显式分页符）
                for br in child.iter(f'{W}br'):
                    br_type = br.get(f'{W}type')
                    if br_type == 'page':
                        current_page += 1
                        has_page_break = True
                        break

                # 方式2：w:lastRenderedPageBreak（Word 渲染分页标记）
                if not has_page_break:
                    for lrpb in child.iter(f'{W}lastRenderedPageBreak'):
                        current_page += 1
                        has_page_break = True
                        break

                # 方式3：段落属性中的分页设置（w:pageBreakBefore）
                if not has_page_break:
                    pPr = child.find(f'{W}pPr')
                    if pPr is not None:
                        # 段前分页
                        pbf = pPr.find(f'{W}pageBreakBefore')
                        if pbf is not None:
                            val = pbf.get(f'{W}val')
                            if val not in ('0', 'false'):
                                current_page += 1
                                has_page_break = True

                # 方式4：sectPr 内嵌在段落中（节分隔符通常也意味着换页）
                if not has_page_break:
                    pPr = child.find(f'{W}pPr')
                    if pPr is not None:
                        sectPr = pPr.find(f'{W}sectPr')
                        if sectPr is not None:
                            sect_type = sectPr.find(f'{W}type')
                            # 仅在 type='nextPage' 或无 type（默认 nextPage）时递增
                            if sect_type is None or sect_type.get(f'{W}val') == 'nextPage':
                                current_page += 1
                                has_page_break = True

                # 方式5：检测页眉页脚中的纯数字页码（如 13、14、15）
                if context and para_text and not has_page_break:
                    stripped = para_text.strip()
                    if stripped.isdigit():
                        pn = int(stripped)
                        total = context.page_count
                        if 1 <= pn <= total and pn > current_page:
                            current_page = pn
                            has_page_break = True

                if has_page_break:
                    pending_paragraphs.clear()

                if para_text:
                    pending_paragraphs.append(para_text)
                # 限制缓存的段落数量，避免积累过多
                if len(pending_paragraphs) > 5:
                    pending_paragraphs = pending_paragraphs[-5:]

            elif tag == 'tbl':
                page_table_map[child] = current_page
                # 取表格前最近 1-3 段作为上下文文本
                ctx_text = '\n'.join(pending_paragraphs[-3:]) if pending_paragraphs else ""
                table_context_map[child] = ctx_text
                pending_paragraphs.clear()  # 表格之后的段落属于下一个表格

            elif tag == 'sectPr':
                # body 级别的 sectPr（文档最后的节属性），也可能意味着换页
                # 但文档末尾的 sectPr 不需要处理，跳过
                pass

        if not page_table_map:
            print(f"  [docx] Word 中未检测到任何表格")
            return []

        # 通过 python-docx 的 Table 对象处理表格数据
        tables = doc.tables
        results = []
        for tbl_idx, table in enumerate(tables):
            try:
                tbl_elem = table._tbl
                page_num = page_table_map.get(tbl_elem, tbl_idx + 1)

                # 提取表格级列宽
                tblGrid = tbl_elem.find(f'{W}tblGrid')
                col_widths = []
                if tblGrid is not None:
                    for gridCol in tblGrid.findall(f'{W}gridCol'):
                        w = float(gridCol.get(f'{W}w', 0))
                        col_widths.append(w)

                # 逐行解析
                rows_data = []
                merge_tracker = {}  # {(row, col): True} 被垂直合并占用的单元格

                for r, tr in enumerate(table.rows):
                    row_cells = []
                    col_idx = 0

                    for cell in tr.cells:
                        # 跳过被垂直合并占用的位置
                        while merge_tracker.get((r, col_idx)):
                            row_cells.append("")
                            col_idx += 1

                        tc = cell._tc
                        tcPr = tc.find(f'{W}tcPr')
                        col_span = 1
                        row_start = True

                        if tcPr is not None:
                            # gridSpan：跨列
                            gridSpan = tcPr.find(f'{W}gridSpan')
                            if gridSpan is not None:
                                col_span = int(gridSpan.get(f'{W}val', 1))

                            # vMerge：垂直合并
                            vMerge = tcPr.find(f'{W}vMerge')
                            if vMerge is not None:
                                val = vMerge.get(f'{W}val')
                                if val != 'restart':
                                    # 被合并的后续行
                                    row_start = False

                        if row_start:
                            text = cell.text.strip()
                            for span in range(col_span):
                                if span == 0:
                                    row_cells.append(text)
                                else:
                                    row_cells.append("")

                            if vMerge is not None:
                                # 标记下方被合并的行
                                for rr in range(r + 1, len(table.rows)):
                                    merge_tracker[(rr, col_idx)] = True
                                    for s in range(1, col_span):
                                        merge_tracker[(rr, col_idx + s)] = True
                        else:
                            # 垂直合并的延续行：占位
                            for span in range(col_span):
                                row_cells.append("")

                        col_idx += col_span

                    if row_cells:
                        rows_data.append(row_cells)

                if rows_data:
                    context_text = table_context_map.get(tbl_elem, "")
                    results.append({
                        "page": page_num,
                        "type": "table",
                        "data": rows_data,
                        "text": "",
                        "extractor": "docx_based",
                        "confidence": 0.85,
                        "rows": len(rows_data),
                        "cols": max(len(r) for r in rows_data) if rows_data else 0,
                        "has_border": True,
                        "context_text": context_text,
                    })
                    if context_text:
                        ctx_preview = context_text[:50].replace('\n', ' ')
                        print(f"  [docx] 表格{tbl_idx+1}(PDF第{page_num}页): {len(rows_data)}行{results[-1]['cols']}列表格, 上下文: {ctx_preview}...")
                    else:
                        print(f"  [docx] 表格{tbl_idx+1}(PDF第{page_num}页): {len(rows_data)}行{results[-1]['cols']}列表格")

            except Exception as e:
                print(f"  [docx] 表格{tbl_idx+1}解析失败: {e}")
                continue

        # 步骤4：V2-Lite 坐标匹配校验页码
        if context and results:
            if progress_callback:
                progress_callback(33, "docx: V2-Lite 坐标匹配校验页码...")
            # V2-Lite 扫描＋Jaccard匹配＋bbox冲突检测＋排序 全部在内部完成
            results = self._verify_docx_page_numbers(results, context)
            # results 已按页码排序，无需再次排序

        if progress_callback:
            progress_callback(36, f"docx提取完成: {len(results)}个表格")
        print(f"  [docx] 共提取 {len(results)} 个表格")
        return results

    # ---- 逐页 pdf2docx 转换（页码 100% 准确） ----

    @staticmethod
    def _parse_docx_tables_static(docx_buf, page_num):
        """从 DOCX 缓冲区解析表格并标记页码（静态，子进程可 pickle）。"""
        from docx import Document
        W = ('{http://schemas.openxmlformats.org/'
             'wordprocessingml/2006/main}')
        doc = Document(docx_buf)
        tables = []

        for table in doc.tables:
            try:
                rows_data = []
                merge_tracker = {}

                for r, tr in enumerate(table.rows):
                    row_cells = []
                    col_idx = 0

                    for cell in tr.cells:
                        while merge_tracker.get((r, col_idx)):
                            row_cells.append("")
                            col_idx += 1

                        tc = cell._tc
                        tcPr = tc.find(f'{W}tcPr')
                        col_span = 1
                        row_start = True

                        if tcPr is not None:
                            gridSpan = tcPr.find(f'{W}gridSpan')
                            if gridSpan is not None:
                                col_span = int(gridSpan.get(f'{W}val', 1))
                            vMerge = tcPr.find(f'{W}vMerge')
                            if vMerge is not None:
                                if vMerge.get(f'{W}val') != 'restart':
                                    row_start = False

                        if row_start:
                            text = cell.text.strip()
                            for span in range(col_span):
                                row_cells.append(text if span == 0 else "")
                        else:
                            for span in range(col_span):
                                row_cells.append("")

                        col_idx += col_span

                    if row_cells:
                        rows_data.append(row_cells)

                if rows_data:
                    tables.append({
                        "page": page_num,
                        "type": "table",
                        "data": rows_data,
                        "text": "",
                        "extractor": "docx_per_page",
                        "confidence": 0.95,
                        "rows": len(rows_data),
                        "cols": (max(len(r) for r in rows_data)
                                 if rows_data else 0),
                        "has_border": True,
                        "context_text": "",
                    })
            except Exception:
                pass
        return tables

    @staticmethod
    def _convert_batch_static(pdf_path, page_nums):
        """每个进程处理一批页（静态方法，可被 pickle 序列化）。

        在独立子进程中运行，PyMuPDF C 扩展状态完全隔离，
        彻底避免多线程共享导致的 refcount 崩溃。
        """
        import tempfile
        import os
        from io import BytesIO
        from pdf2docx import Converter

        batch_tables = []
        cv = None

        try:
            cv = Converter(pdf_path)

            for page_num in page_nums:
                tmp_path = None
                try:
                    fd, tmp_path = tempfile.mkstemp(suffix='.docx')
                    os.close(fd)

                    cv.convert(
                        tmp_path,
                        start=page_num - 1,
                        end=page_num,
                        layout=False,
                        table_deduction=False,
                        multi_processing=False,
                        cpu_count=1,
                    )

                    with open(tmp_path, 'rb') as f:
                        buf = BytesIO(f.read())
                    batch_tables.extend(
                        PDFProcessor._parse_docx_tables_static(buf, page_num))
                    buf.close()

                except Exception:
                    pass
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass
        finally:
            if cv is not None:
                try:
                    cv.close()
                except Exception:
                    pass

        return batch_tables

    def _extract_tables_via_docx_per_page(self, pdf_path=None, context=None,
                                           progress_callback=None):
        """逐页 pdf2docx 转换：每页独立转 DOCX，表格页码 100% 准确。

        相比一次性全文档转换，逐页方案从根本上消除了"猜页码→DP修正"
        的信息损失链条，表格从哪页 DOCX 解析出来就属于哪页。

        性能优化：
        - 多进程并行转换（每进程独立 Python 解释器，完全隔离 PyMuPDF C 扩展）

        TODO: 跨页表格合并（后续单独实现）

        Args:
            pdf_path: PDF 文件路径（向后兼容）
            context:  PDFContext 共享上下文（优先使用）
            progress_callback: callback(value, message) 推送进度
        Returns:
            [{page, type, data, extractor, confidence, context_text, ...}]
        """
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor, as_completed

        if context:
            _pdf_path = context.pdf_path
        else:
            _pdf_path = pdf_path

        total_pages = context.page_count if context else 0
        if total_pages == 0:
            import fitz
            doc = fitz.open(_pdf_path)
            total_pages = len(doc)
            doc.close()

        cpu_count = min(multiprocessing.cpu_count(), 6)

        # 将页分配到各进程（每个进程处理一批页，复用 Converter）
        pages_per_batch = (total_pages + cpu_count - 1) // cpu_count
        page_batches = []
        for t in range(cpu_count):
            start = t * pages_per_batch + 1
            end = min(start + pages_per_batch - 1, total_pages)
            if start <= end:
                page_batches.append(list(range(start, end + 1)))

        print(f"  [docx-per-page] 逐页转换: {total_pages} 页, "
              f"{cpu_count} 进程并行 × ~{pages_per_batch} 页/进程")
        if progress_callback:
            progress_callback(22,
                f"docx: 逐页转换 {total_pages} 页 ({cpu_count}进程)...")

        t0 = time.time()

        # 多进程并行：每个子进程独立 Python 解释器，PyMuPDF C 扩展完全隔离
        all_tables = []
        total_batches = len(page_batches)
        completed_batches = 0

        with ProcessPoolExecutor(max_workers=cpu_count) as executor:
            futures = {
                executor.submit(
                    PDFProcessor._convert_batch_static, _pdf_path, batch
                ): idx
                for idx, batch in enumerate(page_batches)
            }

            for future in as_completed(futures):
                try:
                    batch_tables = future.result()
                    all_tables.extend(batch_tables)
                except Exception as e:
                    print(f"  [docx-per-page] 进程批处理异常: {e}")

                completed_batches += 1
                if progress_callback:
                    pct = 22 + int(completed_batches / total_batches * 8)
                    progress_callback(pct,
                        f"docx: 批次 {completed_batches}/{total_batches}")

        # 按页码排序
        all_tables.sort(key=lambda t: t.get("page", 0))

        elapsed = time.time() - t0
        print(f"  [docx-per-page] 转换完成: {len(all_tables)} 个表格, "
              f"耗时 {elapsed:.1f}s")

        # 注意：不做上下文补充，避免 PyMuPDF C 扩展 refcount bug
        # 子进程已完全隔离，主进程 all_tables 是纯 Python 数据（list[dict]），
        # 不包含任何 PyMuPDF C 对象，后续 deepcopy 安全，无需 gc.collect()

        # 打印摘要
        pages_with_tables = len(set(t["page"] for t in all_tables))
        print(f"  [docx-per-page] {len(all_tables)} 表 / "
              f"{pages_with_tables} 页 / {total_pages} 页 → "
              f"覆盖率 {pages_with_tables/total_pages*100:.0f}%")

        if progress_callback:
            progress_callback(30, f"docx: 提取完成 ({len(all_tables)}表格)")

        return all_tables

    def _enrich_per_page_context(self, all_tables, context):
        """为逐页转换的表补充上下文文本（从 PDF 页文本提取，仅用于展示）。"""
        try:
            pages_with_tables = set(t["page"] for t in all_tables)

            for pn in sorted(pages_with_tables):
                try:
                    page = context.get_page(pn - 1)
                    page_text = page.get_text("text")
                    if not page_text:
                        continue
                    # 取页面前 200 字符作为上下文（仅展示用途）
                    ctx = page_text[:200].strip()

                    if ctx:
                        for t in all_tables:
                            if t["page"] == pn and not t.get("context_text"):
                                t["context_text"] = ctx
                except Exception:
                    pass
        except Exception as e:
            print(f"  [docx-per-page] 上下文补充异常: {e}")

    # ---- V2-Lite 页码分配（委托给独立模块 page_assigner） ----

    def _verify_docx_page_numbers(self, results, context):
        """基于 V2-Lite 物理坐标匹配的页码验证。

        - 逐页转换结果（extractor='docx_per_page'）：页码 100% 准确，跳过 DP
        - 全量转换结果（extractor='docx_based'）：委托 page_assigner 修正

        详见 codes/pdf_extractor/page_assigner.py
        """
        if not results:
            return results

        # 逐页转换 → 页码已准确，跳过 DP 验证
        if results[0].get("extractor") == "docx_per_page":
            print(f"  [docx] 逐页转换结果，页码已 100% 准确，跳过 DP 修正")
            return results

        from codes.pdf_extractor.page_assigner import assign_docx_pages
        return assign_docx_pages(results, context)

    # ---- 表格去重与标题提取 ----

    @staticmethod
    def _table_fingerprint(table_data, sample_cells=6):
        """生成表格的轻量指纹：前 sample_cells 个非空单元格文本（向后兼容接口）

        注意：增强版指纹请使用 _table_fingerprint_v2。
        """
        cells = []
        for row in table_data[:2]:  # 只看前两行
            for cell in row:
                if cell and str(cell).strip():
                    s = str(cell).strip()
                    if len(s) >= 2:
                        cells.append(s)
                        if len(cells) >= sample_cells:
                            break
            if len(cells) >= sample_cells:
                break
        return frozenset(cells) if cells else None

    @staticmethod
    def _table_fingerprint_v2(table_data, max_cells=20):
        """增强版指纹：5行 + 20格 + 数值优先 + 行结构哈希

        相比旧版 _table_fingerprint（只看前2行6格），增强版：
        1. 扩展到5行，max_cells=20，大幅提升区分度
        2. 数值型单元格优先（数值几乎不可能跨表重复）
        3. 附加行结构签名（每行的列数序列），区分不同结构的表

        Returns:
            (frozenset, tuple) 或 None: (指纹集合, 行结构签名)
        """
        cells = []
        numeric_cells = []
        text_cells = []

        for row in table_data[:5]:
            for cell in row:
                if not cell:
                    continue
                s = str(cell).strip()
                if len(s) < 2:
                    continue
                cleaned = s.replace(",", "").replace("%", "").replace("(", "-").replace(")", "")
                try:
                    float(cleaned)
                    numeric_cells.append(s.lower())
                except ValueError:
                    text_cells.append(s.lower())

        cells = numeric_cells[:15] + text_cells[:5]

        # 行结构签名：每行的列数序列
        row_structure = tuple(len(row) for row in table_data[:5])

        return (frozenset(cells[:max_cells]), row_structure) if cells else None

    @staticmethod
    def _deduplicate_v2_docx(v2_tables, docx_tables):
        """去重合并：docx 为主力通道，V2 补漏 docx 未覆盖的表格。

        原则：docx(pdf2word) 从 PDF 内容流重建表格结构，准确度高。
        V2 基于视觉坐标推测，存在合并单元格错位风险。

        规则：
        1. docx 所有表格无条件保留(主力通道)，保持原始阅读顺序不动
        2. 增强指纹匹配：行结构必须一致 + 公共词 >=3 + 公共词占比 >=40%
        3. 搜索范围扩展到 ±1 页容忍（同一张表可能被 docx 和 V2 分配到相邻页）
        4. V2 独有的表格(docx 漏掉的无框表/小表)按页码插入到 docx 序列的正确位置
        """
        if not docx_tables:
            return list(v2_tables)

        merged = list(docx_tables)  # docx 保持原顺序，绝不动
        matched_v2_ids = set()

        # 对每个 docx 表格，在 ±1 页范围内找 V2 匹配项
        for di, dt in enumerate(docx_tables):
            dt_page = dt.get("page", 0)
            dt_data = dt.get("data", [])
            dt_fp = PDFProcessor._table_fingerprint_v2(dt_data)

            if not dt_fp:
                continue

            dt_fp_set, dt_structure = dt_fp

            for vi, vt in enumerate(v2_tables):
                if vi in matched_v2_ids:
                    continue
                vt_page = vt.get("page", 0)
                # ±1 页容忍
                if abs(vt_page - dt_page) > 1:
                    continue
                vt_data = vt.get("data", [])
                vt_fp = PDFProcessor._table_fingerprint_v2(vt_data)
                if not vt_fp:
                    continue

                vt_fp_set, vt_structure = vt_fp

                # 行结构不同 → 不可能是同一张表
                if dt_structure != vt_structure:
                    continue

                common = dt_fp_set & vt_fp_set
                min_size = min(len(dt_fp_set), len(vt_fp_set))
                if min_size == 0:
                    continue
                # 增强匹配：>=3 个公共词 且 公共词占较小集合的 >=40%
                if len(common) >= 3 and len(common) >= min_size * 0.4:
                    matched_v2_ids.add(vi)
                    page_note = f"P{dt_page}" if vt_page == dt_page else f"P{dt_page}/P{vt_page}(±1)"
                    print(f"  [去重] {page_note}: docx表{di+1} ← V2表{vi+1}(匹配{len(common)}个公共词, 行结构={dt_structure})")

        # 收集未匹配的 V2 表格，按页码排序后插入到 docx 序列
        unmatched_v2 = []
        for vi, vt in enumerate(v2_tables):
            if vi not in matched_v2_ids:
                unmatched_v2.append(vt)

        if unmatched_v2:
            unmatched_v2.sort(key=lambda x: x.get("page", 0))
            for vt in reversed(unmatched_v2):
                vt_page = vt.get("page", 0)
                insert_pos = len(merged)
                for i in range(len(merged)):
                    if merged[i].get("page", 0) > vt_page:
                        insert_pos = i
                        break
                merged.insert(insert_pos, vt)

        v2_supplement = len(v2_tables) - len(matched_v2_ids)
        print(f"  [去重] 汇总: docx主力={len(docx_tables)}个 + V2补漏={v2_supplement}个 = 共{len(merged)}个表格")
        return merged

    @staticmethod
    def _filter_table_quality(tables):
        """过滤低质量表格。

        规则：
        1. 只有 1 行数据的跳过，除非是该页第一个表且含 >= 2 个数值
        2. 没有数值类型数据的跳过（纯文本块，不是表格）
        """
        import re

        def count_numbers(data):
            """数有多少个含数字的单元格"""
            cnt = 0
            for row in data:
                for cell in row:
                    if cell and re.search(r'\d', str(cell)):
                        cnt += 1
            return cnt

        def has_any_number(data):
            return count_numbers(data) > 0

        filtered = []
        removed = 0
        kept_exceptions = 0
        last_page = None

        for t in tables:
            data = t.get("data", [])
            page = t.get("page", 0)
            is_first_on_page = (page != last_page)
            last_page = page

            # 规则1：至少 2 行（例外：页首表 + 单行 + 2个以上数值）
            if len(data) < 2:
                if is_first_on_page and count_numbers(data) >= 2:
                    # 例外：页首关键指标表（如每股收益、ROE等单行汇总）
                    kept_exceptions += 1
                else:
                    removed += 1
                    continue

            # 规则2：至少有一个数字
            if not has_any_number(data):
                removed += 1
                continue

            filtered.append(t)

        if removed or kept_exceptions:
            parts = []
            if removed:
                parts.append(f"移除{removed}个")
            if kept_exceptions:
                parts.append(f"保留{kept_exceptions}个页首单行表(含数值)")
            print(f"  [质量过滤] {'; '.join(parts)}")
        return filtered

    class TableAutoCorrector:
        """基于列数据特征的无框表格自动纠错器。

        核心策略：
        1. 分析每列的主导数据类型（数字/文本）
        2. 检测表头区域中被垂直拆分的单元格（如"加权平均"+"净资产收益率"）
        3. 通过子表头检测避免误合并父子层级（如"每股收益"+"基本"）
        """

        @staticmethod
        def correct(table_data):
            if not table_data or len(table_data) < 2:
                return table_data

            data = [list(row) for row in table_data]
            max_cols = max((len(r) for r in data), default=0)
            for r in data:
                while len(r) < max_cols:
                    r.append("")

            col_types = PDFProcessor.TableAutoCorrector._analyze_col_types(data)
            data = PDFProcessor.TableAutoCorrector._merge_vertical_headers(data, col_types)

            # 清理空行并重新规范化
            data = [r for r in data if any(str(c).strip() for c in r)]
            if data:
                max_cols = max(len(r) for r in data)
                for r in data:
                    while len(r) < max_cols:
                        r.append("")
            return data

        @staticmethod
        def _analyze_col_types(data):
            import re
            max_cols = max(len(r) for r in data)
            # 数据区从第2行开始（跳过可能的表头），至少跳过1行
            data_start = min(2, max(1, len(data) // 2))

            types = []
            for c in range(max_cols):
                vals = []
                for r in range(data_start, len(data)):
                    if c < len(data[r]):
                        v = str(data[r][c]).strip()
                        if v:
                            vals.append(v)

                if not vals:
                    types.append("empty")
                    continue

                numeric = 0
                for v in vals:
                    v_clean = v.replace(",", "").replace("(", "-").replace(")", "").replace("%", "").replace("\u2030", "")
                    try:
                        float(v_clean)
                        numeric += 1
                    except ValueError:
                        pass

                if numeric > len(vals) * 0.6:
                    types.append("numeric")
                elif numeric == 0:
                    types.append("text")
                else:
                    types.append("mixed")
            return types

        @staticmethod
        def _merge_vertical_headers(data, col_types, max_header_rows=2):
            if len(data) < 3:
                return data

            corrected = []
            i = 0
            while i < len(data):
                row = list(data[i])

                if i + 1 < len(data) and i < max_header_rows:
                    next_row = list(data[i + 1])
                    merged_any = False

                    for c in range(min(len(row), len(next_row), len(col_types))):
                        if not row[c] or not next_row[c]:
                            continue

                        a = str(row[c]).strip()
                        b = str(next_row[c]).strip()

                        # 跳过子表头（如"基本"不应与"每股收益"合并）
                        if PDFProcessor.TableAutoCorrector._is_likely_child_header(data, i + 1, c):
                            continue

                        # 合并条件：短文本、无数字、该列数据区以数字为主
                        if (len(a) <= 8 and len(b) <= 8 and
                                len(a) + len(b) <= 15 and
                                col_types[c] == "numeric" and
                                not PDFProcessor.TableAutoCorrector._has_digit(a) and
                                not PDFProcessor.TableAutoCorrector._has_digit(b)):
                            row[c] = a + b
                            next_row[c] = ""
                            merged_any = True

                    if merged_any:
                        corrected.append(row)
                        if any(str(x).strip() for x in next_row):
                            corrected.append(next_row)
                        i += 2
                        continue

                corrected.append(row)
                i += 1
            return corrected

        @staticmethod
        def _is_likely_child_header(data, row_idx, col_idx):
            if row_idx >= len(data) or col_idx >= len(data[row_idx]):
                return False

            val = str(data[row_idx][col_idx]).strip()
            if not val or len(val) > 5:
                return False

            row = data[row_idx]
            short_cols = []
            for c, cell in enumerate(row):
                if cell and str(cell).strip():
                    s = str(cell).strip()
                    if len(s) <= 5 and not PDFProcessor.TableAutoCorrector._has_digit(s):
                        short_cols.append(c)

            if len(short_cols) < 2:
                return False

            if row_idx > 0:
                prev_row = data[row_idx - 1]
                parent_vals = []
                for c in short_cols:
                    if c < len(prev_row) and prev_row[c] and str(prev_row[c]).strip():
                        parent_vals.append(str(prev_row[c]).strip())
                parent_vals = [v for v in parent_vals if v]
                if len(parent_vals) <= 1:
                    return True
                if len(set(parent_vals)) == 1:
                    return True
            return False

        @staticmethod
        def _has_digit(s):
            import re
            return bool(re.search(r'\d', str(s)))

    @staticmethod
    def _extract_table_title(table_data):
        """从表格数据中提取标题文字（用于 Sheet 命名）。

        规则：取第一个长度>=4的非空单元格作为标题，
        如果没有，取表格第一行的前 3 个非空单元拼接。
        """
        if not table_data:
            return "表格"
        for row in table_data:
            for cell in row:
                if cell and str(cell).strip():
                    s = str(cell).strip()
                    if len(s) >= 4:
                        # 截取前12个字符
                        return s[:12].replace("/", "-").replace("\\", "-").replace("*", "")
        # fallback：拼接前几个非空
        parts = []
        for row in table_data:
            for cell in row:
                if cell and str(cell).strip():
                    s = str(cell).strip()
                    if s not in parts:
                        parts.append(s[:4])
                    if len(parts) >= 3:
                        break
            if len(parts) >= 3:
                break
        return "-".join(parts)[:20] if parts else "表格"

    # ---- 表格区域检测 ----

    def _detect_table_region(self, drawings, page_width, page_height):
        """从 drawing 中检测表格外框区域"""
        cfg = self.V2_CONFIG

        rectangles = [
            d for d in drawings
            if d["type"] == "rect"
            and d["x1"] - d["x0"] > page_width * cfg["table_min_width_ratio"]
            and d["y1"] - d["y0"] > cfg["table_min_height"]
        ]

        h_lines = [
            d for d in drawings
            if d["type"] == "line" and d["direction"] == "h"
            and d["x1"] - d["x0"] > page_width * cfg["table_min_width_ratio"]
        ]
        v_lines = [
            d for d in drawings
            if d["type"] == "line" and d["direction"] == "v"
            and d["y1"] - d["y0"] > cfg["table_min_height"]
        ]

        regions = []

        for rect in rectangles:
            regions.append((rect["x0"], rect["y0"], rect["x1"], rect["y1"]))

        if len(h_lines) >= 2 and len(v_lines) >= 2:
            x0 = min(l["x0"] for l in v_lines)
            x1 = max(l["x1"] for l in v_lines)
            y0 = min(l["y0"] for l in h_lines)
            y1 = max(l["y1"] for l in h_lines)
            if x1 - x0 > page_width * cfg["table_min_width_ratio"] and y1 - y0 > cfg["table_min_height"]:
                if not any(self._has_overlap((x0, y0, x1, y1), [r]) for r in regions):
                    regions.append((x0, y0, x1, y1))

        return regions

    def _has_overlap(self, rect, regions):
        """检测两个区域是否重叠"""
        rx0, ry0, rx1, ry1 = rect
        for gx0, gy0, gx1, gy1 in regions:
            if not (rx1 <= gx0 or rx0 >= gx1 or ry1 <= gy0 or ry0 >= gy1):
                return True
        return False

    def _detect_table_region_by_text(self, words, page_width, page_height):
        """无框表格区域检测（文本密度法）- 恢复原逻辑"""
        cfg = self.V2_CONFIG
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
        table_row_ranges = self._merge_consecutive(table_row_indices)

        # 恢复原逻辑：只检测上下边界（行），左右边界交给列检测处理
        regions = []
        for start, end in table_row_ranges:
            y0 = start * cell_h
            y1 = (end + 1) * cell_h
            # 左右边界使用整个页面宽度，让列检测算法决定真正的边界
            regions.append((0, y0, page_width, y1))

        return regions

    def _merge_consecutive(self, indices):
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

    # ---- 行边界检测 ----

    def _detect_horizontal_lines(self, page, words, page_drawings):
        """检测行边界，返回 [(y_top, y_bottom), ...]"""
        cfg = self.V2_CONFIG

        h_lines = sorted(set(
            d["y0"] for d in page_drawings
            if d["type"] == "line" and d["direction"] == "h"
            and d["x1"] - d["x0"] > page.rect.width * cfg["table_min_width_ratio"]
        ))

        if len(h_lines) >= 2:
            row_bounds = []
            for i in range(len(h_lines) - 1):
                row_bounds.append((h_lines[i], h_lines[i + 1]))
            return row_bounds

        # 无水平线 → 动态阈值分组
        y_threshold = self._compute_dynamic_y_threshold(words)
        rows = self._group_words_into_rows(words, y_threshold)

        row_bounds = []
        for row_words in rows:
            if row_words:
                y_top = min(w["y0"] for w in row_words)
                y_bot = max(w["y1"] for w in row_words)
                row_bounds.append((y_top, y_bot))

        return row_bounds

    def _compute_dynamic_y_threshold(self, words):
        """动态计算行分组阈值"""
        import statistics
        cfg = self.V2_CONFIG

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
        threshold = median_gap * cfg["y_threshold_factor"]
        return max(cfg["y_threshold_min"], min(cfg["y_threshold_max"], threshold))

    def _group_words_into_rows(self, words, y_threshold):
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

    # ---- 列边界检测 ----

    def _detect_vertical_lines(self, page, words, page_drawings):
        """
        检测列边界（v2规格：三指令融合）
        返回值：[x0, x1, x2, ...] 列分割线位置
        """
        import statistics
        cfg = self.V2_CONFIG

        # ----- 指令1：垂直线（最精确） -----
        v_lines = sorted(set(
            d["x0"] for d in page_drawings
            if d["type"] == "line" and d["direction"] == "v"
        ))

        if len(v_lines) >= 3:
            # 有3条以上垂直线 → ≥2列
            # 检查是否有两条线在页面中间（排除左右边框）
            inner_lines = [x for x in v_lines
                           if page.rect.width * 0.05 < x < page.rect.width * 0.95]
            if len(inner_lines) >= 2:
                return v_lines  # 直接用垂直线坐标

        # ----- 指令2：文本对齐聚簇 -----
        x0_list = [w["x0"] for w in words if w["text"].strip()]
        x1_list = [w["x1"] for w in words if w["text"].strip()]

        if x0_list:
            # x0对齐点检测
            left_aligns = self._cluster_1d(x0_list, cfg["align_tolerance"])
            right_aligns = self._cluster_1d(x1_list, cfg["align_tolerance"])

            # 合并左右对齐点
            all_aligns = sorted(set(left_aligns + right_aligns))

            # 如果对齐点多于2个，用对齐点作为列边界
            if len(all_aligns) >= 3:
                return all_aligns

        # ----- 指令3：gap检测（兜底） -----
        all_x = sorted(set(x0_list + x1_list))

        if len(all_x) < 3:
            return [0, page.rect.width]

        # 计算gap
        gaps = []
        gap_positions = []
        for i in range(len(all_x) - 1):
            gap = all_x[i + 1] - all_x[i]
            if gap > 0:
                gaps.append(gap)
                gap_positions.append((all_x[i], all_x[i + 1]))

        if not gaps:
            return [0, page.rect.width]

        # 用中位数 + 标准差作为阈值
        median_gap = statistics.median(gaps)
        stdev_gap = statistics.stdev(gaps) if len(gaps) >= 2 else median_gap * 0.5
        gap_threshold = max(median_gap + stdev_gap * cfg["gap_factor"], cfg["gap_min"])

        # 找到gap大于阈值的位置
        boundaries = [0]
        for (left, right), gap in zip(gap_positions, gaps):
            if gap > gap_threshold:
                boundaries.append((left + right) / 2)
        boundaries.append(page.rect.width)

        return boundaries

    def _cluster_1d(self, values, tolerance=4):
        """一维坐标聚簇，找出文本对齐位置（v2规格：最小簇大小=3）"""
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

    # ---- 网格填充 ----

    def _assign_words_to_grid(self, words, row_bounds, col_bounds):
        """将 words 分配到行列网格中（v2规格：重叠面积法）"""
        cfg = self.V2_CONFIG
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
            margin = (row_bounds[0][1] - row_bounds[0][0]) * cfg["row_margin_factor"] if row_bounds else 0
            for r, (y_top, y_bot) in enumerate(row_bounds):
                if (y_top - margin) <= center_y <= (y_bot + margin):
                    row_idx = r
                    break

            # 列分配：简单重叠法
            col_idx = None
            max_overlap = 0

            for c in range(n_cols):
                col_left = col_bounds[c]
                col_right = col_bounds[c + 1]
                
                overlap = max(0.0, min(wx1, col_right) - max(wx0, col_left))
                if overlap > max_overlap:
                    max_overlap = overlap
                    col_idx = c
            
            # 兜底：如果没有任何重叠，使用最近列中心
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

    # ---- 置信度评分 ----

    def _compute_table_confidence(self, table_data, has_border, page_words):
        """计算表格提取结果的置信度"""
        import statistics
        cfg = self.V2_CONFIG

        if not table_data or len(table_data) < 2:
            return 0.0

        scores = []

        # 因子1: 列数一致性
        col_counts = [len(row) for row in table_data if row]
        if col_counts and len(col_counts) >= 2:
            mean_cols = statistics.mean(col_counts)
            cv = statistics.stdev(col_counts) / mean_cols if mean_cols > 0 else 1.0
            col_consistency = max(0.0, 1.0 - cv * 2)
            scores.append((col_consistency, cfg["confidence_col_weight"]))
        else:
            scores.append((0.5, cfg["confidence_col_weight"]))

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
        scores.append((empty_score, cfg["confidence_empty_weight"]))

        # 因子3: 数值占比
        def is_numeric(text):
            text = str(text).strip().replace(",", "").replace("(", "-").replace(")", "")
            if not text:
                return False
            try:
                float(text)
                return True
            except:
                if text.endswith("%"):
                    try:
                        float(text[:-1])
                        return True
                    except:
                        return False
                return False

        numeric_count = sum(1 for row in table_data for cell in row
                            if is_numeric(str(cell).strip()))
        numeric_ratio = numeric_count / max(total_cells, 1)
        numeric_score = min(numeric_ratio * 2, 1.0) if numeric_ratio < 0.5 else 1.0
        scores.append((numeric_score, cfg["confidence_num_weight"]))

        # 加权综合
        weighted_sum = sum(s * w for s, w in scores)
        weighted_total = sum(w for _, w in scores)
        confidence = weighted_sum / weighted_total
        if has_border:
            confidence += cfg["confidence_line_bonus"]

        return min(1.0, max(0.0, confidence))


# ============================================================
# LLM视觉识别模块
# ============================================================
class VisionLLM:
    """视觉大模型接口"""

    def __init__(self, api_key=None, endpoint=None, model=None):
        self.config = load_config()
        self.api_key = api_key or self.config.get("doubao_api_key", "")
        self.endpoint = endpoint or self.config.get("doubao_endpoint", "ark.cn-beijing.volces.com")
        self.model = model or self.config.get("doubao_model", "doubao-pro-32k")

    def test_connection(self):
        """测试API连接"""
        import requests
        api_url = f"https://{self.endpoint}/api/v3/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 10
        }
        try:
            resp = requests.post(api_url, headers=headers, json=data, timeout=15)
            resp.raise_for_status()
            return True, "API连接成功！"
        except requests.exceptions.Timeout:
            return False, "连接超时，请检查网络或API地址"
        except requests.exceptions.RequestException as e:
            return False, f"连接失败: {str(e)}"

    def recognize_table(self, image_path):
        """识别图片中的表格"""
        if not self.api_key:
            return {"success": False, "error": "未配置API Key"}

        import base64
        import requests

        with open(image_path, 'rb') as f:
            img_data = base64.b64encode(f.read()).decode()

        api_url = f"https://{self.endpoint}/api/v3/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        prompt = """请识别这张银行年报截图中的财务表格，以JSON数组格式输出。

要求：
1. 只输出JSON数组，不要其他文字
2. 每行数据是一个数组，格式如：["项目名称", "2023年", "2022年", "同比增减"]
3. 保持原表格的行列结构
4. 数字去掉逗号，保留原数值
5. 表头也要包含在结果中

输出示例格式：
[["项目", "2023年末", "2022年末", "变动率"], ["流动资产", "100,000", "90,000", "11.11%"], ...]"""

        data = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_data}"}}
                    ]
                }
            ],
            "max_tokens": 4000,
            "temperature": 0.1
        }

        try:
            resp = requests.post(api_url, headers=headers, json=data, timeout=120)
            resp.raise_for_status()
            result = resp.json()

            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                return {"success": True, "data": content}
            else:
                return {"success": False, "error": "API返回格式错误"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def batch_recognize(self, image_paths, progress_callback=None):
        """批量识别图片"""
        results = []

        for i, img_path in enumerate(image_paths):
            if progress_callback:
                progress_callback(i + 1, len(image_paths))

            result = self.recognize_table(img_path)
            results.append({
                "page": i + 1,
                "image": img_path,
                "result": result
            })

            if i < len(image_paths) - 1:
                time.sleep(1)

        return results


# ============================================================
# 表格上下文LLM模块 - 为表格生成名称和摘要
# ============================================================
class TableContextLLM:
    """文本型LLM接口 - 使用 deepseek-v4 等模型为表格生成名称和摘要"""

    MAX_PREVIEW_ROWS = 5          # 表格预览最多行数
    MAX_PREVIEW_CELL_LEN = 30     # 单格最大字符数

    def __init__(self, api_key=None, endpoint=None, model=None):
        self.config = load_config()
        self.api_key = api_key or self.config.get("deepseek_api_key", "")
        self.endpoint = endpoint or self.config.get("deepseek_endpoint", "api.deepseek.com")
        self.model = model or self.config.get("deepseek_model", "deepseek-chat")

    def test_connection(self):
        """测试 deepseek API 连通性"""
        import requests
        api_url = f"https://{self.endpoint}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 10
        }
        try:
            resp = requests.post(api_url, headers=headers, json=data, timeout=15)
            resp.raise_for_status()
            return True, "DeepSeek API 连接成功！"
        except requests.exceptions.Timeout:
            return False, "连接超时，请检查网络或API地址"
        except requests.exceptions.HTTPError as e:
            return False, f"HTTP错误: {e.response.status_code} - {e.response.reason}"
        except requests.exceptions.RequestException as e:
            return False, f"连接失败: {str(e)}"

    @staticmethod
    def _build_table_preview(table_data, max_rows=None, max_cell_len=None):
        """将表格数据压缩为文本预览（用于 LLM prompt）"""
        if max_rows is None:
            max_rows = TableContextLLM.MAX_PREVIEW_ROWS
        if max_cell_len is None:
            max_cell_len = TableContextLLM.MAX_PREVIEW_CELL_LEN

        if not table_data:
            return "（表格数据为空）"

        lines = []
        for row_idx, row in enumerate(table_data):
            if row_idx >= max_rows:
                lines.append(f"... (共{len(table_data)}行，仅显示前{max_rows}行)")
                break
            cells = []
            for cell in row:
                s = str(cell).strip()
                if len(s) > max_cell_len:
                    s = s[:max_cell_len - 3] + "..."
                cells.append(s)
            lines.append(" | ".join(cells))

        return "\n".join(lines)

    def generate_table_name(self, context_text, table_data):
        """将上下文文本 + 表格摘要发给 LLM，返回 {"title": str, "summary": str}"""
        if not self.api_key:
            return {"title": "", "summary": "", "error": "未配置 DeepSeek API Key"}

        import json as _json
        import requests

        table_preview = self._build_table_preview(table_data)

        prompt = f"""你是一个财务文档分析专家。下面是一份银行年报中提取的表格数据。
请根据表格上方的描述文字和表格内容，为这个表格生成一个规范的名称和一句话摘要。

表格上方描述文字：
{context_text if context_text else "（无上方描述文字）"}

表格内容预览（前{self.MAX_PREVIEW_ROWS}行）：
{table_preview}

请以JSON格式返回，只返回JSON，不要其他文字：
{{"title": "表格的规范名称（如'合并资产负债表'、'利润表'）", "summary": "一句话描述该表格的主要内容（不超过50字）"}}"""

        api_url = f"https://{self.endpoint}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一个专业的财务表格分析助手，擅长为财务表格生成规范的标题和摘要。请只返回JSON格式的结果。"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 500,
            "temperature": 0.1
        }

        try:
            resp = requests.post(api_url, headers=headers, json=data, timeout=30)
            resp.raise_for_status()
            result = resp.json()

            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                # 尝试解析 JSON
                try:
                    parsed = _json.loads(content)
                    return {
                        "title": parsed.get("title", ""),
                        "summary": parsed.get("summary", "")
                    }
                except _json.JSONDecodeError:
                    # 尝试从文本中提取 JSON
                    import re
                    match = re.search(r'\{[^}]*\}', content, re.DOTALL)
                    if match:
                        try:
                            parsed = _json.loads(match.group())
                            return {
                                "title": parsed.get("title", ""),
                                "summary": parsed.get("summary", "")
                            }
                        except _json.JSONDecodeError:
                            pass
                    return {"title": content.strip()[:100], "summary": "", "error": "JSON解析失败，已用原始返回"}
            else:
                return {"title": "", "summary": "", "error": "API返回格式错误"}

        except requests.exceptions.Timeout:
            return {"title": "", "summary": "", "error": "请求超时"}
        except Exception as e:
            return {"title": "", "summary": "", "error": str(e)}

    def batch_generate(self, tables, progress_callback=None):
        """批量处理多个表格，返回每个表格的命名结果列表

        Args:
            tables: [{"context_text": str, "data": [[...], ...]}, ...]
            progress_callback: callable(current, total, message)

        Returns:
            [{"title": str, "summary": str, "error": str or None}, ...]
        """
        results = []
        total = len(tables)

        for i, table in enumerate(tables):
            context_text = table.get("context_text", "")
            table_data = table.get("data", [])

            if progress_callback:
                progress_callback(i + 1, total, f"正在为第{i+1}个表格生成名称...")

            result = self.generate_table_name(context_text, table_data)
            results.append(result)

            if i < total - 1:
                time.sleep(0.3)  # 限速，避免触发 API 频率限制

        return results


# ============================================================
# Excel导出模块
# ============================================================
class ExcelExporter:
    """Excel导出器"""

    @staticmethod
    def parse_json_table(json_str):
        """解析LLM返回的JSON字符串"""
        import json
        import re

        try:
            return json.loads(json_str)
        except:
            pass

        match = re.search(r'\[.*\]', json_str, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass

        return None

    @staticmethod
    def export_tables(tables_data, output_path):
        """将表格数据导出为Excel——每个表格一个独立Sheet。

        Sheet 命名: P{页码}-T{序号}-{标题前若干字}
        表头区: 标题行(粗体) + 来源行(灰色) + 空白行
        数据区: 左右各留1列空白，上下各留1行空白
        """
        wb = Workbook()

        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        # 过滤掉空数据表格，按页码+表格序号排序
        valid_tables = [t for t in tables_data if t.get("data")]
        if not valid_tables:
            # 创建一个空 sheet 避免报错
            ws = wb.active
            ws.title = "无数据"
            ws.cell(row=1, column=1, value="未提取到表格数据")
            wb.save(output_path)
            return True

        # 按页排序，同页按 extractor（docx 优先）
        def sort_key(t):
            page = t.get("page", 0)
            ext = t.get("extractor", "")
            is_docx = 0 if ext.startswith("docx") else 1
            return (page, is_docx)

        valid_tables.sort(key=sort_key)

        # 按页分组，分配每页内序号
        page_tables = {}
        for t in valid_tables:
            page = t.get("page", 0)
            if page not in page_tables:
                page_tables[page] = []
            page_tables[page].append(t)

        # 计算最大页码位数，用于零补位
        max_page = max(page_tables.keys()) if page_tables else 1
        page_digits = max(3, len(str(max_page)))  # 至少 3 位

        # 计算全局最大本页表数，用于零补位
        max_per_page = max(len(v) for v in page_tables.values()) if page_tables else 1
        seq_digits = max(2, len(str(max_per_page)))  # 至少 2 位

        global_idx = 0
        for page in sorted(page_tables.keys()):
            tables_on_page = page_tables[page]
            for seq, table_info in enumerate(tables_on_page, 1):
                table_data = table_info.get("data", [])
                if not table_data:
                    continue

                # ---- Sheet 命名（零补位确保字符串排序=数值排序）----
                # 优先级：llm_title > title > 自动提取
                title = table_info.get("llm_title") or table_info.get("title") or PDFProcessor._extract_table_title(table_data)
                sheet_name = f"P{page:0{page_digits}d}-T{seq:0{seq_digits}d}-{title}"
                # Sheet 名最长 31 字符
                if len(sheet_name) > 31:
                    sheet_name = sheet_name[:28] + "..."
                # 替换非法字符
                sheet_name = sheet_name.replace(":", "-").replace("[", "(").replace("]", ")")

                if global_idx == 0:
                    ws = wb.active
                else:
                    ws = wb.create_sheet()
                ws.title = sheet_name
                global_idx += 1

                # 计算列布局
                max_cols = 0
                for row in table_data:
                    max_cols = max(max_cols, len(row))
                data_start_col = 2  # 左侧留白
                data_end_col = data_start_col + max_cols  # 数据结束列（也是右侧留白）

                row_num = 1

                # ---- 表头区 ----
                # 标题行
                ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=data_end_col)
                title_cell = ws.cell(row=row_num, column=1, value=title)
                title_cell.font = Font(bold=True, size=12)
                title_cell.alignment = Alignment(horizontal="left", vertical="center")
                row_num += 1

                # 来源行
                ext_label = table_info.get("extractor", "unknown")
                ext_display = "docx精准通道" if ext_label.startswith("docx") else "V2快速通道"
                ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=data_end_col)
                source_cell = ws.cell(row=row_num, column=1,
                                      value=f"来源: PDF第{page}页 | 提取方式: {ext_display}")
                source_cell.font = Font(color="808080", size=9)
                source_cell.alignment = Alignment(horizontal="left", vertical="center")
                row_num += 1

                # 标题与数据之间的空行
                row_num += 1

                # ---- 数据区：顶部空白行 ----
                for col in range(1, data_end_col + 1):
                    cell = ws.cell(row=row_num, column=col, value="")
                    cell.border = thin_border
                row_num += 1

                # ---- 写入表格数据 ----
                for row in table_data:
                    # 左侧空白列
                    cell = ws.cell(row=row_num, column=1, value="")
                    cell.border = thin_border

                    for col_idx, value in enumerate(row):
                        col = data_start_col + col_idx
                        cell = ws.cell(row=row_num, column=col, value=str(value) if value is not None else "")
                        cell.border = thin_border
                        cell.alignment = Alignment(horizontal="left", vertical="center")

                    # 右侧空白列
                    cell = ws.cell(row=row_num, column=data_end_col, value="")
                    cell.border = thin_border

                    row_num += 1

                # ---- 底部空白行 ----
                for col in range(1, data_end_col + 1):
                    cell = ws.cell(row=row_num, column=col, value="")
                    cell.border = thin_border
                row_num += 1

                # ---- 自动列宽 ----
                for col_idx in range(1, data_end_col + 1):
                    max_length = 0
                    col_letter = get_column_letter(col_idx)
                    for r in range(1, row_num):
                        cv = ws.cell(row=r, column=col_idx).value
                        if cv:
                            max_length = max(max_length, len(str(cv)))
                    adjusted_width = min(max_length + 2, 50)
                    ws.column_dimensions[col_letter].width = adjusted_width

        wb.save(output_path)
        return True


# ============================================================
# 自动清洗 & 合并 工具函数（数据处理层，无 UI 依赖）
# ============================================================

def _remove_spaces_data(data):
    """智能删除空单元格：仅删除每行左右两端的空单元格，保留内部空单元格。

    策略：
    - 找到该行第一个非空单元格的索引（左边界）
    - 找到该行最后一个非空单元格的索引（右边界）
    - 删除左边界之前和右边界之后的所有单元格
    - 保持内部单元格结构不变（中间的空白列会被保留）

    Returns:
        新数组（list of lists）
    """
    import copy
    if not data:
        return []

    result = []
    for row in data:
        # 找第一个和最后一个非空单元格索引
        first = -1
        last = -1
        for i, cell in enumerate(row):
            if cell is not None and str(cell).strip():
                if first < 0:
                    first = i
                last = i

        if first < 0:
            # 全空行，保留原样
            result.append(list(row))
        else:
            # 只保留 [first .. last] 范围内的单元格
            trimmed = row[first:last + 1]
            result.append(list(trimmed))

    return result


def _clean_data_cells(data):
    """清洗数值类单元格：含中文或英文的跳过，其余移除非数值字符。原地修改。

    纯横线单元格（--、---、—等）视为省略标记，保留不动。
    横线与数字混合时（如 ---100），横线被清洗掉。
    """
    import re
    for row in data:
        for c in range(len(row)):
            cell = row[c]
            if not cell:
                continue
            text = str(cell).strip()
            if not text:
                row[c] = ""
                continue
            # 省略标记（只含横线/破折号，无文本数字）→ 保留不动
            if re.fullmatch(r'[\-\u2014\u2015\u2500\uFF0D]+', text):
                row[c] = text
                continue
            # 含中文或英文字母 → 跳过
            if re.search(r'[\u4e00-\u9fff]|[a-zA-Z]', text):
                row[c] = text
                continue
            # 清洗：保留数字、逗号、小数点、负号、括号、百分号（仅删下划线等杂质）
            cleaned = re.sub(r'[^0-9,\.\-\(\)\%]', '', text).strip()
            row[c] = cleaned
    return data


def _is_numeric_data(text):
    """判断文本是否为纯数值类（含数字格式字符），用于检测无表头数据行。"""
    if not text or not str(text).strip():
        return False
    import re
    t = str(text).strip()
    # 只要不含中文或英文字母，且至少有一个数字，就认为是数值
    if re.search(r'[\u4e00-\u9fff]|[a-zA-Z]', t):
        return False
    return bool(re.search(r'[0-9]', t))


def _remove_empty_rows(data):
    """删除表格中的全空行。只含空字符串或 None 的行会被移除。原地修改。"""
    if not data:
        return data
    data[:] = [
        row for row in data
        if any(cell is not None and str(cell).strip() for cell in row)
    ]
    return data


def _compact_empty_cells(data):
    """删除每行内部的空单元格，压缩为紧凑列。打标记 cells_compacted。

    与 _remove_spaces_data 不同：后者只删两端空单元格，此函数删除行内所有空单元格。

    特殊处理：如果一行只有首列有内容、后续全空，判定为跨列标题行，
    保留原始列数不压缩，避免表格结构断裂。
    """
    if not data:
        return data, False

    # 计算全局最大列数，用于识别跨列标题行
    max_cols = max((len(row) for row in data), default=0)

    # 辅助：判断是否为跨列标题行（仅首列有内容）
    def _is_header_only(row):
        if not row or len(row) < 2 or max_cols < 2:
            return False
        first = row[0]
        if first is None or not str(first).strip():
            return False
        return all(
            cell is None or not str(cell).strip()
            for cell in row[1:]
        )

    compacted = False
    for r in range(len(data)):
        row = data[r]

        if _is_header_only(row):
            # 跨列标题行：保持原始列数不变，仅裁剪到 max_cols
            while len(row) > max_cols and not str(row[-1]).strip():
                row.pop()
            continue

        # 常规压缩：保留非空单元格（含省略标记 —）
        compact = [
            cell for cell in row
            if cell is not None and str(cell).strip()
        ]
        if len(compact) < len(row):
            compacted = True
            data[r] = compact

    return data, compacted


def _deduplicate_columns(data):
    """删除因合并单元格展开导致的完全重复相邻列。

    pdf2docx 在展开跨列合并的标题格时，会把标题文本复制到每个子列，
    导致相邻多列内容完全相同。此函数检测并删除重复列。

    判定条件：如果两个相邻列在【所有行】中的值完全相同 → 列 c+1 被删除。
    从右向左扫描以避免索引偏移，迭代直到无重复列。

    Args:
        data: list[list[str]]

    Returns:
        (cleaned_data, removed_count): 去重后数据 + 删除的列数
    """
    if len(data) < 2:
        return data, 0

    max_cols = max((len(row) for row in data), default=0)
    if max_cols < 2:
        return data, 0

    removed_count = 0
    # 从右向左扫描，避免删除后索引偏移影响左侧比较
    for c in range(max_cols - 2, -1, -1):
        all_match = True
        for row in data:
            v1 = str(row[c]).strip() if c < len(row) and row[c] else ""
            v2 = str(row[c + 1]).strip() if c + 1 < len(row) and row[c + 1] else ""
            if v1 != v2:
                all_match = False
                break

        if all_match:
            for row in data:
                if c + 1 < len(row):
                    row.pop(c + 1)
            removed_count += 1

    return data, removed_count


def _clean_merged_cell_bleed(data):
    """清除 pdf2docx 合并单元格展开时渗透到下行脚注的数值假数据。

    pdf2docx 在处理跨行合并格时，会把合并区域的文本展开到每行。
    如果脚注/注释行占据了一个宽合并格，pdf2docx 可能把上方数据行的
    数值也渗透进脚注行的右侧列。

    检测逻辑：如果某行的数值列（第 1 列起，非空）跟上一行对应列全部
    一模一样，且首列文本与上行不同，判定为渗透，清空该行的数值部分。

    纯文本列（首列）不受影响，仅清空后续数值/金额列。

    Args:
        data: list[list[str]]

    Returns:
        (cleaned_data, cleaned_count): 清洗后数据 + 受影响行数
    """
    if len(data) < 2:
        return data, 0
    
    cleaned_count = 0
    for i in range(1, len(data)):
        prev_row = data[i - 1]
        curr_row = data[i]
        
        # 首列文本必须与上行不同（排除重复表头等正常情况）
        prev_text = str(prev_row[0]).strip() if prev_row else ""
        curr_text = str(curr_row[0]).strip() if curr_row else ""
        if not curr_text or curr_text == prev_text:
            continue
        
        # 取 min 列数，比较第 1 列起的所有列
        n_cols = min(len(prev_row), len(curr_row))
        if n_cols <= 1:
            continue
        
        # 检查数值列是否跟上一行完全一致
        all_match = True
        has_content = False
        for c in range(1, n_cols):
            p = str(prev_row[c]).strip() if prev_row[c] else ""
            q = str(curr_row[c]).strip() if curr_row[c] else ""
            if p or q:
                has_content = True
            if p != q:
                all_match = False
                break
        
        # 所有数值列与上行完全一致 → 渗透，清空
        if has_content and all_match:
            for c in range(1, len(curr_row)):
                curr_row[c] = ""
            cleaned_count += 1
    
    return data, cleaned_count


def _infer_numeric_pattern(data, skip_col):
    """从同一表格的其他列推断常见数值格式，用于拆分拼接列。

    扫描非跳过列的值，寻找小数/百分比等常见金融数值格式，
    返回一个正则模式字符串给 re.findall 使用。

    Args:
        data: list[list[str]]
        skip_col: 要跳过（不参考）的列索引

    Returns:
        str: 正则模式，如 r'\d+\.\d{2}' 或默认回退模式
    """
    import re

    max_cols = max((len(row) for row in data), default=0)

    for c in range(max_cols):
        if c == skip_col:
            continue
        for row in data:
            val = str(row[c]).strip() if c < len(row) and row[c] else ""
            if not val:
                continue
            # 检测 "1.33" 格式（1-3位小数）
            if re.match(r'^-?\d+\.\d{1,3}$', val):
                return r'-?\d+\.\d{1,3}'
            # 检测 "1.33%" 格式
            if re.match(r'^-?\d+\.\d{1,3}\s*%$', val):
                return r'-?\d+\.\d{1,3}\s*%'
            # 检测 "(1.33)" 格式
            if re.match(r'^\(\d+\.\d{1,3}\)$', val):
                return r'\(\d+\.\d{1,3}\)'
            # 检测负数 "-1.33"
            if re.match(r'^ -\d+\.\d{1,3}$', val):
                return r' -\d+\.\d{1,3}'

    # 回退：宽数值模式（匹配含逗号分隔符、可选百分号、可选括号的数值）
    return r'\(?-?[\d,]+\.?\d*[%％]?\)?'


def _find_reference_row_count(data, skip_col, target_rows):
    """找同一表格中格式正常的"参考列"，用于确定拼接串应拆分成几个独立值。

    参考列条件：与目标列填充了相同数量的数据行，且每行值各不相同。

    Args:
        data: list[list[str]]
        skip_col: 不参考的列索引
        target_rows: 目标列有非空值的行索引列表

    Returns:
        int: 参考列建议的拆分数量；若无合适参考列则返回 len(target_rows)
    """
    max_cols = max((len(row) for row in data), default=0)
    target_set = set(target_rows)

    for c in range(max_cols):
        if c == skip_col:
            continue
        # 收集该列在有值的行索引及值集合
        filled_rows = set()
        values_set = set()
        for r, row in enumerate(data):
            val = str(row[c]).strip() if c < len(row) and row[c] else ""
            if val:
                filled_rows.add(r)
                values_set.add(val)

        # 条件1: 该列填充的行数 >= 目标行数的一半（排除稀疏列）
        # 条件2: 值各不相同（不是被复制的假数据列）
        # 条件3: 填充行与目标行有较高重叠
        overlap = len(filled_rows & target_set)
        if (len(filled_rows) >= len(target_rows) * 0.5
                and len(values_set) >= len(filled_rows) * 0.7
                and overlap >= len(target_rows) * 0.7):
            return len(filled_rows)

    return len(target_rows)


def _try_split_numeric_patterns(concat_str):
    """尝试多种正则模式拆分拼接串，返回拆分后的 token 列表。

    按精确度从高到低依次尝试，每种模式要求 tokens 拼回去等于原串
    （即 join(tokens) == concat_str），确保拆分无遗漏/无重叠。

    Args:
        concat_str: 拼接串

    Returns:
        list[str] or None: 拆分成功返回 token 列表，失败返回 None
    """
    # 模式列表：从精确到宽松
    candidates = [
        r'\d+\.\d{2}',           # 1.33  (两位小数)
        r'\d+\.\d{3}',           # 1.331 (三位小数)
        r'\d+\.\d{2}%',          # 1.33%
        r'\d+\.\d{1,3}\s*%',     # 1.33% / 5.6 %
        r'\(\d+\.\d{1,3}\)',     # (1.33)
        r'-\d+\.\d{1,3}',        # -1.33
        r'\d+\.\d{1,3}',         # 1.33 / 10.5 (通用小数)
        r'[\d,]+\.\d*[%％]?',    # 1,234.56 / 5% (宽数值)
    ]

    import re

    for pattern in candidates:
        tokens = re.findall(pattern, concat_str)
        if len(tokens) <= 1:
            continue
        # 关键验证：拼回去必须等于原串
        if ''.join(tokens) == concat_str:
            return tokens

    return None


def _split_concatenated_column(data):
    """拆分因 pdf2docx 垂直合并格展开导致的拼接值列。

    pdf2docx 在展开垂直跨行合并单元格时，有时会把合并区域内所有行的文本
    拼接成一个长串，然后复制到展开后的每一行对应列中。
    此函数通过多策略检测并拆分为每行一个独立值。

    检测条件：
      1. 某列在所有非空行中值完全一致
      2. 该值长度 > 10 字符（排除普通的共享值/短标签）

    拆分策略（按优先级）：
      策略1: 从同行其他列推断数值格式 → 正则拆分 → 验证 token 数与数据行数匹配
      策略2: 固定模式库逐一尝试 → 验证拆分回原串一致
      策略3: 用参考列确定 N → 尝试等分子串
      全部失败: 保留原值，不修改

    Args:
        data: list[list[str]]

    Returns:
        (cleaned_data, split_count): 拆分后数据 + 成功拆分的列数
    """
    import re

    if not data or len(data) < 2:
        return data, 0

    max_cols = max((len(row) for row in data), default=0)
    if max_cols < 2:
        return data, 0

    split_count = 0

    for c in range(max_cols):
        # ---- 阶段A: 检测拼接列 ----
        value_rows = []        # 该列有非空值的行索引
        first_val = None
        all_same = True

        for r, row in enumerate(data):
            val = str(row[c]).strip() if c < len(row) and row[c] else ""
            if val:
                if first_val is None:
                    first_val = val
                elif val != first_val:
                    all_same = False
                value_rows.append(r)

        if not value_rows or not all_same:
            continue
        if len(first_val) <= 10:
            continue

        n_rows = len(value_rows)
        concat = first_val

        # ---- 阶段B: 策略1 — 从同行列推断格式 ----
        numeric_pattern = _infer_numeric_pattern(data, c)
        tokens = re.findall(numeric_pattern, concat)

        # 验证：非空 token 数量与数据行数匹配
        valid_tokens = [t for t in tokens if t.strip()]
        if len(valid_tokens) == n_rows:
            for idx, r in enumerate(value_rows):
                data[r][c] = valid_tokens[idx]
            split_count += 1
            continue

        # ---- 阶段C: 策略2 — 固定模式库逐一尝试 ----
        tokens = _try_split_numeric_patterns(concat)
        if tokens and len(tokens) == n_rows:
            for idx, r in enumerate(value_rows):
                data[r][c] = tokens[idx]
            split_count += 1
            continue

        # ---- 阶段D: 策略3 — 参考列确定N + 等分子串 ----
        ref_n = _find_reference_row_count(data, c, value_rows)
        if ref_n > 1 and len(concat) % ref_n == 0:
            seg_len = len(concat) // ref_n
            segments = [concat[i:i + seg_len] for i in range(0, len(concat), seg_len)]
            # 每段至少含数字或字母，避免切出纯空白
            if all(any(ch.isdigit() or ch.isalpha() for ch in s) for s in segments):
                for idx, r in enumerate(value_rows[:ref_n]):
                    if idx < len(segments):
                        data[r][c] = segments[idx]
                split_count += 1
                continue

    return data, split_count


def _auto_merge_split_tables(results):
    """检测并合并被 pdf2docx 拆分断裂的相邻表格。
    仅合并 data（清洗后），original_data 不动。

    注意：逐页转换结果（extractor='docx_per_page'）不会出现
    跨页拆分问题，跳过合并以避免误合并同页不同表格。
    """
    # 逐页转换 → 无拆分断裂，直接返回
    if results and results[0].get("extractor") == "docx_per_page":
        return results

    results.sort(key=lambda x: x.get("page", 0))

    import re

    # 收集所有有效表格的索引（排除没有 data 的）
    valid_indices = [i for i, t in enumerate(results) if t.get("data")]

    merged_any = True
    merge_count = 0

    while merged_any:
        merged_any = False
        to_remove = set()
        merges_this_round = []

        for idx_i in range(len(valid_indices) - 1):
            i = valid_indices[idx_i]
            j = valid_indices[idx_i + 1]
            if i in to_remove or j in to_remove:
                continue

            table_a = results[i]
            table_b = results[j]

            # 条件1: 列数相同
            cols_a = max((len(r) for r in table_a["data"]), default=0)
            cols_b = max((len(r) for r in table_b["data"]), default=0)
            if cols_a == 0 or cols_b == 0 or cols_a != cols_b:
                continue

            # 辅助：判断是否为本页第一个表格
            def _is_first_on_page(idx):
                page = results[idx].get("page", 0)
                for k in valid_indices:
                    if k == idx:
                        break
                    if results[k].get("page") == page and results[k].get("data"):
                        return False
                return True

            # 规则：两表之间有2行以上实质文本 → 不合并（它们是独立的表）
            ctx_b = table_b.get("context_text", "")
            meaningful_lines = []
            if ctx_b:
                meaningful_lines = [l for l in ctx_b.split('\n') if l.strip() and not re.match(r'^[\d\-\s]+$', l.strip())]
            if len(meaningful_lines) >= 2:
                continue

            # 规则：后表是本页第一个且有实质描述文字 → 不合并（新章节的开始）
            if _is_first_on_page(j) and len(meaningful_lines) >= 1:
                continue

            # 条件2: table_b 首行全部是纯数值类（无表头）
            first_row_b = table_b["data"][0] if table_b["data"] else []
            if not first_row_b or not all(_is_numeric_data(cell) for cell in first_row_b):
                numeric_count = sum(1 for cell in first_row_b if _is_numeric_data(cell))
                if numeric_count < max(1, len(first_row_b) * 0.5):
                    continue

            # 执行合并：B 行追加到 A 末尾
            page_a = table_a.get("page", 0)
            page_b = table_b.get("page", 0)

            # 确保每行列数一致
            for row in table_b["data"]:
                while len(row) < cols_a:
                    row.append("")
            table_a["data"].extend([row[:cols_a] for row in table_b["data"]])
            table_a["rows"] = len(table_a["data"])

            if "original_data" in table_b:
                if "original_data" not in table_a:
                    table_a["original_data"] = []
                for row in table_b["original_data"]:
                    while len(row) < cols_a:
                        row.append("")
                table_a["original_data"].extend([row[:cols_a] for row in table_b["original_data"]])

            # 标记 B 为已合并（需移除）
            to_remove.add(j)
            merge_count += 1
            merges_this_round.append((i, j, page_a, page_b, len(table_b["data"])))

        # 批量移除
        if to_remove:
            merged_any = True
            for j in sorted(to_remove, reverse=True):
                results.pop(j)
            valid_indices = [i for i, t in enumerate(results) if t.get("data")]

        # 日志
        for i, j, pa, pb, rows in merges_this_round:
            print(f"  [auto-merge] P{pa}+P{pb}: 表格#{i}+#{j} → +{rows}行")

    if merge_count > 0:
        print(f"  [auto-merge] 共合并 {merge_count} 对断裂表格")

    return results


def _auto_clean_tables(results, progress_callback=None):
    """对解析结果自动清洗：保存原始 → 清洗数值 → 删空格 → 删空行 → 合并断裂。"""
    import copy

    # results 中的 data 字段是纯 Python list[list[str]]，
    # deepcopy 不涉及 PyMuPDF C 对象，无需 gc.collect()

    if progress_callback:
        progress_callback(93, "正在自动清洗数据...")

    total_cleaned_cells = 0
    total_removed_spaces = 0

    for i, table in enumerate(results):
        if not table.get("data"):
            continue

        # 1. 深拷贝原始数据
        table["original_data"] = copy.deepcopy(table["data"])
        table["is_cleaned"] = True

        # 2. 清洗数值（含中英文保护）
        prev_data = copy.deepcopy(table["data"])
        _clean_data_cells(table["data"])
        for r in range(len(prev_data)):
            for c in range(min(len(prev_data[r]), len(table["data"][r]))):
                if prev_data[r][c] != table["data"][r][c]:
                    total_cleaned_cells += 1

        # 3. 智能删除空格（仅两端空单元格）
        prev_data = copy.deepcopy(table["data"])
        cleaned = _remove_spaces_data(table["data"])
        table["data"] = cleaned
        for r in range(len(prev_data)):
            for c in range(min(len(prev_data[r]), len(cleaned[r]))):
                if prev_data[r][c] != cleaned[r][c]:
                    total_removed_spaces += 1

        # 4. 删除全空行（不影响含省略标记 -- 的行）
        row_before = len(table["data"])
        _remove_empty_rows(table["data"])
        row_after = len(table["data"])
        if row_before != row_after:
            print(f"  [清洗] 表格{i+1} 删除空行: {row_before}→{row_after}行")

        # 4.5 删除因合并单元格展开导致的完全重复相邻列
        table["data"], dup_cols = _deduplicate_columns(table["data"])
        if dup_cols > 0:
            print(f"  [清洗] 表格{i+1} 列去重: 删除 {dup_cols} 个重复列")

        # 5. 删除行内空单元格（紧凑化，打标记 cells_compacted）
        data_before = sum(len(row) for row in table["data"])
        table["data"], compacted = _compact_empty_cells(table["data"])
        if compacted:
            data_after = sum(len(row) for row in table["data"])
            table["cells_compacted"] = True
            print(f"  [清洗] 表格{i+1} 紧凑化: {data_before}→{data_after}个单元格")

        # 5.5 清除 pdf2docx 合并格渗透的假数据
        table["data"], bleed_count = _clean_merged_cell_bleed(table["data"])
        if bleed_count > 0:
            print(f"  [清洗] 表格{i+1} 清除合并格渗透: {bleed_count}行")

        # 5.6 拆分垂直合并格导致的拼接值列
        table["data"], split_count = _split_concatenated_column(table["data"])
        if split_count > 0:
            print(f"  [清洗] 表格{i+1} 拆分拼接列: {split_count}列")

    # 6. 自动合并断裂表格
    results = _auto_merge_split_tables(results)

    if progress_callback:
        progress_callback(94, f"清洗完成({len(results)}个表格, {total_cleaned_cells}个单元格已清洗)")

    return results


# ============================================================
# 工作线程：PDF处理
# ============================================================
class ProcessingWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    warning = pyqtSignal(str)  # 添加警告信号

    def __init__(self, pdf_path, mode="auto", max_pages=100):
        super().__init__()
        self.pdf_path = pdf_path
        self.mode = mode
        self.max_pages = max_pages
        self.pdf_processor = PDFProcessor()
        self.llm = VisionLLM()

    def run(self):
        context = None
        try:
            self.progress.emit(5, "正在检测PDF类型...")

            # ---- 创建 PDF 共享上下文（一次打开，全流程复用） ----
            print(f"  [Worker] 创建 PDFContext: {self.pdf_path}")
            context = PDFContext(self.pdf_path)
            total_pages = context.page_count
            if self.max_pages:
                total_pages = min(self.max_pages, total_pages)

            is_image = self.pdf_processor.is_image_pdf(context=context)

            results = []
            failed_pages = set()  # 用 set 防止 auto 降级时重复记录同一页
            image_cache_dir = ""
            image_paths = []

            if self.mode == "text_only" or self.mode == "auto":
                # 进度回调 lambda
                cb = lambda v, m: self.progress.emit(v, m)

                if self.mode == "auto" and not is_image:
                    # ---- auto 模式：逐页 pdf2word (页码100%准确) ----
                    self.progress.emit(20, "pdf2word 逐页提取表格...")
                    docx_tables = self.pdf_processor._extract_tables_via_docx_per_page(
                        pdf_path=self.pdf_path,
                        context=context,
                        progress_callback=cb
                    )
                    v2_tables = []  # auto 模式不跑完整 V2，docx 已覆盖
                else:
                    # ---- text_only 模式：纯 V2 ----
                    self.progress.emit(20, "V2提取表格...")
                    v2_tables = self.pdf_processor.extract_text_tables(
                        pdf_path=self.pdf_path,
                        max_pages=self.max_pages,
                        context=context,
                        progress_callback=cb
                    )
                    docx_tables = []

                # ---- 去重合并：docx 优先，V2 补漏 ----
                self.progress.emit(50, "正在去重合并表格...")
                merged_tables = self.pdf_processor._deduplicate_v2_docx(
                    v2_tables, docx_tables
                )
                # 质量过滤：去掉单行/无数字的无效表格
                merged_tables = self.pdf_processor._filter_table_quality(merged_tables)

                for t in merged_tables:
                    ext = t.get("extractor", "v2")
                    data = t.get("data", [])

                    # 自动纠错：修复无框表格的行列错位
                    if data and len(data) >= 2:
                        corrected = self.pdf_processor.TableAutoCorrector.correct(data)
                        if corrected and len(corrected) != len(data):
                            print(f"  [自动纠错] P{t.get('page')}: {len(data)}行→{len(corrected)}行")
                            data = corrected
                            t["data"] = data
                            t["rows"] = len(corrected)

                    is_docx = ext.startswith("docx")
                    entry = {
                        "page": t["page"],
                        "type": "text",
                        "data": data,
                        "extractor": ext,
                        "parse_status": "success" if data else "failed",
                        "parse_message": "docx通道提取" if (is_docx and data) else ("V2提取" if data else "未检测到表格")
                    }
                    if t.get("context_text"):
                        entry["context_text"] = t["context_text"]
                    results.append(entry)

                extracted_pages = {t["page"] for t in merged_tables}
                for page_num in range(1, total_pages + 1):
                    if page_num not in extracted_pages:
                        failed_pages.add(page_num)

                if self.mode == "auto" and is_image and not v2_tables:
                    print(f"  [auto模式] 文本提取未找到表格，降级到图片处理...")

            if self.mode == "ai_only" or (self.mode == "auto" and is_image and not results):
                # 图片型PDF处理（ai_only 模式，或 auto 模式文本提取失败后的降级）
                self.progress.emit(20, "正在转换为图片...")

                # 使用 context 生成 LLM 图片
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                llm_image_dir = Path(TEMP_DIR) / f"pdf_images_{timestamp}"
                image_paths = context.generate_all_llm_images(llm_image_dir)
                image_cache_dir = str(llm_image_dir)

                if not self.llm.api_key:
                    # 没有API Key时，给每页生成空数据
                    self.warning.emit("未配置API Key，已将PDF转为图片缓存，每页生成空数据")
                    for img_path in image_paths:
                        page_num = int(os.path.basename(img_path).split('_')[1].split('.')[0])
                        results.append({
                            "page": page_num,
                            "type": "ai",
                            "data": [],
                            "parse_status": "empty",
                            "parse_message": "未配置API Key（空数据）"
                        })
                    results.sort(key=lambda x: x["page"])
                    self.progress.emit(95, "正在整理数据...")

                    # 生成预览图
                    from codes.pdf_extractor.utils import get_pdf_preview_dir
                    preview_dir = get_pdf_preview_dir(self.pdf_path)
                    context.generate_all_previews(preview_dir)

                    context.close()
                    self.finished.emit({
                        "success": True,
                        "is_image_pdf": True,
                        "image_cache_dir": image_cache_dir,
                        "tables": results,
                        "total_tables": len(results),
                        "success_count": 0,
                        "empty_count": len(results),
                        "failed_count": len(results),
                        "total_pages": total_pages
                    })
                    return

                self.progress.emit(40, "正在调用AI识别表格...")
                llm_results = self.llm.batch_recognize(
                    image_paths,
                    progress_callback=lambda x, y: self.progress.emit(
                        40 + int(x / y * 50),
                        f"正在识别第 {x}/{y} 页..."
                    )
                )

                successful_pages = set()
                for res in llm_results:
                    if res["result"]["success"]:
                        table_data = ExcelExporter.parse_json_table(res["result"]["data"])
                        if table_data:
                            results.append({
                                "page": res["page"],
                                "type": "ai",
                                "data": table_data,
                                "parse_status": "success",
                                "parse_message": "AI识别成功"
                            })
                            successful_pages.add(res["page"])
                        else:
                            results.append({
                                "page": res["page"],
                                "type": "ai",
                                "data": [],
                                "parse_status": "failed",
                                "parse_message": "AI返回数据解析失败"
                            })
                            successful_pages.add(res["page"])
                    else:
                        results.append({
                            "page": res["page"],
                            "type": "ai",
                            "data": [],
                            "parse_status": "failed",
                            "parse_message": res["result"].get("error", "AI识别失败")
                        })
                        successful_pages.add(res["page"])

                for page_num in range(1, total_pages + 1):
                    if page_num not in successful_pages:
                        failed_pages.add(page_num)

            for page_num in sorted(failed_pages):
                results.append({
                    "page": page_num,
                    "type": "failed",
                    "data": [],
                    "parse_status": "failed",
                    "parse_message": "未提取到表格数据"
                })

            # 按页码排序
            results.sort(key=lambda x: x["page"])

            self.progress.emit(95, "正在整理数据...")

            # 获取图片缓存目录（如果之前没有设置）
            if not image_cache_dir and image_paths:
                image_cache_dir = str(Path(image_paths[0]).parent) if image_paths else ""

            # ---- 统一生成预览图到磁盘（UI 层复用） ----
            from codes.pdf_extractor.utils import get_pdf_preview_dir
            preview_dir = get_pdf_preview_dir(self.pdf_path)
            need_preview = True
            if os.path.isdir(preview_dir):
                cached = [f for f in os.listdir(preview_dir) if f.startswith("preview_")]
                if cached:
                    need_preview = False
            if need_preview:
                self.progress.emit(96, "正在生成预览图...")
                context.generate_all_previews(preview_dir)

            # ---- 自动清洗数据 + 合并断裂表格 ----
            results = _auto_clean_tables(results, progress_callback=lambda v, m: self.progress.emit(v, m))
            # 最终确保按页码排序
            results.sort(key=lambda x: x["page"])

            self.progress.emit(98, "处理完成")

            self.finished.emit({
                "success": True,
                "is_image_pdf": is_image,
                "image_cache_dir": image_cache_dir,
                "tables": results,
                "total_tables": len(results),
                "success_count": len([r for r in results if r.get("parse_status") == "success"]),
                "failed_count": len([r for r in results if r.get("parse_status") == "failed"]),
                "total_pages": total_pages
            })

        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            if context:
                context.close()
