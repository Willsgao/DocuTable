# -*- coding: utf-8 -*-
"""Table Engine 配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


# 第三支柱报告默认缓存（回归测试）
DEFAULT_PILLAR_CACHE = Path(
    "data/mid_cache/2025-03-29-601939_SH-建设银行-601939建设银行2024年度资本管理第三支柱信息披露报告"
    "/liteparse/pages.json"
)


@dataclass
class TableEngineConfig:
    """全局配置；Step 0 仅使用 cache 路径相关项。"""

    # --- Step 0+ ---
    pages_json_path: Path | None = None

    # --- Step 1+ 几何 ---
    row_cluster_y_tolerance: float = 3.0
    row_cluster_dynamic: bool = True

    # --- Step 3+ scope ---
    scope_y_margin_above: float = 10.0
    scope_y_margin_below: float = 30.0

    # --- Step 2+ layout ---
    layout_score_threshold: float = 0.45

    # --- Step 4+ split ---
    geometry_text_split_min_gap_ratio: float = 1.5
    structure_split_grid_jaccard: float = 0.5

    # --- OCR（Step 8）---
    ocr_backend: str = "stub"

    extra: dict = field(default_factory=dict)


def default_config() -> TableEngineConfig:
    return TableEngineConfig()
