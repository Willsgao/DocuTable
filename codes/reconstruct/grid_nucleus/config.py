# -*- coding: utf-8 -*-
"""凝结核网格恢复：配置。"""

from __future__ import annotations

from typing import Any, Dict

# 默认开启算法诊断；写回 data 需 ok+守恒（见 pipeline）
GRID_NUCLEUS: Dict[str, Any] = {
    "enabled": True,
    "allow_overwrite_data": True,
    "row_gap_factor": 0.65,
    "col_gap_factor": 0.6,
    "abnormal_count_ratio": 0.5,
    "wide_factor": 1.8,
    "cross_eps_pt": 5.0,
    "min_gap_pt": 1.0,
    "cover_thresh": 0.90,
    "min_body_rows": 3,
    "max_cols": 20,
    "min_cols": 2,
    "cross_ratio_fallback": 0.4,
}
