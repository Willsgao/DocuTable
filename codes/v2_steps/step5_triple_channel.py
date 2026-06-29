# -*- coding: utf-8 -*-
"""
Step 5: 并行三通道文本提取

将串行 fallback 改为并行：PyMuPDF || pdfplumber || liteparse
三通道并行提取 → 融合去重 → 增强的词列表，喂给 Step1 列切分。

通道：
- PyMuPDF words: 坐标最精细，主力通道
- pdfplumber words: 补充遗漏文本，表格检测强
- liteparse TextItem: 布局感知最强，可选（需 liteparse 依赖）

设计原则：
- 每通道输出统一的 word dict 格式 {"text", "x0", "y0", "x1", "y1"}
- 融合策略：PyMuPDF 为主，pdfplumber/liteparse 补充不重叠的文本
- 并行用 ThreadPoolExecutor（I/O 密集型）
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any, Tuple


# ============================================================
# 统一 word 格式
# ============================================================

def _make_word(text: str, x0: float, y0: float, x1: float, y1: float,
               source: str = "") -> Dict[str, Any]:
    """构造标准化 word 字典"""
    return {
        "text": str(text).strip(),
        "x0": float(x0), "y0": float(y0),
        "x1": float(x1), "y1": float(y1),
        "source": source,
    }


# ============================================================
# 通道 1: PyMuPDF words
# ============================================================

class PyMuPDFChannel:
    """PyMuPDF 词提取通道 — 坐标最精细，作为主力通道"""

    @staticmethod
    def extract(page) -> List[Dict[str, Any]]:
        """从 PyMuPDF page 提取 words

        Args:
            page: fitz.Page 对象

        Returns:
            标准化 word 列表
        """
        words = []
        try:
            raw = page.get_text("words")
            for w in raw:
                words.append(_make_word(
                    text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3],
                    source="pymupdf",
                ))
        except Exception:
            # 回退：dict 模式
            try:
                from codes.pdf_extractor.processor import PDFProcessor
                raw = PDFProcessor._extract_words_from_dict(page)
                for w in raw:
                    words.append(_make_word(
                        text=w["text"], x0=w["x0"], y0=w["y0"],
                        x1=w["x1"], y1=w["y1"], source="pymupdf",
                    ))
            except Exception:
                pass
        return words


# ============================================================
# 通道 2: pdfplumber words
# ============================================================

class PdfPlumberChannel:
    """pdfplumber 词提取通道 — 补充遗漏文本，表格检测强"""

    @staticmethod
    def extract(pdf_path: str, page_num: int) -> List[Dict[str, Any]]:
        """从 pdfplumber 提取 words

        Args:
            pdf_path: PDF 文件路径
            page_num: 0-based 页码

        Returns:
            标准化 word 列表
        """
        words = []
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                if page_num < len(pdf.pages):
                    p = pdf.pages[page_num]
                    raw = p.extract_words(
                        keep_blank_chars=False,
                        use_text_flow=False,
                    )
                    for w in raw:
                        words.append(_make_word(
                            text=w.get("text", ""),
                            x0=w.get("x0", 0),
                            y0=w.get("top", 0),
                            x1=w.get("x1", 0),
                            y1=w.get("bottom", 0),
                            source="pdfplumber",
                        ))
        except ImportError:
            pass  # pdfplumber 未安装，静默跳过
        except Exception as e:
            pass  # 单页失败不阻塞

        return words


# ============================================================
# 通道 3: liteparse TextItem（可选）
# ============================================================

class LiteParseChannel:
    """liteparse 文本提取通道 — 布局感知最强

    注意：liteparse 需要批量处理（一次打开 PDF 处理多页），与逐页 Pipeline
    模式不完全匹配。此通道主要用于批量预处理场景。
    """

    @staticmethod
    def extract(pdf_path: str, page_nums: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """批量从 liteparse 提取 TextItem

        Args:
            pdf_path: PDF 文件路径
            page_nums: 1-based 页码列表

        Returns:
            {1-based page_num: [标准化 word 列表]}
        """
        result: Dict[int, List[Dict]] = {}
        try:
            from codes.liteparse_extractor import LiteParseParser
            parser = LiteParseParser()
            parse_result = parser.parse(pdf_path, target_pages=page_nums)

            for page_result in parse_result.pages:
                pn = page_result.page_number
                words = []
                for item in page_result.text_items:
                    words.append(_make_word(
                        text=item.text,
                        x0=item.x0, y0=item.y0,
                        x1=item.x1, y1=item.y1,
                        source="liteparse",
                    ))
                result[pn] = words
        except ImportError:
            pass
        except Exception:
            pass

        return result


# ============================================================
# 融合器：多通道 word 合并去重
# ============================================================

class TextFuser:
    """多通道文本融合器

    策略：PyMuPDF 为主坐标源，pdfplumber/liteparse 补充不重叠的文本。
    """

    # 重叠判定阈值
    OVERLAP_IOU_MIN = 0.3       # IoU ≥ 此值视为同一 word
    OVERLAP_AREA_MIN = 0.5      # 面积重叠率 ≥ 此值视为同一 word

    @staticmethod
    def _compute_iou(a: Dict, b: Dict) -> float:
        """计算两个 word bbox 的 IoU"""
        x_left = max(a["x0"], b["x0"])
        y_top = max(a["y0"], b["y0"])
        x_right = min(a["x1"], b["x1"])
        y_bottom = min(a["y1"], b["y1"])

        if x_left >= x_right or y_top >= y_bottom:
            return 0.0

        inter = (x_right - x_left) * (y_bottom - y_top)
        area_a = max((a["x1"] - a["x0"]) * (a["y1"] - a["y0"]), 0.001)
        area_b = max((b["x1"] - b["x0"]) * (b["y1"] - b["y0"]), 0.001)

        # 使用较小面积作为分母，更敏感地检测包含关系
        return inter / min(area_a, area_b)

    @classmethod
    def fuse(cls,
             primary: List[Dict[str, Any]],
             supplementary: List[Dict[str, Any]],
             primary_label: str = "pymupdf",
             ) -> List[Dict[str, Any]]:
        """融合主通道和补充通道的 words

        Args:
            primary: 主通道 words（PyMuPDF）
            supplementary: 补充通道 words（pdfplumber 或 liteparse）
            primary_label: 主通道标签（用于过滤）

        Returns:
            融合后的 word 列表（保留主通道全部 + 补充通道不重叠部分）
        """
        if not supplementary:
            return list(primary)

        if not primary:
            return list(supplementary)

        result = list(primary)  # 主通道全部保留
        added = 0

        for sw in supplementary:
            text = sw["text"]
            if not text:
                continue

            # 检查是否与已有 word 重叠
            is_duplicate = False
            for pw in primary:
                iou = cls._compute_iou(sw, pw)
                if iou >= cls.OVERLAP_IOU_MIN:
                    is_duplicate = True
                    break

                # 额外检查：文本完全相同且 bbox 接近
                if pw["text"] == text:
                    dx = abs(sw["x0"] - pw["x0"])
                    dy = abs(sw["y0"] - pw["y0"])
                    if dx < 5.0 and dy < 3.0:
                        is_duplicate = True
                        break

            if not is_duplicate:
                result.append(sw)
                added += 1

        return result

    @classmethod
    def fuse_multi(cls,
                   channels: Dict[str, List[Dict[str, Any]]],
                   primary_key: str = "pymupdf",
                   ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """多通道融合

        Args:
            channels: {"pymupdf": [...], "pdfplumber": [...], ...}
            primary_key: 主通道 key

        Returns:
            (融合后 words, {"pymupdf": N, "pdfplumber_added": M, ...})
        """
        primary_words = channels.get(primary_key, [])
        merged = list(primary_words)

        stats = {primary_key: len(primary_words)}

        for key, words in channels.items():
            if key == primary_key or not words:
                continue
            before = len(merged)
            merged = cls.fuse(merged, words, primary_label=primary_key)
            stats[f"{key}_added"] = len(merged) - before

        stats["total"] = len(merged)
        return merged, stats


# ============================================================
# 并行提取器
# ============================================================

class Step5TripleChannel:
    """并行三通道提取器（V2 Step 5）

    使用方式：
        extractor = Step5TripleChannel(cfg)
        enhanced_words = extractor.extract_page(
            pdf_path="doc.pdf", page_num=0, page=pymupdf_page)
    """

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or {}
        self.enable_pymupdf = self.cfg.get("enable_pymupdf", True)
        self.enable_pdfplumber = self.cfg.get("enable_pdfplumber", True)
        self.enable_liteparse = self.cfg.get("enable_liteparse", False)
        self.parallel_workers = self.cfg.get("parallel_workers", 2)
        self.fusion_strategy = self.cfg.get("fusion_strategy", "pymupdf_primary")

    def extract_page(self,
                     pdf_path: str,
                     page_num: int,
                     page: Any = None,
                     ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """并行提取单页文本

        Args:
            pdf_path: PDF 路径
            page_num: 0-based 页码
            page: PyMuPDF page 对象（可选，有则用）

        Returns:
            (融合后的 words, 统计信息 dict)
        """
        channels: Dict[str, List[Dict]] = {}
        t0 = time.time()

        # 需要并行的任务
        tasks = []

        # PyMuPDF 通道（如有 page 对象则同步执行，最快）
        if self.enable_pymupdf and page is not None:
            channels["pymupdf"] = PyMuPDFChannel.extract(page)

        # pdfplumber 通道
        if self.enable_pdfplumber:
            tasks.append(("pdfplumber", pdf_path, page_num))

        # liteparse 通道（可选，当前逐页调用效率低，默认关闭）
        if self.enable_liteparse:
            tasks.append(("liteparse", pdf_path, [page_num + 1]))

        # 并行执行
        if tasks and self.parallel_workers > 1:
            with ThreadPoolExecutor(max_workers=min(len(tasks), self.parallel_workers)) as executor:
                futures = {}
                for task in tasks:
                    channel_name = task[0]
                    if channel_name == "pdfplumber":
                        future = executor.submit(
                            PdfPlumberChannel.extract, task[1], task[2])
                        futures[future] = channel_name
                    elif channel_name == "liteparse":
                        future = executor.submit(
                            LiteParseChannel.extract, task[1], task[2])
                        futures[future] = channel_name

                for future in as_completed(futures):
                    channel_name = futures[future]
                    try:
                        result = future.result(timeout=30)
                        if channel_name == "liteparse":
                            # liteparse 返回 {page_num: [words]}
                            page_words = result.get(page_num + 1, [])
                            if page_words:
                                channels[channel_name] = page_words
                        else:
                            if result:
                                channels[channel_name] = result
                    except Exception:
                        pass
        elif tasks:
            # 单线程回退
            for task in tasks:
                channel_name = task[0]
                try:
                    if channel_name == "pdfplumber":
                        result = PdfPlumberChannel.extract(task[1], task[2])
                        if result:
                            channels[channel_name] = result
                    elif channel_name == "liteparse":
                        result = LiteParseChannel.extract(task[1], task[2])
                        page_words = result.get(page_num + 1, [])
                        if page_words:
                            channels[channel_name] = page_words
                except Exception:
                    pass

        # 融合
        merged, stats = TextFuser.fuse_multi(channels, primary_key="pymupdf")
        stats["time_ms"] = round((time.time() - t0) * 1000, 1)
        stats["channels_used"] = list(channels.keys())

        return merged, stats
