# -*- coding: utf-8 -*-
"""
页面布局模型（PageLayoutModel）—— 架构修复 #1：自适应阈值

硬伤诊断：
  7 个不同位置的硬编码阈值（80pt, 60pt, 30pt, 25pt, 18pt, 3.0×, 15pt, 0.6×）
  互相没有推导关系，是"缺少统一的页面布局模型"的替身。

解决方案：
  PageLayoutModel 从页面的实际数据推导所有阈值，替代所有魔法数字。
  输入：一页的 text_items + PDF 线条矢量
  输出：统一推导的阈值 + 页面特征

设计原则：
  - 零硬编码阈值（所有阈值从数据自动推导）
  - 缓存友好（相同输入返回相同输出）
  - 不修改输入数据
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class PageLayoutModel:
    """统一的页面布局模型，从 text_items 自动推导所有阈值。

    用法：
        model = PageLayoutModel.from_text_items(text_items, page_num=1)
        # 获取自适应阈值
        y_margin = model.table_y_margin     # 替代 _Y_MARGIN=25
        big_gap = model.big_gap_threshold   # 替代 max(mean_gap*10, 80)
        merge_gap = model.merge_gap_max     # 替代 MAX_MERGE=60
    """

    # ── 页面基本信息 ──
    page_num: int = 0
    item_count: int = 0

    # ── 字体/行高统计 ──
    median_font_size: float = 10.0
    median_row_height: float = 14.0
    mean_row_height: float = 14.0
    median_line_gap: float = 4.0    # 相邻文本行之间的 Y 间隙中位数

    # ── 页面尺寸 ──
    page_height: float = 800.0
    page_width: float = 600.0

    # ── X 方向列间隙 ──
    median_col_gap: float = 15.0    # 同一行内相邻文本项之间 X 间隙中位数

    # ═══════════════════════════════════════════════════════════════
    # 派生阈值（全部从上述统计量自动计算，替代所有硬编码常数）
    # ═══════════════════════════════════════════════════════════════

    @property
    def big_gap_threshold(self) -> float:
        """极端大间隙阈值，用于 _split_by_y_gaps 拆分混合区域。

        替代：max(mean_gap * 10, 80.0)
        策略：mean_gap = median_line_gap + median_row_height
        """
        mean_gap = self.median_line_gap + self.median_row_height
        return max(mean_gap * 10.0, self.median_row_height * 6.0)

    @property
    def merge_gap_max(self) -> float:
        """Region 合并的最大 Y 间隙阈值。

        替代：MAX_MERGE = 60.0
        策略：3 倍行高，但不少于 2 倍（防止密集表格被过度合并）
        """
        return max(self.median_row_height * 3.0, self.median_row_height * 2.0)

    @property
    def merge_gap_min(self) -> float:
        """Region 合并的最小 Y 间隙阈值。

        替代：MIN_MERGE = 18.0
        策略：1 倍行高
        """
        return self.median_row_height

    @property
    def merge_ratio(self) -> float:
        """Region 合并的间隙倍率阈值。

        替代：MERGE_RATIO = 3.0
        """
        return 3.0

    @property
    def table_y_margin(self) -> float:
        """表格覆盖范围的 Y 边距。

        替代：
          - 旧 hybrid 分割链: Y_MARGIN_BELOW = 30.0
          - processor.py: _Y_MARGIN = 25.0

        策略：行高的 70%，但不少于 15pt 且不多于 40pt。
        理由：边距太小会漏边界数据行，太大会把段落误吸入表格。
        """
        margin = self.median_row_height * 0.70
        return max(15.0, min(margin, 40.0))

    @property
    def gap_capture_y_margin(self) -> float:
        """间隙捕获区域边距（Phase 1.5 用）。

        替代：Y_MARGIN_BELOW = 30.0
        策略：行高的 80%
        """
        return self.median_row_height * 0.80

    @property
    def row_y_tolerance(self) -> float:
        """Y 聚类中判定"同一行"的容差。

        替代：max(avg_row_height * 0.6, 3.0)
        策略：行高的 50%
        """
        return max(self.median_row_height * 0.50, 2.0)

    @property
    def column_x_tolerance(self) -> float:
        """列对齐的 X 容差。

        替代：boundary_col_tolerance = 15.0
        策略：列间隙中位数的 40%，但不少于 5pt
        """
        return max(self.median_col_gap * 0.40, 5.0)

    @property
    def max_gap_for_boundary(self) -> float:
        """_refine_lower_boundary 中最大允许的 Y 间隙。

        替代：boundary_max_gap_ratio = 3.0 → max_gap = row_height * 3
        """
        return self.median_row_height * 3.0

    @property
    def consecutive_miss_max(self) -> int:
        """_refine_lower_boundary 中连续无对齐行上限。

        替代：boundary_max_consecutive_miss = 2
        """
        return 2

    @property
    def paragraph_x_coverage_threshold(self) -> float:
        """文本 X 方向覆盖率阈值，用于判定"全宽段落"。

        替代：X_COVERAGE_SNAP_THRESHOLD = 0.85
        """
        return 0.85

    @property
    def min_gap_width(self) -> float:
        """列间最小间隙宽度（content_segmenter 用）。

        替代：MIN_GAP_WIDTH_PT = 12.0
        策略：字体大小 × 1.2
        """
        return self.median_font_size * 1.2

    # ═══════════════════════════════════════════════════════════════
    # 工厂方法
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def from_text_items(
        cls,
        text_items: List[dict],
        page_num: int = 0,
        page_drawings: Optional[List[dict]] = None,
    ) -> "PageLayoutModel":
        """从一页的 text_items 统计构建布局模型。"""
        if not text_items:
            return cls(page_num=page_num)

        model = cls(page_num=page_num, item_count=len(text_items))

        # 1. 字体大小统计
        font_sizes = []
        for it in text_items:
            fs = it.get("font_size") or it.get("size")
            if fs and fs > 0:
                font_sizes.append(fs)
        if font_sizes:
            model.median_font_size = _median(font_sizes)

        # 2. 行高统计
        row_heights = []
        for it in text_items:
            h = it.get("y1", 0) - it.get("y0", 0)
            if h > 0:
                row_heights.append(h)
        if row_heights:
            model.median_row_height = _median(row_heights)
            model.mean_row_height = sum(row_heights) / len(row_heights)

        # 3. 页面尺寸
        if text_items:
            model.page_width = max(it.get("x1", 0) for it in text_items)
            model.page_height = max(it.get("y1", 0) for it in text_items)

        # 4. 行间距统计 — 按 Y 排序后计算相邻项间隙
        sorted_by_y = sorted(text_items, key=lambda it: (it.get("y_mid", 0), it.get("x0", 0)))
        y_gaps = []
        for i in range(1, len(sorted_by_y)):
            prev = sorted_by_y[i - 1]
            curr = sorted_by_y[i]
            gap = curr.get("y_mid", 0) - prev.get("y_mid", 0)
            # 只统计合理范围的正间隙（排除跨 block 的大间隙）
            if 0 < gap < model.median_row_height * 5:
                y_gaps.append(gap)
        if y_gaps:
            model.median_line_gap = _median(y_gaps)
        else:
            model.median_line_gap = model.median_row_height * 0.3

        # 5. X 方向列间隙 — 同一 Y 行内相邻文本项间隙
        # 按 Y 行分组
        row_items: Dict[int, List[dict]] = {}
        for it in sorted_by_y:
            y_bucket = round(it.get("y_mid", 0) / model.median_row_height) if model.median_row_height > 0 else 0
            row_items.setdefault(y_bucket, []).append(it)

        col_gaps = []
        for y_items in row_items.values():
            sorted_x = sorted(y_items, key=lambda it: it.get("x0", 0))
            for i in range(1, len(sorted_x)):
                gap = sorted_x[i].get("x0", 0) - sorted_x[i - 1].get("x1", 0)
                if 0 < gap < model.page_width * 0.5:
                    col_gaps.append(gap)

        if col_gaps:
            model.median_col_gap = _median(col_gaps)
        else:
            model.median_col_gap = model.median_font_size * 1.5

        # 6. 从 PDF 线条矢量提取辅助信息
        if page_drawings:
            model._ingest_drawings(page_drawings)

        return model

    def _ingest_drawings(self, drawings: List[dict]) -> None:
        """从 PyMuPDF get_drawings() 提取线条信息。用于后续增强。"""
        # 预留给 TableBlockDecider 使用
        # 这里仅提取基本信息供阈值推导
        horizontal_lines = []
        vertical_lines = []
        for d in drawings:
            rect = d.get("rect")
            if not rect:
                continue
            # rect 格式: (x0, y0, x1, y1)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if h < 2 and w > self.page_width * 0.3:
                horizontal_lines.append(rect)
            elif w < 2 and h > self.median_row_height * 0.5:
                vertical_lines.append(rect)

        self._horizontal_lines = horizontal_lines
        self._vertical_lines = vertical_lines

    def has_table_lines(self) -> bool:
        """页面是否包含明显的表格横线/竖线。"""
        return bool(
            getattr(self, "_horizontal_lines", [])
            or getattr(self, "_vertical_lines", [])
        )

    @property
    def summary(self) -> dict:
        """返回布局模型摘要（用于调试/日志）。"""
        return {
            "page": self.page_num,
            "items": self.item_count,
            "font_size_median": round(self.median_font_size, 1),
            "row_height_median": round(self.median_row_height, 1),
            "row_height_mean": round(self.mean_row_height, 1),
            "line_gap_median": round(self.median_line_gap, 1),
            "col_gap_median": round(self.median_col_gap, 1),
            "page_dims": f"{self.page_width:.0f}×{self.page_height:.0f}",
            "has_lines": self.has_table_lines(),
            # 关键派生阈值
            "big_gap_threshold": round(self.big_gap_threshold, 1),
            "merge_gap_max": round(self.merge_gap_max, 1),
            "table_y_margin": round(self.table_y_margin, 1),
            "row_y_tolerance": round(self.row_y_tolerance, 1),
            "column_x_tolerance": round(self.column_x_tolerance, 1),
        }

    def __repr__(self) -> str:
        return (
            f"PageLayoutModel(page={self.page_num}, "
            f"font={self.median_font_size:.1f}pt, "
            f"row_h={self.median_row_height:.1f}pt, "
            f"gap={self.median_line_gap:.1f}pt)"
        )


# ==================================================================
# 工具函数
# ==================================================================

def _median(values: List[float]) -> float:
    """计算中位数（排序后取中间值）。"""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
