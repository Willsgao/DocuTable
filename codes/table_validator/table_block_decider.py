# -*- coding: utf-8 -*-
"""
集中分块决策器（TableBlockDecider）—— 架构修复 #2

硬伤诊断：
  表格分块决策曾分散在旧 liteparse 分割链 → processor；
  现由 Table Engine + TableBlockDecider 承接。
  形成"先合并再修补"的长链，每一步引入误差累积。

解决方案：
  TableBlockDecider 是"分块"成为一等公民的入口。
  输入：liteparse items + PDF 线条矢量 + PageLayoutModel
  输出：清晰的 [{y0, y1, type, confidence}] 列表

核心原则：
  1. 默认不合并（conservative splitting）
  2. 仅在强多信号证据下合并
  3. 分块决策与内容提取解耦
  4. 不确定的区域标记 needs_review，不再猜测

设计约束：
  - 不替代 Table Engine 主建表路径
  - 作为新的决策层包裹现有能力
  - 后续逐步将 segmenter 内的分块逻辑迁移到此模块
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from codes.table_validator.page_layout_model import PageLayoutModel


class BlockType(Enum):
    """块类型"""
    TABLE = "table"
    TEXT = "text"
    UNCERTAIN = "uncertain"  # 需要人工确认


@dataclass
class TableBlock:
    """单个表格块的决策结果。

    Attributes:
        y0, y1: 块的 Y 坐标范围
        block_type: table / text / uncertain
        confidence: 0.0 ~ 1.0 置信度
        text_items: 该块包含的 liteparse text_items
        page: 页码
        evidence: 判定依据列表
    """
    y0: float
    y1: float
    block_type: BlockType
    confidence: float
    text_items: List[dict] = field(default_factory=list)
    page: int = 0
    caption: Optional[str] = None
    evidence: List[str] = field(default_factory=list)

    @property
    def is_table(self) -> bool:
        return self.block_type == BlockType.TABLE

    @property
    def is_text(self) -> bool:
        return self.block_type == BlockType.TEXT

    @property
    def needs_review(self) -> bool:
        return self.block_type == BlockType.UNCERTAIN

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def to_dict(self) -> dict:
        return {
            "y0": self.y0,
            "y1": self.y1,
            "type": self.block_type.value,
            "confidence": self.confidence,
            "page": self.page,
            "caption": self.caption,
            "item_count": len(self.text_items),
            "evidence": self.evidence[:5],  # 前5条
        }


# ==================================================================
# MergeSignal: 多信号合并决策框架
# ==================================================================

@dataclass
class MergeSignal:
    """合并决策信号 — 评估两个相邻块是否应该合并。

    每项信号独立打分，综合得分决定是否合并。
    """

    # 独立信号
    y_gap_small: float = 0.0          # Y 间隙是否小（0~1）
    column_structure_match: float = 0.0  # 列结构是否匹配
    font_consistency: float = 0.0     # 字体大小一致性
    no_paragraph_between: float = 0.0   # 中间是否有段落
    common_header: float = 0.0        # 是否共享表头结构

    # 矢量线硬证据
    has_separator_line: bool = False    # 之间有横线

    @property
    def score(self) -> float:
        """综合得分 (0~5)"""
        return (
            self.y_gap_small
            + self.column_structure_match
            + self.font_consistency
            + self.no_paragraph_between
            + self.common_header
        )

    @property
    def should_merge(self) -> bool:
        """是否应该合并。

        合并条件（所有必须满足）：
        - 综合得分 >= 3.5
        - 没有分隔横线
        - 至少有列结构匹配或字体匹配（说明是同一张表的不同部分）
        """
        if self.has_separator_line:
            return False
        if self.score < 3.5:
            return False
        # 必须有至少一个"强信号"
        if self.column_structure_match < 0.8 or self.font_consistency < 0.5:
            return False
        return True

    @property
    def needs_review(self) -> bool:
        """是否需要人工复核。得分 2.5~3.5 且无分隔线。"""
        return not self.has_separator_line and 2.5 <= self.score < 3.5


# ==================================================================
# TableBlockDecider — 集中分块决策器
# ==================================================================

class TableBlockDecider:
    """集中分块决策器。

    用法：
        decider = TableBlockDecider(layout_model)
        blocks = decider.decide_blocks(
            text_items, regions, drawings, page_num
        )

    决策流程：
      1. Phase 0: 线条矢量硬分割（最高优先级）
      2. Phase 1: liteparse region 初步分块
      3. Phase 2: 多信号合并判定（每次合并需强证据）
      4. Phase 3: 不确定块标记 needs_review
    """

    def __init__(self, layout_model: PageLayoutModel):
        self.layout = layout_model
        # 配置
        self.MERGE_MIN_SCORE = 3.5
        self.UNCERTAIN_MIN_SCORE = 2.5
        self.UNCERTAIN_MAX_SCORE = 3.5

    # ── 公共入口 ───────────────────────────────────────────────

    def decide_blocks(
        self,
        text_items: List[dict],
        regions: Optional[List[dict]] = None,
        drawings: Optional[List[dict]] = None,
        page_num: int = 0,
    ) -> List[TableBlock]:
        """主决策入口。

        Args:
            text_items: 页面的 liteparse text_items
            regions: liteparse 内置的 table_regions（可选）
            drawings: PyMuPDF get_drawings() 的线条矢量（可选）
            page_num: 页码

        Returns:
            [{y0, y1, type, confidence}, ...] 按 Y 从上到下排列
        """
        if not text_items:
            return []

        # Step 0: 线条矢量硬分割
        line_splits = self._find_line_boundaries(drawings or [])

        # Step 1: 用 liteparse regions + line_splits 初步分块
        initial_blocks = self._initial_split(
            text_items, regions or [], line_splits, page_num
        )

        # Step 2: 多信号合并判定（核心：保守策略）
        merged_blocks = self._conservative_merge(initial_blocks)

        # Step 3: 分类每个块的类型
        classified = self._classify_blocks(merged_blocks)

        return classified

    # ── Phase 0: 线条矢量硬分割 ──────────────────────────────────

    def _find_line_boundaries(self, drawings: List[dict]) -> List[float]:
        """从 PDF 线条矢量中提取表格边界 Y 坐标。

        横向长线条（> 页面宽度 40%）是可依赖的表格分隔信号。
        竖线条确认横线条所在的区域是表格而非装饰线。

        Returns:
            排序后的 Y 坐标列表（用于硬分割）
        """
        horizontal_lines = []  # [(y, x0, x1, length)]
        vertical_lines = []    # [(x, y0, y1)]

        for d in drawings:
            rect = d.get("rect")
            if not rect or len(rect) < 4:
                continue
            x0, y0, x1, y1 = rect
            w = x1 - x0
            h = y1 - y0

            if h < 3 and w > self.layout.page_width * 0.4:
                horizontal_lines.append((y0, x0, x1, w))
            elif w < 3 and h > self.layout.median_row_height * 0.5:
                vertical_lines.append((x0, y0, y1))

        if not horizontal_lines:
            return []

        # 合并邻近横线（< 3pt 视同一条）
        horizontal_lines.sort()
        merged = []
        for y, x0, x1, w in horizontal_lines:
            if merged and abs(y - merged[-1]) < 3.0:
                continue
            merged.append(y)

        # 检查横线附近是否有竖线（确认为表格线而非装饰线）
        if vertical_lines:
            confirmed = []
            for y in merged:
                y_range = range(
                    int(y - self.layout.median_row_height * 2),
                    int(y + self.layout.median_row_height * 2),
                )
                has_verticals = any(
                    int(vy0) in y_range or int(vy1) in y_range
                    for _, vy0, vy1 in vertical_lines
                )
                if has_verticals:
                    confirmed.append(y)
            return confirmed

        return merged

    # ── Phase 1: 初步分块 ───────────────────────────────────────

    def _initial_split(
        self,
        text_items: List[dict],
        regions: List[dict],
        line_splits: List[float],
        page_num: int,
    ) -> List[TableBlock]:
        """用 liteparse regions + 线条分割点 做初步分块。

        策略：保守切分 — 每一个可能的边界都先切开，留待 Phase 2 合并。
        """
        if not text_items:
            return []

        sorted_items = sorted(text_items, key=lambda it: (it.get("y_mid", 0), it.get("x0", 0)))

        # 收集所有候选分割点
        split_points: List[float] = []

        # 来源 1: 线条矢量硬分割
        split_points.extend(line_splits)

        # 来源 2: liteparse region 边界
        for region in regions:
            y0 = region.get("y0", 0)
            y1 = region.get("y1", 0)
            if y0 > 0:
                split_points.append(y0)
            if y1 > 0:
                split_points.append(y1)

        # 来源 3: 大文本间隙（> big_gap_threshold）
        y_mids = [it.get("y_mid", 0) for it in sorted_items]
        for i in range(1, len(y_mids)):
            gap = y_mids[i] - y_mids[i - 1]
            if gap >= self.layout.big_gap_threshold:
                split_points.append((y_mids[i - 1] + y_mids[i]) / 2)

        # 去重并排序
        split_points = sorted(set(
            sp for sp in split_points
            if sp > sorted_items[0].get("y0", 0)
            and sp < sorted_items[-1].get("y1", 0)
        ))

        if not split_points:
            # 没有分割点 → 整页一个块
            return [self._make_block(sorted_items, page_num, evidence=["no_split_points"])]

        # 按分割点切片
        blocks = []
        start_y = sorted_items[0].get("y0", 0)
        for sp in split_points:
            segment_items = [
                it for it in sorted_items
                if start_y <= it.get("y_mid", 0) < sp
            ]
            if segment_items:
                blocks.append(self._make_block(
                    segment_items, page_num,
                    evidence=[f"split_at_{sp:.1f}"]
                ))
            start_y = sp

        # 最后一段
        tail_items = [
            it for it in sorted_items
            if it.get("y_mid", 0) >= start_y
        ]
        if tail_items:
            blocks.append(self._make_block(
                tail_items, page_num,
                evidence=["tail_segment"]
            ))

        return blocks

    def _make_block(
        self, items: List[dict], page_num: int, evidence: List[str]
    ) -> TableBlock:
        """从一组 text_items 创建一个 TableBlock。"""
        if not items:
            return TableBlock(
                y0=0, y1=0, block_type=BlockType.TEXT,
                confidence=0.0, page=page_num, evidence=evidence,
            )

        y0 = min(it.get("y0", float("inf")) for it in items)
        y1 = max(it.get("y1", 0) for it in items)

        return TableBlock(
            y0=y0, y1=y1,
            block_type=BlockType.UNCERTAIN,  # Phase 3 才分类
            confidence=0.5,
            text_items=items,
            page=page_num,
            evidence=evidence,
        )

    # ── Phase 2: 保守合并 ───────────────────────────────────────

    def _conservative_merge(
        self, blocks: List[TableBlock]
    ) -> List[TableBlock]:
        """保守合并策略：只在强证据下合并相邻块。

        与旧架构的根本区别：
          旧架构：先合并，错了再拆（_merge_regions_by_proximity → _split_fused_table_by_structure）
          新架构：默认不合并，只在多信号通过时才合并

        合并信号（5 项，每项 0~1 分）：
          1. Y 间隙小（0~1，间隙 < 行高 = 1，间隙 > 3× 行高 = 0）
          2. 列结构匹配（Jaccard 相似度）
          3. 字体大小一致
          4. 中间无段落文本
          5. 共享表头结构（上块有表头但下块没有）
        """
        if len(blocks) <= 1:
            return blocks

        merged = [blocks[0]]

        for i in range(1, len(blocks)):
            prev = merged[-1]
            curr = blocks[i]

            signal = self._eval_merge_signal(prev, curr)

            if signal.should_merge:
                # 强证据 → 合并
                merged[-1] = TableBlock(
                    y0=prev.y0,
                    y1=curr.y1,
                    block_type=BlockType.UNCERTAIN,
                    confidence=prev.confidence * 0.95,  # 合并降低置信度
                    text_items=prev.text_items + curr.text_items,
                    page=prev.page,
                    evidence=prev.evidence + [f"merged_with_block_{i}"],
                )
            else:
                merged.append(curr)

        return merged

    def _eval_merge_signal(
        self, block_a: TableBlock, block_b: TableBlock
    ) -> MergeSignal:
        """评估两个相邻块的合并信号。"""
        signal = MergeSignal()

        # 1. Y 间隙评分
        gap = block_b.y0 - block_a.y1
        if gap <= 0:  # 重叠
            signal.y_gap_small = 1.0
        elif gap <= self.layout.median_row_height:
            signal.y_gap_small = 1.0
        elif gap <= self.layout.median_row_height * 2:
            signal.y_gap_small = 0.8
        elif gap <= self.layout.median_row_height * 3:
            signal.y_gap_small = 0.5
        elif gap <= self.layout.median_row_height * 5:
            signal.y_gap_small = 0.2
        else:
            signal.y_gap_small = 0.0

        # 2. 列结构匹配
        cols_a = self._extract_column_positions(block_a.text_items)
        cols_b = self._extract_column_positions(block_b.text_items)
        signal.column_structure_match = self._jaccard_columns(cols_a, cols_b)

        # 3. 字体大小一致性
        signal.font_consistency = self._font_size_consistency(
            block_a.text_items, block_b.text_items
        )

        # 4. 中间是否有段落文本
        signal.no_paragraph_between = float(
            not self._has_paragraph_between(block_a, block_b)
        )

        # 5. 共享表头
        signal.common_header = float(
            self._has_header_continuation(block_a, block_b)
        )

        # 6. 硬证据：分隔横线
        if block_a.y1 > 0 and block_b.y0 > 0:
            mid_y = (block_a.y1 + block_b.y0) / 2
            signal.has_separator_line = self._has_horizontal_line_at(mid_y)

        return signal

    # ── 辅助：列位置提取 ──────────────────────────────────────

    def _extract_column_positions(
        self, items: List[dict], tolerance: Optional[float] = None
    ) -> List[float]:
        """从 text_items 提取列 X 中心位置。"""
        if not items:
            return []

        tol = tolerance or self.layout.column_x_tolerance
        x_centers = []
        for it in items:
            cx = (it.get("x0", 0) + it.get("x1", 0)) / 2
            x_centers.append(cx)
        x_centers.sort()

        # 用 tol 聚类
        clusters = []
        for xc in x_centers:
            merged = False
            for cl in clusters:
                if abs(xc - sum(cl) / len(cl)) <= tol:
                    cl.append(xc)
                    merged = True
                    break
            if not merged:
                clusters.append([xc])

        return [sum(cl) / len(cl) for cl in clusters]

    def _jaccard_columns(
        self, cols_a: List[float], cols_b: List[float], tolerance: float = 15.0
    ) -> float:
        """计算两套列位置的重叠程度（Jaccard 相似度，带容差）。"""
        if not cols_a or not cols_b:
            return 0.0

        matched_a = set()
        matched_b = set()

        for i, ca in enumerate(cols_a):
            for j, cb in enumerate(cols_b):
                if abs(ca - cb) <= tolerance:
                    matched_a.add(i)
                    matched_b.add(j)

        intersection = len(matched_a)  # = len(matched_b) because symmetric
        union = len(set(range(len(cols_a))) | set(range(len(cols_b))))
        if union == 0:
            return 0.0
        return intersection / union

    def _font_size_consistency(
        self, items_a: List[dict], items_b: List[dict]
    ) -> float:
        """评估两组 items 的字体大小一致性。"""
        sizes_a = [
            it.get("font_size") or it.get("size", 0)
            for it in items_a
            if (it.get("font_size") or it.get("size", 0)) > 0
        ]
        sizes_b = [
            it.get("font_size") or it.get("size", 0)
            for it in items_b
            if (it.get("font_size") or it.get("size", 0)) > 0
        ]

        if not sizes_a or not sizes_b:
            return 0.5  # 中性

        median_a = self._median(sizes_a)
        median_b = self._median(sizes_b)

        if median_a <= 0 or median_b <= 0:
            return 0.5

        ratio = min(median_a, median_b) / max(median_a, median_b)
        return ratio  # 越接近 1 越一致

    def _has_paragraph_between(
        self, block_a: TableBlock, block_b: TableBlock
    ) -> bool:
        """判断两个块之间是否有段落文本（不应合并）。"""
        gap = block_b.y0 - block_a.y1
        if gap <= 0:
            return False
        if gap < self.layout.median_row_height * 0.5:
            return False

        # 检查间隙中是否有长文本行（X 覆盖率低的行 = 段落）
        between_items = [
            it for it in block_a.text_items + block_b.text_items
            if block_a.y1 <= it.get("y_mid", 0) <= block_b.y0
        ]
        for it in between_items:
            x_span = it.get("x1", 0) - it.get("x0", 0)
            if x_span > self.layout.page_width * 0.6:
                return True

        return False

    def _has_header_continuation(
        self, block_a: TableBlock, block_b: TableBlock
    ) -> bool:
        """判断下块是否为上块的延续（无独立表头）。"""
        items_b = block_b.text_items
        if len(items_b) < 3:
            return True  # 小块默认可能是延续

        # 简单检查：下块前3行是否像表头
        sorted_b = sorted(items_b, key=lambda it: it.get("y_mid", 0))
        first_items = sorted_b[: min(10, len(sorted_b))]

        # 检测数值占比
        numeric_count = 0
        total = 0
        for it in first_items:
            text = str(it.get("text", "")).strip()
            if not text:
                continue
            total += 1
            if self._looks_numeric(text):
                numeric_count += 1

        if total == 0:
            return True

        # 如果数值占比 < 30% → 像表头 → 可能是新表
        # 如果数值占比 ≥ 30% → 像数据行 → 可能是延续
        return (numeric_count / total) >= 0.30

    def _has_horizontal_line_at(self, y: float) -> bool:
        """检查 y 坐标附近是否有水平分隔线。"""
        if not hasattr(self.layout, "_horizontal_lines"):
            return False
        for hy, *_ in self.layout._horizontal_lines:
            if abs(hy - y) < self.layout.median_row_height:
                return True
        return False

    # ── Phase 3: 块分类 ────────────────────────────────────────

    def _classify_blocks(self, blocks: List[TableBlock]) -> List[TableBlock]:
        """将每个块分类为 table / text / uncertain。

        分类规则：
          - 含多列（≥2）+ 中高数值占比 → table
          - 单列 + 长文本 → text
          - 边缘情况 → uncertain
        """
        for block in blocks:
            items = block.text_items
            if not items:
                block.block_type = BlockType.TEXT
                block.confidence = 0.3
                block.evidence.append("empty_block")
                continue

            # 提取列结构
            col_positions = self._extract_column_positions(items)
            col_count = len(col_positions)

            # 数值占比统计
            total_items = len(items)
            numeric_items = sum(
                1 for it in items
                if self._looks_numeric(str(it.get("text", "")))
            )
            numeric_ratio = numeric_items / total_items if total_items > 0 else 0

            # 段落文本特征
            full_width_count = sum(
                1 for it in items
                if (it.get("x1", 0) - it.get("x0", 0))
                > self.layout.page_width * self.layout.paragraph_x_coverage_threshold
            )
            full_width_ratio = full_width_count / total_items if total_items > 0 else 0

            # 文本长度中位数
            text_lengths = [
                len(str(it.get("text", "")))
                for it in items
            ]
            median_text_len = self._median(text_lengths) if text_lengths else 0

            # ═══ 分类决策 ═══
            if col_count >= 3 and numeric_ratio >= 0.20:
                # 多列 + 有一定数值 → 高置信度表格
                block.block_type = BlockType.TABLE
                block.confidence = min(0.85 + numeric_ratio * 0.15, 0.98)
                block.evidence.append(
                    f"multi_col({col_count})_numeric({numeric_ratio:.2f})"
                )

            elif col_count >= 2 and numeric_ratio >= 0.35:
                # 双列 + 高数值 → 表格
                block.block_type = BlockType.TABLE
                block.confidence = 0.80 + numeric_ratio * 0.10
                block.evidence.append(
                    f"dual_col_numeric({numeric_ratio:.2f})"
                )

            elif col_count <= 1 and full_width_ratio > 0.5:
                # 单列 + 全宽文本 → 段落
                block.block_type = BlockType.TEXT
                block.confidence = 0.75 + full_width_ratio * 0.15
                block.evidence.append(
                    f"single_col_paragraph(fw={full_width_ratio:.2f})"
                )

            elif total_items < 3:
                # 项目太少 → 不确定
                block.block_type = BlockType.UNCERTAIN
                block.confidence = 0.40
                block.evidence.append("too_few_items")

            elif col_count >= 2 and numeric_ratio < 0.15 and median_text_len > 20:
                # 多列但都是长文本 → 可能是目录/段落
                block.block_type = BlockType.TEXT
                block.confidence = 0.55
                block.evidence.append("multi_col_long_text")

            else:
                # 边缘情况 → 不确定，标记 needs_review
                block.block_type = BlockType.UNCERTAIN
                block.confidence = 0.45
                block.evidence.append(
                    f"ambiguous(cols={col_count},num={numeric_ratio:.2f},text_len={median_text_len:.0f})"
                )

        return blocks

    # ── 工具函数 ──────────────────────────────────────────────

    @staticmethod
    def _looks_numeric(text: str) -> bool:
        """检测字符串是否像数值。"""
        text = text.strip()
        if not text:
            return False
        # 去除常见格式字符
        cleaned = text.replace(",", "").replace(" ", "").replace("%", "")
        cleaned = re.sub(r"^[\(（\-]", "", cleaned)
        cleaned = re.sub(r"[\)）]$", "", cleaned)
        try:
            float(cleaned)
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _median(values: List[float]) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        if n % 2 == 1:
            return sorted_vals[n // 2]
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0


# ==================================================================
# 便捷函数
# ==================================================================

def decide_table_blocks(
    text_items: List[dict],
    regions: Optional[List[dict]] = None,
    drawings: Optional[List[dict]] = None,
    page_num: int = 0,
) -> Tuple[List[TableBlock], PageLayoutModel]:
    """便捷函数：一键完成分块决策。

    Returns:
        (blocks, layout_model) — 分块结果和对应的布局模型
    """
    layout = PageLayoutModel.from_text_items(text_items, page_num, drawings)
    decider = TableBlockDecider(layout)
    blocks = decider.decide_blocks(text_items, regions, drawings, page_num)
    return blocks, layout
