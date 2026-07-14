# -*- coding: utf-8
"""Layout 插件基类与上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol, Tuple

from codes.table_engine.models import ColumnGrid, ColumnRange


@dataclass
class LayoutContext:
    """列模板推断输入。"""

    page: int
    scope_y0: float
    region_y0: float
    region_y1: float
    items: List[dict]
    rows: List[dict]


@dataclass
class LayoutSelection:
    layout_id: str
    col_ranges: List[Tuple[float, float]]
    confidence: float = 1.0
    roles: List[str] = field(default_factory=list)

    def to_column_grid(self) -> ColumnGrid:
        return ColumnGrid(
            ranges=[
                ColumnRange(
                    x0=a,
                    x1=b,
                    col_index=i,
                    role=self.roles[i] if i < len(self.roles) else "",
                )
                for i, (a, b) in enumerate(self.col_ranges)
            ],
            layout_id=self.layout_id,
            confidence=self.confidence,
        )


class LayoutPlugin(Protocol):
    layout_id: str

    def score(self, ctx: LayoutContext) -> float:
        """0~1，越高越匹配。"""

    def infer(self, ctx: LayoutContext) -> LayoutSelection | None:
        """推断列界；不匹配时返回 None。"""

    def col_index_for_item(
        self,
        x0: float,
        x1: float,
        text: str,
        col_ranges: List[Tuple[float, float]],
    ) -> int:
        """将 item 映射到列下标。"""
