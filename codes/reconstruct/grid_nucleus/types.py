# -*- coding: utf-8 -*-
"""凝结核网格恢复：数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class Nucleus:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    row_id: int = -1
    col_id: int = -1
    flags: Set[str] = field(default_factory=set)
    source_ids: Set[str] = field(default_factory=set)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    def anchor_x(self, *, amount_like: bool = False) -> float:
        """兼容旧接口；落列应以 [x0,x1] 双边为准，默认返回中点。"""
        return (self.x0 + self.x1) / 2.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["flags"] = sorted(self.flags)
        d["source_ids"] = sorted(self.source_ids)
        d["cx"] = self.cx
        d["cy"] = self.cy
        return d


@dataclass
class RowCluster:
    row_id: int
    cy: float
    nuclei: List[Nucleus] = field(default_factory=list)
    is_abnormal: bool = False
    role: str = "unknown"  # body|header|title|abnormal|unknown

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_id": self.row_id,
            "cy": self.cy,
            "n_nuclei": len(self.nuclei),
            "is_abnormal": self.is_abnormal,
            "role": self.role,
        }


@dataclass
class ColumnBand:
    col_id: int
    left: float
    right: float
    align: str = "left"  # left|right|center
    weak: bool = False

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GridResult:
    ok: bool = False
    n_rows: int = 0
    n_cols: int = 0
    row_lines: List[float] = field(default_factory=list)
    col_lines: List[float] = field(default_factory=list)
    data: List[List[str]] = field(default_factory=list)
    method: str = "none"
    errors: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    rows_meta: List[Dict[str, Any]] = field(default_factory=list)
    columns_meta: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "row_lines": list(self.row_lines),
            "col_lines": list(self.col_lines),
            "data": self.data,
            "method": self.method,
            "errors": list(self.errors),
            "metrics": dict(self.metrics),
            "rows_meta": list(self.rows_meta),
            "columns_meta": list(self.columns_meta),
        }
