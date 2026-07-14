# -*- coding: utf-8 -*-
"""Table Engine 核心数据模型。

硬性约定：pipeline 建表/split 阶段使用 StructuredTable / Cell / SourceItem，
禁止以 List[List[str]] 作为中间态（export 层除外）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


@dataclass(frozen=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def union(self, other: "BBox") -> "BBox":
        return BBox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


@dataclass
class SourceItem:
    """liteparse 词级片段（pipeline 输入单元）。"""

    text: str
    bbox: BBox
    page: int
    item_index: str
    y_mid: float = 0.0
    font_size: float = 0.0
    font_name: str = ""
    merged_from: List[str] = field(default_factory=list)

    @property
    def x0(self) -> float:
        return self.bbox.x0

    @property
    def y0(self) -> float:
        return self.bbox.y0

    @property
    def x1(self) -> float:
        return self.bbox.x1

    @property
    def y1(self) -> float:
        return self.bbox.y1


@dataclass
class RegionBox:
    """表格/段落区域 bbox（仅几何 + 元数据，不用 region_text 重建）。"""

    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = 0.0
    region_index: int = 0


@dataclass
class PageSource:
    """单页 liteparse 输入（标准化后）。"""

    page_number: int
    page_width: float
    page_height: float
    items: List[SourceItem]
    table_regions: List[RegionBox] = field(default_factory=list)
    is_table_page: bool = False
    error: Optional[str] = None


@dataclass
class LiteparseDocument:
    """整份 PDF 的 liteparse 缓存视图。"""

    pdf_path: str
    total_pages: int
    pages: List[PageSource]
    parse_time_sec: float = 0.0
    error: Optional[str] = None
    _page_index: Dict[int, PageSource] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._page_index = {p.page_number: p for p in self.pages}

    def get_page(self, page_num: int) -> Optional[PageSource]:
        return self._page_index.get(page_num)


@dataclass
class ColumnRange:
    x0: float
    x1: float
    col_index: int
    role: str = ""


@dataclass
class ColumnGrid:
    ranges: List[ColumnRange]
    layout_id: str
    confidence: float = 1.0

    @property
    def col_count(self) -> int:
        return len(self.ranges)


@dataclass
class Cell:
    text: str
    bbox: BBox
    row: int
    col: int
    source_items: List[str]


@dataclass
class RowCluster:
    """Y 聚类结果（分列前）。"""

    y_mid: float
    y0: float
    y1: float
    items: List[SourceItem]


@dataclass
class StructuredTable:
    page: int
    pages: List[int]
    y0: float
    y1: float
    x0: float
    x1: float
    rows: List[List[Optional[Cell]]]
    grid: ColumnGrid
    layout_id: str = ""
    caption: str = ""
    description_text: str = ""
    notes: str = ""
    metadata: dict = field(default_factory=dict)

    def iter_rows_dense(self) -> List[List[str]]:
        """export 用：稀疏 Cell 矩阵 →  dense 字符串行。"""
        out: List[List[str]] = []
        ncol = self.grid.col_count or max(
            (len(row) for row in self.rows), default=0
        )
        for row in self.rows:
            cells = [""] * ncol
            for j, cell in enumerate(row):
                if cell is None:
                    continue
                if j < ncol:
                    cells[j] = cell.text
            out.append(cells)
        return out


@dataclass
class TextBlock:
    page: int
    y0: float
    y1: float
    text: str
    source_items: List[str] = field(default_factory=list)


@dataclass
class DocumentEntry:
    kind: Literal["table", "text"]
    page: int
    y0: float
    y1: float
    table: Optional[StructuredTable] = None
    text_block: Optional[TextBlock] = None
    entry_id: int = 0


@dataclass
class BuildReport:
    pages_processed: int = 0
    tables_built: int = 0
    text_blocks: int = 0
    warnings: List[str] = field(default_factory=list)
    fallback_count: int = 0


@dataclass
class Document:
    entries: List[DocumentEntry]
    source_pdf: str
    parse_kind: Literal["native", "ocr"] = "native"
    build_report: BuildReport = field(default_factory=BuildReport)
