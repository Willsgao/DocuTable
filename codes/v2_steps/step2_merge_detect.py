# -*- coding: utf-8 -*-
"""
Step 2: 合并单元格检测（参考用）

从处理器代码迁移而来。定位为辅助/参考信息，非确定性检测。
检测结果存入 result["merge_info"]，默认不修改 table_data。

下游用途：
- LLM 修复时可作为提示信息传入 prompt
- HTML 导出时可渲染 rowspan/colspan
- Step 7 表头树建模可利用 merge 信息
"""

from typing import List, Dict, Tuple, Any, Optional

from .config import V2Config


class Step2MergeDetect:
    """合并单元格检测（V2 Step 2）"""

    @classmethod
    def execute(cls,
                table_data: List[List[str]],
                drawings: List[dict],
                row_bounds: List[Tuple[float, float]],
                col_bounds: List[float],
                config: Optional[Dict[str, Any]] = None,
                apply_merge: bool = False,
                words: Optional[List[dict]] = None) -> tuple:
        """主入口

        Args:
            table_data: 当前网格填充结果
            drawings: get_drawings() 提取的线条
            row_bounds: [(y_top, y_bottom), ...]
            col_bounds: [x0, x1, ...] 列分割线
            config: 步骤配置
            apply_merge: 是否将合并应用到 table_data（默认 False）
            words: 原始 words（用于坐标跨列检测，无线表）

        Returns:
            (modified_table, merge_info, stats)
        """
        if config is None:
            config = V2Config.STEP2_DEFAULTS

        n_rows = len(table_data)
        n_cols = max(len(row) for row in table_data) if table_data else 0

        line_spans: List[Tuple[int, int, int, int, float]] = []
        if config.get("enable_line_detection", True) and drawings:
            line_spans = cls._detect_from_lines(drawings, row_bounds, col_bounds, config)

        text_spans: List[Tuple[int, int, int, int, float]] = []
        if config.get("enable_text_detection", True):
            text_spans = cls._detect_from_text(table_data)

        coord_spans: List[Tuple[int, int, int, int, float]] = []
        if config.get("enable_coord_detection", True) and words:
            coord_spans = cls._detect_from_coordinates(
                words, row_bounds, col_bounds, table_data, config,
            )

        all_spans = line_spans + text_spans + coord_spans
        merged_spans = cls._merge_overlapping_spans(
            all_spans, max(n_rows, 1), max(n_cols, 1),
        )

        # 阶段3：应用合并
        if apply_merge:
            modified_table, merge_info = cls._apply_spans(table_data, merged_spans)
        else:
            modified_table = table_data
            merge_info = {}
            for row, col, rowspan, colspan, conf in merged_spans:
                if rowspan > 1 or colspan > 1:
                    merge_info[(row, col)] = {
                        "rowspan": rowspan, "colspan": colspan, "confidence": conf,
                    }

        stats = {
            "line_spans": len(line_spans),
            "text_spans": len(text_spans),
            "coord_spans": len(coord_spans),
            "total_spans": len(merged_spans),
            "cells_merged": sum(rs * cs - 1 for _, _, rs, cs, _ in merged_spans),
        }

        return modified_table, merge_info, stats

    # ================================================================
    # 线条检测（从 processor.py 迁入）
    # ================================================================

    @staticmethod
    def _detect_from_lines(drawings: List[dict],
                           row_bounds: List[Tuple[float, float]],
                           col_bounds: List[float],
                           config: Dict[str, Any]) -> List[Tuple[int, int, int, int, float]]:
        """从表格线检测合并单元格"""
        if not drawings or len(row_bounds) < 2 or len(col_bounds) < 3:
            return []

        n_rows = len(row_bounds)
        n_cols = len(col_bounds) - 1
        tol = config.get("line_merge_tolerance", 2.0)

        # 提取线条
        v_lines = [(d["x0"], d["y0"], d["y1"]) for d in drawings
                   if d["type"] == "line" and d["direction"] == "v"]
        h_lines = [(d["y0"], d["x0"], d["x1"]) for d in drawings
                   if d["type"] == "line" and d["direction"] == "h"]

        merge_spans = []

        # ---- colspan 检测 ----
        v_x_groups = {}
        for x, y0, y1 in v_lines:
            matched = None
            for gx in v_x_groups:
                if abs(x - gx) <= tol * 2:
                    matched = gx
                    break
            if matched is not None:
                v_x_groups[matched].append((y0, y1))
            else:
                v_x_groups[x] = [(y0, y1)]

        for r, (ry0, ry1) in enumerate(row_bounds):
            row_height = ry1 - ry0
            missing_boundaries = []
            for c in range(1, n_cols):
                boundary_x = col_bounds[c]
                has_line = False
                for gx, segs in v_x_groups.items():
                    if abs(gx - boundary_x) <= tol * 3:
                        for sy0, sy1 in segs:
                            overlap = max(0.0, min(sy1, ry1) - max(sy0, ry0))
                            if overlap > row_height * 0.6:
                                has_line = True
                                break
                        if has_line:
                            break
                if not has_line:
                    missing_boundaries.append(c)

            if not missing_boundaries:
                continue

            # 分组连续缺失 → colspan
            groups = Step2MergeDetect._group_consecutive(missing_boundaries)
            for gs, ge in groups:
                start_col = gs - 1
                if start_col < 0:
                    start_col = 0
                span_cols = (ge + 1) - start_col
                if 2 <= span_cols < n_cols:
                    merge_spans.append((r, start_col, 1, span_cols, 0.85))

        # ---- rowspan 检测 ----
        h_y_groups = {}
        for y, x0, x1 in h_lines:
            matched = None
            for gy in h_y_groups:
                if abs(y - gy) <= tol * 2:
                    matched = gy
                    break
            if matched is not None:
                h_y_groups[matched].append((x0, x1))
            else:
                h_y_groups[y] = [(x0, x1)]

        for c in range(n_cols):
            cx0, cx1 = col_bounds[c], col_bounds[c + 1]
            col_width = cx1 - cx0

            missing_boundaries = []
            for r in range(1, n_rows):
                boundary_y = row_bounds[r][0]
                has_line = False
                for gy, segs in h_y_groups.items():
                    if abs(gy - boundary_y) <= tol * 3:
                        for sx0, sx1 in segs:
                            overlap = max(0.0, min(sx1, cx1) - max(sx0, cx0))
                            if overlap > col_width * 0.6:
                                has_line = True
                                break
                        if has_line:
                            break
                if not has_line:
                    missing_boundaries.append(r)

            if not missing_boundaries:
                continue

            # 排除整列无横线
            if len(missing_boundaries) >= n_rows - 1:
                continue

            groups = Step2MergeDetect._group_consecutive(missing_boundaries)
            for gs, ge in groups:
                start_r = gs - 1
                if start_r < 0:
                    start_r = 0
                span_rows = (ge + 1) - start_r
                if 2 <= span_rows < n_rows:
                    merge_spans.append((start_r, c, span_rows, 1, 0.8))

        return Step2MergeDetect._merge_overlapping_spans(merge_spans, n_rows, n_cols)

    @staticmethod
    def _group_consecutive(indices: List[int]) -> List[Tuple[int, int]]:
        """分组连续整数"""
        if not indices:
            return []
        groups = []
        gs = indices[0]
        gp = indices[0]
        for b in indices[1:]:
            if b == gp + 1:
                gp = b
            else:
                groups.append((gs, gp))
                gs = b
                gp = b
        groups.append((gs, gp))
        return groups

    # ================================================================
    # 文本检测（从 processor.py 迁入）
    # ================================================================

    @staticmethod
    def _detect_from_text(table_data: List[List[str]]) -> List[Tuple[int, int, int, int, float]]:
        """从文本模式检测合并单元格"""
        if not table_data or len(table_data) < 2:
            return []

        n_rows = len(table_data)
        n_cols = max(len(row) for row in table_data) if table_data else 0
        if n_cols < 2:
            return []

        merge_spans = []

        # 模式1：相邻行同一列内容完全相同 → rowspan
        for c in range(n_cols):
            r = 0
            while r < n_rows - 1:
                cur_val = Step2MergeDetect._safe_cell(table_data, r, c)
                if not cur_val or len(cur_val) < 2:
                    r += 1
                    continue

                span_rows = 1
                for nr in range(r + 1, n_rows):
                    next_val = Step2MergeDetect._safe_cell(table_data, nr, c)
                    if next_val == cur_val:
                        span_rows += 1
                    else:
                        break

                if span_rows >= 2:
                    merge_spans.append((r, c, span_rows, 1, 0.7))
                    r += span_rows
                else:
                    r += 1

        # 模式2：表头行连续空单元格 → 可能 colspan
        header_rows = min(n_rows, max(2, n_rows // 3))
        for r in range(header_rows):
            c = 0
            while c < n_cols:
                cur_val = Step2MergeDetect._safe_cell(table_data, r, c)
                if cur_val and len(cur_val) >= 2:
                    empty_count = 0
                    for nc in range(c + 1, n_cols):
                        next_val = Step2MergeDetect._safe_cell(table_data, r, nc)
                        if not next_val or len(next_val.strip()) == 0:
                            empty_count += 1
                        else:
                            break
                    if empty_count >= 1:
                        merge_spans.append((r, c, 1, empty_count + 1, 0.55))
                        c += empty_count + 1
                    else:
                        c += 1
                else:
                    c += 1

        return Step2MergeDetect._merge_overlapping_spans(merge_spans, n_rows, n_cols)

    @staticmethod
    def _detect_from_coordinates(
        words: List[dict],
        row_bounds: List[Tuple[float, float]],
        col_bounds: List[float],
        table_data: List[List[str]],
        config: Dict[str, Any],
    ) -> List[Tuple[int, int, int, int, float]]:
        """无线表：word 横向跨多列 → colspan。"""
        from codes.v2_steps.column_align_utils import (
            _horizontal_overlap,
            _row_index_for_word,
            is_center_header_word,
        )

        if not words or len(col_bounds) < 3 or not table_data:
            return []

        n_rows = len(table_data)
        n_cols = len(col_bounds) - 1
        margin = config.get("row_margin_factor", 0.15)
        conf = float(config.get("coord_confidence", 0.75))
        spans: List[Tuple[int, int, int, int, float]] = []
        seen: set = set()

        for w in words:
            text = str(w.get("text", "")).strip()
            if not text or len(text) < 2:
                continue
            x0 = float(w.get("x0", 0))
            x1 = float(w.get("x1", x0))
            cy = (float(w["y0"]) + float(w["y1"])) / 2.0
            row_idx = _row_index_for_word(cy, row_bounds, margin)
            if row_idx is None or row_idx >= n_rows:
                continue

            spanned: List[int] = []
            for c in range(n_cols):
                lo = float(col_bounds[c])
                hi = float(col_bounds[c + 1])
                col_w = max(hi - lo, 1.0)
                if _horizontal_overlap(x0, x1, lo, hi) >= col_w * 0.2:
                    spanned.append(c)

            if len(spanned) < 2:
                continue

            start_col = min(spanned)
            end_col = max(spanned)
            colspan = end_col - start_col + 1
            if colspan < 2:
                continue

            key = (row_idx, start_col, colspan)
            if key in seen:
                continue

            start_val = Step2MergeDetect._safe_cell(table_data, row_idx, start_col)
            if not start_val:
                continue

            empty_tail = all(
                not Step2MergeDetect._safe_cell(table_data, row_idx, c)
                for c in range(start_col + 1, end_col + 1)
            )
            if empty_tail or is_center_header_word(text):
                spans.append((row_idx, start_col, 1, colspan, conf))
                seen.add(key)

        return Step2MergeDetect._merge_overlapping_spans(spans, n_rows, n_cols)

    @staticmethod
    def _safe_cell(table_data: List[List[str]], row: int, col: int) -> str:
        """安全获取单元格值"""
        if 0 <= row < len(table_data) and 0 <= col < len(table_data[row]):
            return str(table_data[row][col]).strip()
        return ""

    # ================================================================
    # Span 合并与去重
    # ================================================================

    @staticmethod
    def _merge_overlapping_spans(spans: List[Tuple[int, int, int, int, float]],
                                  n_rows: int, n_cols: int) -> List[Tuple[int, int, int, int, float]]:
        """合并重叠的 span，去重并处理冲突"""
        if not spans:
            return []

        sorted_spans = sorted(spans, key=lambda s: s[4], reverse=True)
        occupied = [[False] * n_cols for _ in range(n_rows)]
        result = []

        for row, col, rowspan, colspan, conf in sorted_spans:
            rowspan = min(rowspan, n_rows - row)
            colspan = min(colspan, n_cols - col)
            if rowspan < 1 or colspan < 1:
                continue
            if rowspan == 1 and colspan == 1:
                continue

            conflict = False
            for dr in range(rowspan):
                for dc in range(colspan):
                    if occupied[row + dr][col + dc]:
                        conflict = True
                        break
                if conflict:
                    break

            if not conflict:
                result.append((row, col, rowspan, colspan, round(conf, 2)))
                for dr in range(rowspan):
                    for dc in range(colspan):
                        occupied[row + dr][col + dc] = True

        return sorted(result, key=lambda s: (s[0], s[1]))

    @staticmethod
    def _apply_spans(table_data: List[List[str]],
                     merge_spans: List[Tuple[int, int, int, int, float]]) -> tuple:
        """将合并 span 应用到表格数据"""
        if not merge_spans:
            return table_data, {}

        n_rows = len(table_data)
        n_cols = max(len(row) for row in table_data) if table_data else 0

        normalized = []
        for row in table_data:
            r = list(row)
            while len(r) < n_cols:
                r.append("")
            normalized.append(r)

        merge_info = {}
        for row, col, rowspan, colspan, conf in merge_spans:
            if rowspan <= 1 and colspan <= 1:
                continue
            merge_info[(row, col)] = {
                "rowspan": rowspan, "colspan": colspan, "confidence": conf,
            }
            for dr in range(rowspan):
                for dc in range(colspan):
                    if dr == 0 and dc == 0:
                        continue
                    if row + dr < n_rows and col + dc < n_cols:
                        normalized[row + dr][col + dc] = ""

        return normalized, merge_info
