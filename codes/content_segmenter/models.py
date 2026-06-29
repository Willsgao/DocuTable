# -*- coding: utf-8 -*-
"""
Content Segmenter — 数据模型
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


# ============================================================
# 区域类型枚举
# ============================================================

class RegionType:
    TABLE = "table"
    PARAGRAPH = "paragraph"
    UNKNOWN = "unknown"


# ============================================================
# 段落区域
# ============================================================

@dataclass
class ParagraphRegion:
    """页面上一个被识别为段落文本的区域。"""
    x0: float
    y0: float
    x1: float
    y1: float
    text: str = ""                          # 段落全文
    line_count: int = 0                     # 行数
    avg_x_coverage: float = 0.0             # 平均 x 覆盖率（0~1）
    confidence: float = 0.0                 # 段落置信度 0~1

    def to_dict(self) -> dict:
        return {
            "x0": round(self.x0, 2),
            "y0": round(self.y0, 2),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
            "text": self.text,
            "line_count": self.line_count,
            "avg_x_coverage": round(self.avg_x_coverage, 4),
            "confidence": round(self.confidence, 4),
        }


# ============================================================
# 通用区域（分割结果）
# ============================================================

@dataclass
class SegmentRegion:
    """内容分割后的一个独立区域（可能是表格也可能是段落）。"""
    x0: float
    y0: float
    x1: float
    y1: float
    region_type: str = RegionType.UNKNOWN   # "table" | "paragraph"
    text_items: List[Any] = field(default_factory=list)  # 区域内的 TextItem/word 列表
    text: str = ""                          # 区域内文本拼接
    confidence: float = 0.0
    # 诊断信息（用于数据对比记录）
    diagnosis: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_table(self) -> bool:
        return self.region_type == RegionType.TABLE

    @property
    def is_paragraph(self) -> bool:
        return self.region_type == RegionType.PARAGRAPH

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def to_dict(self) -> dict:
        return {
            "x0": round(self.x0, 2),
            "y0": round(self.y0, 2),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
            "region_type": self.region_type,
            "text": self.text[:200] + "..." if len(self.text) > 200 else self.text,
            "item_count": len(self.text_items),
            "confidence": round(self.confidence, 4),
            "diagnosis": self.diagnosis,
        }


# ============================================================
# 单页分割结果
# ============================================================

@dataclass
class SegmentResult:
    """单页内容分割的完整结果。"""
    page_number: int
    page_width: float
    page_height: float
    regions: List[SegmentRegion] = field(default_factory=list)
    segment_time_ms: float = 0.0

    @property
    def table_regions(self) -> List[SegmentRegion]:
        return [r for r in self.regions if r.is_table]

    @property
    def paragraph_regions(self) -> List[SegmentRegion]:
        return [r for r in self.regions if r.is_paragraph]

    @property
    def region_count(self) -> int:
        return len(self.regions)

    def to_dict(self) -> dict:
        return {
            "page_number": self.page_number,
            "page_width": round(self.page_width, 2),
            "page_height": round(self.page_height, 2),
            "total_regions": len(self.regions),
            "table_regions": len(self.table_regions),
            "paragraph_regions": len(self.paragraph_regions),
            "segment_time_ms": round(self.segment_time_ms, 2),
            "regions": [r.to_dict() for r in self.regions],
        }
