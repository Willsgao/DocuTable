# -*- coding: utf-8 -*-
"""V2 Steps 共享数据结构"""

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any


# ============================================================
# 步骤间传递的核心数据结构
# ============================================================

@dataclass
class GridResult:
    """Step 1 输出：网格化结果"""
    table_data: List[List[str]] = field(default_factory=list)      # 2D 网格
    row_bounds: List[Tuple[float, float]] = field(default_factory=list)  # [(y_top, y_bottom), ...]
    col_bounds: List[float] = field(default_factory=list)          # [x0, x1, x2, ...]
    drawings: List[dict] = field(default_factory=list)             # 原始线条
    confidence: float = 0.0                                        # 网格置信度 0~1


@dataclass
class MergeSpan:
    """合并单元格描述（Step 2 输出）"""
    row: int
    col: int
    rowspan: int
    colspan: int
    confidence: float = 0.0
    source: str = "unknown"  # "lines" | "text"


@dataclass
class ClassifyResult:
    """Step 3 输出：分类结果"""
    is_table: bool
    confidence: float           # 0~1
    needs_review: bool = False  # 需人工复核
    reasons: List[str] = field(default_factory=list)
    scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class TextItem:
    """Step 5/6 输出：统一文本项（三通道归一化）"""
    text: str
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    page: int = 0
    source: str = ""            # "pymupdf" | "liteparse" | "pdfplumber" | "paddleocr"
    confidence: float = 0.0     # 提取置信度 0~1
    font_size: float = 0.0      # 字号（用于表头/数据行区分）
    is_bold: bool = False       # 是否粗体（用于表头识别）
    block_type: str = ""        # "text" | "table_cell" | "header" | "footer"
    # 注意：block_type 暂由下游推断，此处保留字段供后续使用


@dataclass
class PipelineContext:
    """Pipeline 上下文，在步骤间逐页传递

    每页处理时创建新实例，步骤通过 ctx 读/写各自的产物字段。
    """
    pdf_path: str = ""
    page_num: int = 0
    page: Any = None            # PyMuPDF page 对象
    page_rect: Any = None       # page.rect
    words: List[dict] = field(default_factory=list)
    drawings: List[dict] = field(default_factory=list)

    # Step 1 产出
    grid: Optional[GridResult] = None

    # Step 2 产出（参考用）
    merge_spans: List[MergeSpan] = field(default_factory=list)

    # Step 3 产出
    classify_result: Optional[ClassifyResult] = None

    # Step 5/6 产出
    text_items: List[TextItem] = field(default_factory=list)

    # 通用元数据（各步骤可写入）
    metadata: Dict[str, Any] = field(default_factory=dict)

    # 兼容旧代码：原始 table_data（Step 1 填充后可用）
    table_data: List[List[str]] = field(default_factory=list)
