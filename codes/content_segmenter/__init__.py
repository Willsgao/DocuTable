# -*- coding: utf-8 -*-
"""
Content Segmenter — 页面内容结构化分割模块

将页面上的文本块按空间分布模式分割为"段落区域"和"表格区域"。
核心思路：不是用密度阈值一刀切，而是分析每行文本的 x 方向离散度：
  - 表格行：列间有明显间隙（x 方向不连续）
  - 段落行：文本几乎覆盖整行（x 方向连续）

模块结构:
    models.py       — 数据模型 (ParagraphRegion, SegmentRegion, SegmentResult)
    segmenter.py    — 核心分割逻辑
    segment_logger.py — 优化前后数据对比记录
"""

from .models import ParagraphRegion, SegmentRegion, SegmentResult
from .segmenter import ContentSegmenter
from .segment_logger import SegmentLogger

__all__ = [
    "ParagraphRegion",
    "SegmentRegion",
    "SegmentResult",
    "ContentSegmenter",
    "SegmentLogger",
]
