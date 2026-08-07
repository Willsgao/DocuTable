# -*- coding: utf-8 -*-
"""凝结核法表格网格恢复（数字 PDF / liteparse 字框）。

**凝结核分割原则是第一位**（不可被质检/导出/跨格/启发式覆盖）。

1. **主** — 凝结核分割行列：同界必同列；同列公共边界；仅同行大空隙才拆列；
   列数以表体为准；顺序铁律（只平移/伸缩空白，不交换顺序）。
2. **次** — 跨格标注与表头回挂：只在已定网格上标记/填字，不得反向改列数。
"""

from codes.reconstruct.grid_nucleus.config import GRID_NUCLEUS
from codes.reconstruct.grid_nucleus.pipeline import apply_grid_to_table, restore_table_grid
from codes.reconstruct.grid_nucleus.types import GridResult

__all__ = [
    "GRID_NUCLEUS",
    "GridResult",
    "restore_table_grid",
    "apply_grid_to_table",
]
