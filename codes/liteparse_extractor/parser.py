# -*- coding: utf-8 -*-
"""
LiteParse Extractor — 主编排器

职责：
1. 编排完整解析流水线：分页提取 → 表格区域检测 → 缓存
2. 对外提供统一入口：LiteParseParser
3. 默认解析 PDF 全部页（受 max_pages 约束）；亦可按 target_pages 指定子集
"""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional

from .config import LITEPARSE_CONFIG
from .models import ParseResult, PageResult
from .page_processor import PageProcessor
from .region_detector import RegionDetector
from . import cache_manager as cache

logger = logging.getLogger(__name__)


class LiteParseParser:
    """liteparse 解析主编排器。

    用法:
        parser = LiteParseParser()
        result = parser.parse("report.pdf")

        # 只解析指定页码
        result = parser.parse("report.pdf", target_pages=[5, 7, 12])

        # 从缓存加载
        result = parser.load_or_parse("report.pdf")
    """

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or LITEPARSE_CONFIG
        self.page_processor = PageProcessor(self.cfg)
        self.region_detector = RegionDetector(self.cfg)

    def _resolve_target_pages(
        self,
        total_pages: int,
        target_pages: Optional[List[int]],
    ) -> List[int]:
        """计算本次应解析的页码列表（1-based，去重排序）。"""
        if target_pages is not None:
            return sorted(set(target_pages))
        cap = min(total_pages, self.cfg.get("max_pages", 500))
        return list(range(1, cap + 1))

    # ================================================================
    # 公开接口
    # ================================================================

    def parse(
        self,
        pdf_path: str,
        target_pages: Optional[List[int]] = None,
        detect_regions: bool = True,
        force: bool = False,
    ) -> ParseResult:
        """解析 PDF，返回标准化的 ParseResult。

        Args:
            pdf_path:       PDF 文件路径
            target_pages:   要解析的页码列表（1-based），None = 全部
            detect_regions: 是否检测表格区域
            force:          是否强制重新解析（忽略缓存）

        Returns:
            ParseResult（按页组织）

        Raises:
            FileNotFoundError: PDF 文件不存在
            RuntimeError:      liteparse 不可用
        """
        if not os.path.isfile(pdf_path):
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

        total_pages = self._get_page_count(pdf_path)
        expected_pages = self._resolve_target_pages(total_pages, target_pages)

        # 尝试加载缓存（须覆盖本次请求的全部页码）
        if not force and cache.is_cache_valid(pdf_path):
            cached = cache.load_parse_result(pdf_path)
            if cached is not None:
                cached_nums = {p.page_number for p in cached.pages}
                if set(expected_pages) <= cached_nums:
                    logger.info(f"从缓存加载 liteparse 结果: {pdf_path}")
                    return cached
                missing = sorted(set(expected_pages) - cached_nums)
                logger.info(
                    f"liteparse 缓存缺 {len(missing)} 页 {missing[:8]}"
                    f"{'...' if len(missing) > 8 else ''}，重新解析"
                )

        t_start = time.time()

        # 逐页解析
        logger.info(
            f"liteparse 开始解析: {pdf_path} "
            f"(总 {total_pages} 页, 目标 {len(expected_pages)} 页)"
        )
        pages = self.page_processor.process_all_pages(
            pdf_path, total_pages, expected_pages
        )

        # 表格区域检测
        if detect_regions:
            logger.info("检测表格区域...")
            pages = self.region_detector.detect_all(pages)
            n_table = sum(1 for p in pages if p.is_table_page)
            logger.info(f"表格区域检测完成: {n_table} / {len(pages)} 页包含表格")

        # 补充 liteparse 未返回的页（保证请求页码完整）
        parsed_nums = {p.page_number for p in pages}
        for pn in expected_pages:
            if pn not in parsed_nums:
                pages.append(PageResult(
                    page_number=pn,
                    page_width=0, page_height=0,
                    error="liteparse 未返回此页数据",
                ))

        # 组装结果
        pages.sort(key=lambda p: p.page_number)
        result = ParseResult(
            pdf_path=pdf_path,
            total_pages=total_pages,
            pages=pages,
            parse_time_sec=round(time.time() - t_start, 2),
        )

        # 保存中间数据
        cache_dir = cache.save_parse_result(result)
        logger.info(
            f"liteparse 解析完成: "
            f"{result.page_count_with_table}/{result.total_pages} 页含表格, "
            f"耗时 {result.parse_time_sec:.1f}s, "
            f"缓存 → {cache_dir}"
        )

        return result

    def load_or_parse(
        self,
        pdf_path: str,
        target_pages: Optional[List[int]] = None,
        detect_regions: bool = True,
    ) -> ParseResult:
        """优先从缓存加载，缓存失效则重新解析。"""
        return self.parse(
            pdf_path, target_pages, detect_regions, force=False
        )

    def parse_table_pages_only(
        self,
        pdf_path: str,
        table_page_numbers: List[int],
    ) -> ParseResult:
        """只解析指定页码子集（兼容旧接口）。

        主流程已改为全页解析；差分对比等场景仍可传入页码子集。
        """
        if not table_page_numbers:
            logger.warning("table_page_numbers 为空，跳过 liteparse 解析")
            return ParseResult(
                pdf_path=pdf_path,
                total_pages=self._get_page_count(pdf_path),
            )
        return self.parse(pdf_path, target_pages=sorted(table_page_numbers))

    # ================================================================
    # 工具
    # ================================================================

    @staticmethod
    def _get_page_count(pdf_path: str) -> int:
        """获取 PDF 总页数（优先用 PyMuPDF，降级用 pdfplumber）。"""
        try:
            import fitz
            doc = fitz.open(pdf_path)
            count = doc.page_count
            doc.close()
            return count
        except Exception:
            pass
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                return len(pdf.pages)
        except Exception:
            pass
        return 0

    @staticmethod
    def is_available() -> bool:
        """检查 liteparse 是否可用。"""
        from .page_processor import _check_liteparse_python, _check_liteparse_cli
        return _check_liteparse_python() or _check_liteparse_cli()
