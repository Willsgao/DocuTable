# -*- coding: utf-8 -*-
"""
Segment Logger — 优化前后数据对比记录

在优化过程中，自动记录每次运行的分割数据，方便核查优化效果：
1. 优化前：记录原始密度法的区域检测结果
2. 优化后：记录 ContentSegmenter 的分割结果
3. 对比摘要：自动生成前后差异报告

存储结构:
    files/optimization_logs/
    ├── v1_before/          # 优化前原始数据
    │   └── <pdf_name>/
    │       ├── page_001.json
    │       └── page_002.json
    ├── v2_after/           # 优化后数据
    │   └── <pdf_name>/
    │       ├── page_001.json
    │       └── page_002.json
    └── diff_reports/       # 对比报告
        └── <pdf_name>_diff.json
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


# ============================================================
# 日志根目录
# ============================================================

LOG_ROOT = Path(__file__).resolve().parents[2] / "files" / "optimization_logs"
BEFORE_DIR = LOG_ROOT / "v1_before"
AFTER_DIR = LOG_ROOT / "v2_after"
DIFF_DIR = LOG_ROOT / "diff_reports"


class SegmentLogger:
    """分割数据记录器。"""

    # ================================================================
    # 公开 API
    # ================================================================

    @classmethod
    def ensure_dirs(cls):
        """确保日志目录存在。"""
        for d in [BEFORE_DIR, AFTER_DIR, DIFF_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def log_before(
        cls,
        pdf_name: str,
        page_number: int,
        merged_regions: List[Dict[str, Any]],
        page_size: Dict[str, float] = None,
        extra_info: Dict[str, Any] = None,
    ):
        """记录优化前的原始区域检测结果。

        Args:
            pdf_name: PDF 文件名（不含扩展名）
            page_number: 页码
            merged_regions: 原始密度法合并后的区域列表
            page_size: {"width": float, "height": float}
            extra_info: 额外信息（如 density_grid、row_classifications 等）
        """
        cls.ensure_dirs()

        pdf_dir = BEFORE_DIR / pdf_name
        pdf_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "pdf_name": pdf_name,
            "page_number": page_number,
            "timestamp": datetime.now().isoformat(),
            "method": "density_grid_merge",  # 优化前使用密度网格合并
            "page_size": page_size or {},
            "region_count": len(merged_regions),
            "regions": merged_regions,
            "extra": extra_info or {},
        }

        filepath = pdf_dir / f"page_{page_number:03d}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        return str(filepath)

    @classmethod
    def log_after(
        cls,
        pdf_name: str,
        page_number: int,
        segment_result,  # SegmentResult
        original_density_regions: List[Dict[str, Any]] = None,
        page_size: Dict[str, float] = None,
        extra_info: Dict[str, Any] = None,
    ):
        """记录优化后的内容分割结果。

        Args:
            pdf_name: PDF 文件名（不含扩展名）
            page_number: 页码
            segment_result: ContentSegmenter 分割后的 SegmentResult
            original_density_regions: 原始密度法检测的区域（用于对比）
            page_size: {"width": float, "height": float}
            extra_info: 额外信息
        """
        cls.ensure_dirs()

        pdf_dir = AFTER_DIR / pdf_name
        pdf_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "pdf_name": pdf_name,
            "page_number": page_number,
            "timestamp": datetime.now().isoformat(),
            "method": "content_segmenter",
            "page_size": page_size or {},
            "total_regions": segment_result.region_count,
            "table_regions": len(segment_result.table_regions),
            "paragraph_regions": len(segment_result.paragraph_regions),
            "segment_time_ms": round(segment_result.segment_time_ms, 2),
            "regions": [r.to_dict() for r in segment_result.regions],
            "extra": extra_info or {},
        }

        filepath = pdf_dir / f"page_{page_number:03d}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        return str(filepath)

    @classmethod
    def log_page_diff(
        cls,
        pdf_name: str,
        page_number: int,
        before_regions: List[Dict[str, Any]],
        after_segment,  # SegmentResult
        page_size: Dict[str, float] = None,
    ):
        """自动生成单页前后对比报告。

        Returns:
            对比摘要 dict
        """
        cls.ensure_dirs()

        before_count = len(before_regions)
        after_table_count = len(after_segment.table_regions)
        after_paragraph_count = len(after_segment.paragraph_regions)
        after_total = after_segment.region_count

        # 判断关键变化类型
        changes = []
        if before_count == 1 and after_total > 1:
            changes.append(f"单区域被拆分为 {after_total} 个子区域（表段分离）")
        if after_paragraph_count > 0:
            changes.append(f"新识别出 {after_paragraph_count} 个段落区域")
        if after_table_count < before_count:
            changes.append(f"表格区域从 {before_count} 减少到 {after_table_count}")
        if after_table_count == before_count and after_paragraph_count == 0:
            changes.append("无变化（该页无段落混入表格区域）")

        diff = {
            "pdf_name": pdf_name,
            "page_number": page_number,
            "timestamp": datetime.now().isoformat(),
            "page_size": page_size or {},
            "summary": {
                "before_regions": before_count,
                "after_total_regions": after_total,
                "after_table_regions": after_table_count,
                "after_paragraph_regions": after_paragraph_count,
                "region_count_delta": after_total - before_count,
                "paragraphs_newly_found": after_paragraph_count,
                "changes": changes,
            },
            "before": {
                "region_count": before_count,
                "regions": before_regions,
            },
            "after": after_segment.to_dict(),
        }

        # 保存 diff 文件
        diff_dir = DIFF_DIR / pdf_name
        diff_dir.mkdir(parents=True, exist_ok=True)
        filepath = diff_dir / f"page_{page_number:03d}_diff.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(diff, f, ensure_ascii=False, indent=2)

        return diff

    @classmethod
    def generate_summary_report(
        cls,
        pdf_name: str,
        total_pages: int,
        pages_with_changes: int,
        total_paragraphs_found: int,
        details: List[Dict[str, Any]] = None,
    ) -> str:
        """生成整体优化效果摘要报告（文本格式）。

        Returns:
            可读的文本报告
        """
        lines = [
            "=" * 60,
            f"DocuTable 内容分割优化 — 效果对比报告",
            "=" * 60,
            f"PDF 文件: {pdf_name}",
            f"总页数: {total_pages}",
            f"生成时间: {datetime.now().isoformat()}",
            "",
            "-" * 40,
            "整体指标",
            "-" * 40,
            f"有变化的页面: {pages_with_changes} / {total_pages}",
            f"新识别段落区域: {total_paragraphs_found} 个",
            "",
        ]

        if details:
            lines.append("-" * 40)
            lines.append("逐页详情")
            lines.append("-" * 40)
            for d in details:
                lines.append(
                    f"  第 {d['page']} 页: "
                    f"前 {d['before_count']} 区域 → "
                    f"后 {d['after_total']} 区域 "
                    f"(表格 {d['after_tables']}, 段落 {d['after_paragraphs']})"
                )
                if d.get('changes'):
                    for c in d['changes']:
                        lines.append(f"    → {c}")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    @classmethod
    def load_before_log(cls, pdf_name: str, page_number: int) -> Optional[Dict]:
        """加载优化前某页的记录。"""
        filepath = BEFORE_DIR / pdf_name / f"page_{page_number:03d}.json"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    @classmethod
    def load_after_log(cls, pdf_name: str, page_number: int) -> Optional[Dict]:
        """加载优化后某页的记录。"""
        filepath = AFTER_DIR / pdf_name / f"page_{page_number:03d}.json"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    @classmethod
    def list_logged_pdfs(cls, version: str = "v2_after") -> List[str]:
        """列出已记录日志的 PDF 名称。

        Args:
            version: "v1_before" | "v2_after"
        """
        root = AFTER_DIR if version == "v2_after" else BEFORE_DIR
        if not root.exists():
            return []
        return sorted([
            d.name for d in root.iterdir() if d.is_dir()
        ])
