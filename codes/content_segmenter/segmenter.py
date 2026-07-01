# -*- coding: utf-8 -*-
"""
Content Segmenter — 核心分割逻辑

基于 x 方向文本离散度识别表格行 vs 段落行，将大区域拆分为独立的
表格子区域和段落子区域。

算法流程:
  1. 输入: 一个矩形区域内的所有文本项（含 x0, y0, x1, y1, text）
  2. 按 y 坐标将文本项归为"行"
  3. 对每行计算 x 方向离散度指标
  4. 根据离散度判定该行是 "table_row" 还是 "paragraph_row"
  5. 合并连续同类型的行 → 输出子区域列表
"""

from __future__ import annotations

import time
from typing import List, Tuple, Dict, Any, Optional, Union

from .models import SegmentRegion, SegmentResult, RegionType


# ============================================================
# 配置常量
# ============================================================

class SegmenterConfig:
    """分割器配置参数"""

    # ---- 行归并 ----
    Y_TOLERANCE_PT: float = 6.0           # 两行文字的最大 y 偏差（pt），超过则算新行

    # ---- x 方向离散度检测 ----
    MIN_GAP_WIDTH_PT: float = 12.0         # 两个相邻词之间超过此间隙 → 列间 gap
                                            # 分类规则（硬编码在 _classify_row 中）：
                                            #   2+ gaps → 表格, 1 gap + 覆盖率<0.75 → 表格
                                            #   0 gaps → 段落, 覆盖率≥0.85 → 段落

    # ---- x 覆盖率 ----
    X_COVERAGE_SNAP_THRESHOLD: float = 0.85  # 文字在 x 方向覆盖超此比值 → 偏段落
    TWO_COL_TABLE_COVERAGE_MAX: float = 0.75 # 1 gap 时，覆盖率低于此值 → 双列表格

    # ---- 子区域合并 ----
    MIN_TABLE_ROWS: int = 2                # 表格子区域最少行数
    MIN_TABLE_HEIGHT_PT: float = 20.0      # 表格子区域最小高度
    MIN_PARAGRAPH_HEIGHT_PT: float = 10.0  # 段落子区域最小高度

    # ---- 区段切换容差 ----
    # 允许表格行组中夹杂少量段落行（如表格标题行），
    # 但不允许段落行组中夹杂表格行
    MAX_PARAGRAPH_INTRUSION_IN_TABLE: int = 1  # 表格区内最多允许的连续段落行数


# ============================================================
# 行级数据结构
# ============================================================

class TextRow:
    """一行文本的结构化信息"""

    def __init__(self, items: list, item_getter=None):
        """
        Args:
            items: 该行包含的文本项列表
            item_getter: 可选，从 item 提取 (x0, x1, text) 的函数
                        如果不提供，假定 item 有 .x0, .x1, .text 属性
        """
        if item_getter:
            self._items = items
            self._getter = item_getter
            extracted_full = [item_getter(it) for it in items]
            # extracted_full is [(x0, x1, y0, y1, text), ...]
            self._extracted = [(e[0], e[1], e[4]) for e in extracted_full]  # (x0, x1, text)
            _y0_vals = [e[2] for e in extracted_full]
            _y1_vals = [e[3] for e in extracted_full]
        else:
            self._items = items
            self._getter = None
            self._extracted = [(it.x0, it.x1, it.text) if hasattr(it, 'x0') else (it['x0'], it['x1'], it['text']) for it in items]
            _y0_vals = [it.y0 if hasattr(it, 'y0') else it.get('y0', 0) for it in items]
            _y1_vals = [it.y1 if hasattr(it, 'y1') else it.get('y1', 0) for it in items]

        # 计算基本属性
        self.item_count = len(items)
        if self._extracted:
            self.x0 = min(ex[0] for ex in self._extracted)
            self.x1 = max(ex[1] for ex in self._extracted)
            self.y0 = min(_y0_vals) if _y0_vals else 0.0
            self.y1 = max(_y1_vals) if _y1_vals else 0.0
            self.text = " ".join(str(ex[2]).strip() for ex in self._extracted if str(ex[2]).strip())
        else:
            self.x0 = 0.0
            self.x1 = 0.0
            self.y0 = 0.0
            self.y1 = 0.0
            self.text = ""

        # x 方向分析
        self._sorted_x = sorted(
            [(ex[0], ex[1]) for ex in self._extracted],
            key=lambda p: p[0]
        )
        self._gaps: list = []       # 相邻词之间的间隙宽度列表
        self._large_gap_count: int = 0
        self.x_coverage: float = 0.0
        self.row_type: str = "unknown"  # "table" | "paragraph" | "unknown"

    @property
    def items(self):
        return self._items

    @property
    def width(self) -> float:
        return self.x1 - self.x0


# ============================================================
# 核心分割器
# ============================================================

class ContentSegmenter:
    """内容分割器：将页面文本项按空间模式拆分为段落和表格区域。

    使用示例:
        segmenter = ContentSegmenter()

        # 输入: 一个区域的文本项列表（liteparse 格式）
        result = segmenter.segment_region(
            text_items=region_items,
            page_width=595.0, page_height=842.0,
            page_number=1,
        )
        for r in result.regions:
            print(f"{r.region_type}: {r.text[:60]}...")
    """

    def __init__(self, config: Optional[SegmenterConfig] = None):
        self.cfg = config or SegmenterConfig()

    # ================================================================
    # 公开 API
    # ================================================================

    def segment_region(
        self,
        text_items: list,
        page_width: float,
        page_height: float,
        page_number: int = 0,
        region_bbox: Optional[Tuple[float, float, float, float]] = None,
        item_getter=None,
    ) -> SegmentResult:
        """对单个区域内的文本项进行结构分割。

        Args:
            text_items: 文本项列表，每个项需包含 x0, y0, x1, y1, text
                       可以是 obj 属性形式，也可以是 dict 形式
            page_width: 页面宽度（pt）
            page_height: 页面高度（pt）
            page_number: 页码
            region_bbox: 可选，区域的物理边界 (rx0, ry0, rx1, ry1)，
                         如果不提供则自动从 text_items 推算
            item_getter: 可选，从 item 提取 (x0, x1, y0, y1, text) 的函数

        Returns:
            SegmentResult: 包含分割后的独立区域列表
        """
        t0 = time.perf_counter()

        if not text_items:
            return SegmentResult(
                page_number=page_number,
                page_width=page_width,
                page_height=page_height,
                segment_time_ms=0.0,
            )

        # Step 1: 将文本项归并为"行"
        rows = self._build_rows(text_items, item_getter)
        if len(rows) < 2:
            return self._single_region_result(
                rows, page_number, page_width, page_height, region_bbox, text_items
            )

        # Step 2: 分析每行的 x 方向离散度，分类为 table / paragraph
        region_x_range = self._get_region_x_range(text_items, region_bbox, page_width)
        for row in rows:
            self._classify_row(row, region_x_range, page_width)

        # Step 3: 合并连续同类型行 → 子区域
        sub_regions = self._merge_rows_to_sub_regions(rows, region_x_range, page_width)

        # Step 4: 构建 SegmentResult
        regions = []
        for sub in sub_regions:
            sr = self._build_segment_region(sub, rows, region_x_range)
            if sr:
                regions.append(sr)

        elapsed = (time.perf_counter() - t0) * 1000
        return SegmentResult(
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
            regions=regions,
            segment_time_ms=elapsed,
        )

    def segment_region_by_rows(
        self,
        rows_data: List[Dict[str, Any]],
        page_width: float,
        page_height: float,
        page_number: int = 0,
    ) -> SegmentResult:
        """高级 API：直接按预分组的行数据进行分割。

        Args:
            rows_data: 每行由 {'items': [...], 'y_mid': float} 表示
            page_width/page_height: 页面尺寸
            page_number: 页码

        Returns:
            SegmentResult
        """
        t0 = time.perf_counter()

        if not rows_data:
            return SegmentResult(
                page_number=page_number,
                page_width=page_width,
                page_height=page_height,
                segment_time_ms=0.0,
            )

        rows = []
        all_items = []
        for rd in rows_data:
            row = TextRow(rd['items'])
            rows.append(row)
            all_items.extend(rd['items'])

        region_x_range = (0.0, page_width)  # 宽松默认

        for row in rows:
            self._classify_row(row, region_x_range, page_width)

        sub_regions = self._merge_rows_to_sub_regions(rows, region_x_range, page_width)

        regions = []
        for sub in sub_regions:
            sr = self._build_segment_region(sub, rows, region_x_range)
            if sr:
                regions.append(sr)

        elapsed = (time.perf_counter() - t0) * 1000
        return SegmentResult(
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
            regions=regions,
            segment_time_ms=elapsed,
        )

    # ================================================================
    # 行归并
    # ================================================================

    def _build_rows(
        self,
        text_items: list,
        item_getter=None,
    ) -> List[TextRow]:
        """将文本项按 y 坐标归并为行。"""
        if not text_items:
            return []

        # 按 y0 排序
        if item_getter:
            sorted_items = sorted(text_items, key=lambda it: item_getter(it)[2])  # y0
        elif hasattr(text_items[0], 'y0'):
            sorted_items = sorted(text_items, key=lambda it: it.y0)
        else:
            sorted_items = sorted(text_items, key=lambda it: it.get('y0', 0))

        rows = []
        current_row_items = []
        current_y_mid = None

        for item in sorted_items:
            if item_getter:
                _x0, _x1, y0, y1, _txt = item_getter(item)
            elif hasattr(item, 'y0'):
                y0, y1 = item.y0, item.y1
            else:
                y0 = item.get('y0', 0)
                y1 = item.get('y1', 0)
            y_mid = (y0 + y1) / 2

            if current_y_mid is None:
                current_y_mid = y_mid
                current_row_items.append(item)
            elif abs(y_mid - current_y_mid) <= self.cfg.Y_TOLERANCE_PT:
                current_row_items.append(item)
            else:
                rows.append(TextRow(current_row_items, item_getter))
                current_row_items = [item]
                current_y_mid = y_mid

        if current_row_items:
            rows.append(TextRow(current_row_items, item_getter))

        return rows

    # ================================================================
    # 行分类
    # ================================================================

    def _classify_row(
        self,
        row: TextRow,
        region_x_range: Tuple[float, float],
        page_width: float,
    ):
        """分析一行的 x 方向离散度，将其分类为 table 或 paragraph。"""
        if not row._sorted_x or len(row._sorted_x) < 2:
            row.row_type = "paragraph" if row.item_count == 1 else "unknown"
            row._large_gap_count = 0
            row.x_coverage = (row.x1 - row.x0) / max(region_x_range[1] - region_x_range[0], 1)
            return

        # 计算相邻词之间的 x 间隙
        gaps = []
        large_gaps = 0
        for i in range(len(row._sorted_x) - 1):
            gap = row._sorted_x[i + 1][0] - row._sorted_x[i][1]
            if gap < 0:
                gap = 0  # 词重叠，不算 gap
            gaps.append(gap)
            if gap > self.cfg.MIN_GAP_WIDTH_PT:
                large_gaps += 1

        row._gaps = gaps
        row._large_gap_count = large_gaps

        # 计算 x 覆盖率（相对于页面宽度，而不是区域宽度）
        # 原因：如果区域本身就是全宽表格，区域宽度≈页面宽度，两者相同
        #       但如果区域由密度网格合并（全页面宽度），则需要用页面宽度
        use_width = max(page_width, region_x_range[1] - region_x_range[0])
        if use_width > 0:
            row.x_coverage = (row.x1 - row.x0) / use_width
        else:
            row.x_coverage = 0.0

        # 分类逻辑:
        # 1. 有 >= 2 个大 gap → 明确表格行（3+列）
        # 2. 1 个大 gap + x 覆盖率 < 0.75 → 双列表格
        # 3. 0 gap → 段落行（单一块）
        # 4. x 覆盖率超阈值 (>= 0.85) → 段落行（全宽标题/说明文字）
        # 5. 其余 → 表格（兜底：单 gap 中等覆盖率的行如"本集团/本行"列头）
        if large_gaps >= 2:
            row.row_type = "table"
        elif large_gaps == 1 and row.x_coverage < 0.75:
            row.row_type = "table"  # 双列表格：1个列间 gap + 未占满全宽
        elif large_gaps == 0:
            row.row_type = "paragraph"  # 单一文本块，倾向于段落
        elif row.x_coverage >= self.cfg.X_COVERAGE_SNAP_THRESHOLD:
            row.row_type = "paragraph"
        else:
            row.row_type = "table"  # 兜底：可能是有大空格的表格 or 双列表头

    # ================================================================
    # 子区域合并
    # ================================================================

    def _merge_rows_to_sub_regions(
        self,
        rows: List[TextRow],
        region_x_range: Tuple[float, float],
        page_width: float,
    ) -> List[Dict[str, Any]]:
        """将已分类的行合并为子区域。

        Returns:
            [{row_indices: [idx, ...], region_type: "table"|"paragraph"}]
        """
        if not rows:
            return []

        # 第一遍：按 row_type 合并连续同类型行
        raw_groups = []
        current_type = rows[0].row_type
        current_indices = [0]

        for i in range(1, len(rows)):
            rt = rows[i].row_type
            if rt == current_type:
                current_indices.append(i)
            else:
                raw_groups.append({
                    "row_indices": current_indices,
                    "region_type": current_type,
                })
                current_type = rt
                current_indices = [i]

        raw_groups.append({
            "row_indices": current_indices,
            "region_type": current_type,
        })

        # 第二遍：修正 table→paragraph→table 三明治结构
        # 如果某个 paragraph/unknown 组被两个 table 组夹在中间，
        # 且它的 x 列结构与前后表格共享（item x 中心点重叠比例高），
        # 则该组很可能是被误分类的表格数据行（text 密集导致分类为 paragraph）
        # → 重归类为 table 并与相邻组合并
        # 典型 case：page 5 两段财务表之间 7 行数据丢失
        _reclassify_table_sandwich(raw_groups, rows)

        merged = _merge_adjacent_same_type(raw_groups)

        # 第三遍：检测表头重复，将大表格组拆分为独立表格
        # 当一行被分类为 table 且 gap≥3，文本与组内第一个 table 行
        # 的前几个词高度重合时，判定为新表格的开始 → 在此拆分
        # 典型 case：page 5 行 0 "阶段一 阶段二 阶段三 合计"
        #          行 11 同样为 "阶段一 阶段二 阶段三 合计" → 拆分成两个表
        merged = _split_at_table_headers(merged, rows)

        return merged

    def _build_segment_region(
        self,
        sub: Dict[str, Any],
        rows: List[TextRow],
        region_x_range: Tuple[float, float],
    ) -> Optional[SegmentRegion]:
        """从子区域描述构建 SegmentRegion。"""
        indices = sub["row_indices"]
        rtype = sub["region_type"]
        if not indices:
            return None

        # 收集区域内的所有 text_items
        all_items = []
        for idx in indices:
            all_items.extend(rows[idx].items)

        if not all_items:
            return None

        # 计算区域边界
        if hasattr(all_items[0], 'x0'):
            x0 = min(it.x0 for it in all_items)
            x1 = max(it.x1 for it in all_items)
            y0 = min(it.y0 for it in all_items)
            y1 = max(it.y1 for it in all_items)
        else:
            x0 = min(it.get('x0', 0) for it in all_items)
            x1 = max(it.get('x1', 0) for it in all_items)
            y0 = min(it.get('y0', 0) for it in all_items)
            y1 = max(it.get('y1', 0) for it in all_items)

        # 对于表格类型的子区域，x 方向继承父区域的宽度
        # 避免因子区域内缺少某列文字而导致边界收窄
        # 典型场景：PDF 表头行只有数据区文字，没有行标签列文字，
        # 导致子区域 x0 被抬高，丢失左侧的行标签列
        parent_x0, parent_x1 = region_x_range
        if rtype == "table":
            x0 = min(x0, parent_x0)
            x1 = max(x1, parent_x1)

        # 过滤太小的区域
        height = y1 - y0
        if rtype == "table" and height < self.cfg.MIN_TABLE_HEIGHT_PT:
            return None
        if rtype == "paragraph" and height < self.cfg.MIN_PARAGRAPH_HEIGHT_PT:
            return None

        # 拼接文本
        text_parts = []
        for idx in indices:
            t = rows[idx].text.strip()
            if t:
                text_parts.append(t)
        full_text = "\n".join(text_parts)

        # 计算置信度
        if rtype == "table":
            table_rows = sum(1 for i in indices if rows[i].row_type == "table")
            confidence = table_rows / len(indices) if indices else 0.0
        else:
            paragraph_rows = sum(1 for i in indices if rows[i].row_type == "paragraph")
            confidence = paragraph_rows / len(indices) if indices else 0.0

        # 诊断信息
        diagnosis = {
            "row_count": len(indices),
            "row_indices": [int(i) for i in indices],
            "table_rows": sum(1 for i in indices if rows[i].row_type == "table"),
            "paragraph_rows": sum(1 for i in indices if rows[i].row_type == "paragraph"),
            "avg_x_coverage": round(
                sum(rows[i].x_coverage for i in indices) / max(len(indices), 1), 4
            ),
            "avg_large_gaps": round(
                sum(rows[i]._large_gap_count for i in indices) / max(len(indices), 1), 2
            ),
            "row_details": [
                {
                    "idx": int(i),
                    "type": rows[i].row_type,
                    "x_coverage": round(rows[i].x_coverage, 4),
                    "large_gaps": rows[i]._large_gap_count,
                    "text_preview": rows[i].text[:60] if rows[i].text else "",
                }
                for i in indices
            ],
        }

        # 调试日志：打印子区域的类型分布，便于排查切割原因
        self._log_sub_region(rtype, x0, y0, x1, y1, diagnosis)

        return SegmentRegion(
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
            region_type=rtype,
            text_items=all_items,
            text=full_text,
            confidence=confidence,
            diagnosis=diagnosis,
        )

    def _log_sub_region(self, rtype, x0, y0, x1, y1, diagnosis):
        """打印子区域诊断摘要，帮助排查表格切割问题。"""
        import sys
        rc = diagnosis.get("row_count", 0)
        tc = diagnosis.get("table_rows", 0)
        pc = diagnosis.get("paragraph_rows", 0)
        bbox = f"({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f})"
        print(f"  [Segmenter] {rtype.upper():>9} | rows={rc} T={tc} P={pc} | bbox={bbox}", file=sys.stderr, flush=True)
        # 仅对多类型混杂的子区域打印行详情
        if tc > 0 and pc > 0:
            for rd in diagnosis.get("row_details", []):
                flag = " <-- mix" if rd["type"] != rtype else ""
                print(f"    Row[{rd['idx']:>3}] {rd['type']:>9}  cov={rd['x_coverage']:.3f}  gaps={rd['large_gaps']}  | {rd['text_preview'][:50]}{flag}", file=sys.stderr, flush=True)

    def _single_region_result(
        self,
        rows: List[TextRow],
        page_number: int,
        page_width: float,
        page_height: float,
        region_bbox: Optional[Tuple[float, float, float, float]],
        text_items: list,
    ) -> SegmentResult:
        """当只有 0-1 行时，返回单区域结果。"""
        regions = []
        if rows:
            row = rows[0]
            rtype = "paragraph" if row.item_count <= 1 else "table"
            if region_bbox:
                x0, y0, x1, y1 = region_bbox
            else:
                x0, y0, x1, y1 = row.x0, row.y0, row.x1, row.y1
            regions.append(SegmentRegion(
                x0=x0, y0=y0, x1=x1, y1=y1,
                region_type=rtype,
                text_items=text_items,
                text=row.text,
                confidence=0.5,
                diagnosis={"row_count": 1, "note": "single_row_fallback"},
            ))

        return SegmentResult(
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
            regions=regions,
            segment_time_ms=0.0,
        )

    def _get_region_x_range(
        self,
        text_items: list,
        region_bbox: Optional[Tuple[float, float, float, float]],
        page_width: float,
    ) -> Tuple[float, float]:
        """获取区域的 x 范围。"""
        if region_bbox:
            return region_bbox[0], region_bbox[2]
        # 从 text_items 推算
        if hasattr(text_items[0], 'x0'):
            x0 = min(it.x0 for it in text_items)
            x1 = max(it.x1 for it in text_items)
        else:
            x0 = min(it.get('x0', 0) for it in text_items)
            x1 = max(it.get('x1', 0) for it in text_items)
        return x0, x1


# ============================================================
# 模块级辅助函数
# ============================================================

def _reclassify_table_sandwich(
    groups: List[Dict[str, Any]],
    rows: list,
) -> None:
    """重分类被表格夹在中间的非表格组。

    当 paragraph/unknown 组被两组 table 组夹在中间，
    且中间组的 x 坐标与表格共享列结构时，重归类为 table。

    典型场景：page 5 两段财务表之间，rows 4-10 的 text 密集行
    被 _classify_row 误判为 paragraph → 形成 table→paragraph→table 三明治结构。

    反例防护（v2 新增）：
    - 段落组 ≥3 行且 avg_x_coverage ≥ 0.85 → 真实段落文本（如脚注、说明文字），不合并
    - 段落组 ≥3 行且 paragraph 行占比 ≥ 60% → 偏向真实段落，不合并
      （避免 page 6 等场景的段落脚注被误吸入表格）
    - 1-2 行的小规模组跳过上述两个检查：单行组 para_ratio 恒为 1.0，
      误拦会导致子表标签行无法合并回表格（page 5 典型案例）
    """
    for gi in range(1, len(groups) - 1):
        g_prev = groups[gi - 1]
        g_curr = groups[gi]
        g_next = groups[gi + 1]

        if g_prev["region_type"] != "table":
            continue
        if g_next["region_type"] != "table":
            continue
        if g_curr["region_type"] not in ("paragraph", "unknown"):
            continue

        # ---- 反例防护：检查段落组的"真实性" ----
        indices = g_curr["row_indices"]
        if indices:
            avg_cov = sum(rows[i].x_coverage for i in indices) / len(indices)
            # 高 x 覆盖率 → 全宽段落文本（脚注、说明），非表格数据
            # 但对 1-2 行小规模组放宽：子表标签行可能覆盖较宽但仍属表格
            _small_group = len(indices) <= 2
            if avg_cov >= 0.85 and not _small_group:
                continue
            # paragraph 行占比高 → 确实是段落，非误分类的表格行
            # 仅对 ≥3 行组启用此检查：1-2 行组大概率是子表标签行
            # （单行组 para_ratio 恒为 1.0，必然触发误拦）
            if len(indices) >= 3:
                para_ratio = sum(1 for i in indices if rows[i].row_type == "paragraph") / len(indices)
                if para_ratio >= 0.60:
                    continue

        if _share_x_with_tables(g_curr, g_prev, g_next, rows):
            g_curr["region_type"] = "table"


def _share_x_with_tables(
    para_group: Dict[str, Any],
    table_group_a: Dict[str, Any],
    table_group_b: Dict[str, Any],
    rows: list,
) -> bool:
    """检查段落组的 x 中心点是否落在前后表格组的 x 范围内。

    如果 ≥ 70% 的段落 item 的 x 中心点与表格 item 的 x 范围重叠，
    则认为该段落组与表格共享列结构，应属于同一表格。
    """
    para_xc = _collect_x_centers(para_group["row_indices"], rows)
    if not para_xc:
        return False

    table_xc = _collect_x_centers(table_group_a["row_indices"], rows)
    table_xc += _collect_x_centers(table_group_b["row_indices"], rows)
    if not table_xc:
        return False

    tx_min, tx_max = min(table_xc) - 25, max(table_xc) + 25
    if tx_max <= tx_min:
        return False

    in_range = sum(1 for xc in para_xc if tx_min <= xc <= tx_max)
    return in_range / len(para_xc) >= 0.70


def _collect_x_centers(indices, rows: list) -> List[float]:
    """收集指定行索引中所有 item 的 x 中心点。"""
    xc_list = []
    for idx in indices:
        row = rows[idx]
        for ex in row._extracted:
            xc = (ex[0] + ex[1]) / 2
            xc_list.append(xc)
    return xc_list


def _split_at_table_headers(
    groups: List[Dict[str, Any]],
    rows: list,
) -> List[Dict[str, Any]]:
    """在合并后的表格组中检测表头重复并拆分。

    大表格组可能包含多个独立表格（由表头重复标识），
    拆分为独立子表格可避免下游 Step1ColumnSplit 的行检测混乱。

    典型 case：page 5 财务表，行 0 和行 11 都是
    "阶段一 阶段二 阶段三 合计" → 在行 11 拆分。
    """
    result = []
    for group in groups:
        if group["region_type"] != "table":
            result.append(group)
            continue

        indices = list(group["row_indices"])
        if len(indices) <= 5:  # 太小的组无需拆分
            result.append(group)
            continue

        # 找组内第一个有 ≥2 gaps 的 table row 作为参考表头
        # 阈值从 ≥3 降为 ≥2：3列表格（如"折现率 | 1.75% | 2.50%"）只有 2 个 gap，
        # 原有 ≥3 阈值会漏掉这类表格 → 无法拆分合并的多表格
        first_header_idx = None
        for i in indices:
            r = rows[i]
            if r.row_type == "table" and r._large_gap_count >= 2:
                first_header_idx = i
                break

        if first_header_idx is None:
            result.append(group)
            continue

        first_header_words = _normalize_header_words(rows[first_header_idx].text)

        # 从参考表头之后找匹配的重复表头位置
        split_at = None
        header_pos_in_indices = indices.index(first_header_idx)
        for j in range(header_pos_in_indices + 2, len(indices)):  # +2 跳过至少 2 行
            idx = indices[j]
            r = rows[idx]
            if r.row_type != "table" or r._large_gap_count < 2:
                continue
            candidate_words = _normalize_header_words(r.text)
            if _header_match(first_header_words, candidate_words):
                split_at = j
                break

        if split_at is None:
            result.append(group)
            continue

        # 拆分
        result.append({
            "row_indices": indices[:split_at],
            "region_type": "table",
        })
        result.append({
            "row_indices": indices[split_at:],
            "region_type": "table",
        })

    return result


def _normalize_header_words(text: str) -> List[str]:
    """提取表头文本中长度 ≥2 的词（过滤纯数字和符号）。"""
    words = text.split()
    return [w for w in words if len(w) >= 2]


def _header_match(a: List[str], b: List[str]) -> bool:
    """检查两个表头词列表是否匹配（≥2 个词相同）。"""
    if len(a) < 2 or len(b) < 2:
        return False
    common = sum(1 for wa in a for wb in b if wa == wb)
    return common >= 2


def _merge_adjacent_same_type(
    groups: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """合并相邻的同类型组。"""
    if not groups:
        return []
    merged = [groups[0]]
    for g in groups[1:]:
        last = merged[-1]
        if last["region_type"] == g["region_type"]:
            last["row_indices"].extend(g["row_indices"])
        else:
            merged.append(g)
    return merged
