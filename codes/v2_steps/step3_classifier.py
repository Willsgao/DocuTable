# -*- coding: utf-8 -*-
"""
Step 3: 分类器加权 + needs_review

从 4条件AND 升级为加权评分版。
引入 needs_review 中间状态（0.40 ≤ score < 0.65 → 需人工/LLM复核）。

设计原则：
- 复用 table_classifier 的底层 helper（_is_numeric_value 等），不重复实现
- 新增表头结构质量评估（权重 0.20），填补原分类器盲区
- 保持 classify_page() 接口兼容，返回 ClassifyResult（含 needs_review 字段）
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional

from codes.table_validator.models import ClassifyResult
from codes.table_validator.table_classifier import (
    _is_numeric_value,
    _column_numeric_ratio,
    _column_duplicate_ratio,
    _contains_toc_keyword,
)
from codes.table_validator.config import CLASSIFIER_CONFIG, TOC_KEYWORDS


# ============================================================
# 表头结构质量评估（V2 新增）
# ============================================================

def _evaluate_header_structure(data: List[List[str]]) -> float:
    """评估表格的表头结构质量，返回 0~1 分数。

    评估维度：
    1. 首行文本特征：非数值占比越高，越像表头
    2. 表头行多样性：前几行列名是否各不相同（真表头特征）
    3. 多级表头迹象：连续多行非数值 → 层级表头

    Args:
        data: 2D 表格数据

    Returns:
        0~1 的表头质量分数
    """
    if not data or len(data) < 2:
        return 0.0

    max_cols = max(len(r) for r in data) if data else 0
    if max_cols < 2:
        return 0.0

    score = 0.0

    # ---- 维度1: 首行非数值比例（权重 0.4）----
    first_row = [str(data[0][c]).strip() if c < len(data[0]) else "" for c in range(max_cols)]
    non_empty_first = [v for v in first_row if v]
    if non_empty_first:
        text_count = sum(1 for v in non_empty_first if not _is_numeric_value(v))
        text_ratio = text_count / len(non_empty_first)
        # 如果首行多数是文本 → 像表头
        # 如果首行全是数字 → 可能是无表头的数据表
        score += 0.4 * min(text_ratio / 0.7, 1.0)

    # ---- 维度2: 表头行多样性（权重 0.3）----
    # 检查前 3 行中，是否存在"多样文本行"（各列值互不相同）
    # 真表头通常列名各不相同
    header_rows = data[:min(3, len(data))]
    best_diversity = 0.0
    for row in header_rows:
        texts = [str(row[c]).strip() if c < len(row) else "" for c in range(max_cols)]
        non_empty = [t for t in texts if t]
        if len(non_empty) >= 2:
            unique_ratio = len(set(non_empty)) / len(non_empty)
            best_diversity = max(best_diversity, unique_ratio)
    score += 0.3 * best_diversity

    # ---- 维度3: 多级表头迹象（权重 0.3）----
    # 如果有连续 2+ 行都以文本为主（非数值），表明存在层级表头
    consecutive_text_rows = 0
    for row_idx in range(min(4, len(data))):
        row = data[row_idx]
        row_texts = [str(row[c]).strip() if c < len(row) else "" for c in range(max_cols)]
        non_empty = [t for t in row_texts if t]
        if non_empty:
            text_ratio = sum(1 for t in non_empty if not _is_numeric_value(t)) / len(non_empty)
            if text_ratio >= 0.5:
                consecutive_text_rows += 1
            else:
                break
        else:
            break
    multi_level_score = min(consecutive_text_rows / 2, 1.0)
    score += 0.3 * multi_level_score

    return min(score, 1.0)


# ============================================================
# 加权评分分类器
# ============================================================

# 权重配置（与 config.py STEP3_DEFAULTS 保持一致）
DEFAULT_WEIGHTS = {
    "numeric_col_ratio": 0.30,
    "data_rows": 0.20,
    "column_count": 0.15,
    "toc_exclude": 0.15,
    "header_quality": 0.20,
}

# 阈值
DEFAULT_THRESHOLDS = {
    "table": 0.65,    # ≥ 此值 → 确定真表格
    "review": 0.40,   # ≥ 此值 → needs_review（真表格但存疑）
    # < review → 假表格
}


class Step3Classifier:
    """增强表格分类器（V2 Step 3）

    与原 classify_page 的区别：
    - AND → 加权评分：不再一刀切，各项独立打分后汇总
    - 新增 needs_review 中间状态：低分真表格标记需复核
    - 新增表头结构质量维度

    使用方式：
        result = Step3Classifier.classify(data, page_num=1)
        if result.needs_review:
            print("建议人工复核或 LLM 确认")
    """

    @staticmethod
    def classify(data: List[List[str]],
                 page_num: int = 0,
                 weights: Optional[Dict[str, float]] = None,
                 thresholds: Optional[Dict[str, float]] = None) -> ClassifyResult:
        """加权评分分类

        Args:
            data: 2D 表格数据
            page_num: 页码（仅用于结果标识）
            weights: 自定义权重（None 用默认值）
            thresholds: 自定义阈值（None 用默认值）

        Returns:
            ClassifyResult（含 weighted_score, score_details, needs_review）
        """
        w = weights or DEFAULT_WEIGHTS
        t = thresholds or DEFAULT_THRESHOLDS

        # 空数据快速返回
        if not data or not isinstance(data, list) or len(data) < 1:
            return ClassifyResult(
                page=page_num, is_real_table=False, confidence=0.95,
                reason="无数据或数据格式异常",
                checks={"error": "empty_or_invalid_data"},
                weighted_score=0.0,
            )

        max_cols = max(len(r) for r in data) if data else 0
        data_rows = max(0, len(data) - 1)  # 排除首行表头
        scores: Dict[str, float] = {}
        details: Dict[str, Any] = {}

        # ---- ① 数值列比例（权重 0.30）----
        best_numeric_ratio = 0.0
        numeric_cols = []
        for c in range(max_cols):
            col_vals = []
            for r in data:
                if c < len(r):
                    col_vals.append(str(r[c]).strip() if r[c] is not None else "")
            ratio = _column_numeric_ratio(col_vals)
            if ratio >= CLASSIFIER_CONFIG["numeric_column_ratio"]:
                best_numeric_ratio = max(best_numeric_ratio, ratio)
                numeric_cols.append({"col": c, "ratio": round(ratio, 2)})

        # 宽松兜底：30% + 5 行数据
        if not numeric_cols:
            for c in range(max_cols):
                col_vals = [str(r[c]).strip() if c < len(r) else "" for r in data]
                ratio = _column_numeric_ratio(col_vals)
                if ratio >= 0.30 and data_rows >= 5:
                    best_numeric_ratio = ratio
                    numeric_cols.append({"col": c, "ratio": round(ratio, 2), "relaxed": True})
                    break

        numeric_score = min(best_numeric_ratio / CLASSIFIER_CONFIG["numeric_column_ratio"], 1.0)
        scores["numeric_col_ratio"] = numeric_score
        details["numeric_cols"] = numeric_cols
        details["best_numeric_ratio"] = round(best_numeric_ratio, 3)

        # ---- ② 数据行数量（权重 0.20）----
        row_score = min(data_rows / CLASSIFIER_CONFIG["min_data_rows"], 1.0) if data_rows > 0 else 0.0
        scores["data_rows"] = row_score
        details["data_rows"] = data_rows

        # ---- ③ 列数（权重 0.15）----
        col_score = min(max_cols / CLASSIFIER_CONFIG["min_columns"], 1.0)
        scores["column_count"] = col_score
        details["columns"] = max_cols

        # ---- ④ 目录排除（权重 0.15）----
        is_toc = _contains_toc_keyword(data)
        if not is_toc:
            # 补充检测：高重复率列
            for c in range(max_cols):
                col_vals = [str(r[c]).strip() if c < len(r) else "" for r in data]
                dup_ratio = _column_duplicate_ratio(col_vals)
                if dup_ratio >= CLASSIFIER_CONFIG["toc_duplicate_ratio"] and len(col_vals) >= 3:
                    is_toc = True
                    break
        toc_score = 0.0 if is_toc else 1.0
        scores["toc_exclude"] = toc_score
        details["is_toc"] = is_toc

        # ---- ⑤ 表头结构质量（权重 0.20）----
        header_score = _evaluate_header_structure(data)
        scores["header_quality"] = round(header_score, 3)
        details["header_quality_raw"] = round(header_score, 3)

        # ---- 加权汇总 ----
        weighted_score = sum(scores[k] * w[k] for k in w if k in scores)
        weighted_score = round(weighted_score, 4)

        # ---- 分类决策 ----
        if weighted_score >= t["table"]:
            # 确定真表格
            return ClassifyResult(
                page=page_num, is_real_table=True,
                confidence=round(weighted_score, 2),
                reason=f"加权评分 {weighted_score:.2f} ≥ {t['table']}，确定真表格",
                checks=details,
                needs_review=False,
                weighted_score=weighted_score,
                score_details=scores,
            )
        elif weighted_score >= t["review"]:
            # 待复核
            weak_points = [k for k, v in scores.items() if v < 0.5]
            reason = (f"加权评分 {weighted_score:.2f}，存疑（"
                      f"弱项: {', '.join(weak_points) if weak_points else '无'}），"
                      f"建议人工复核或 LLM 确认")
            return ClassifyResult(
                page=page_num, is_real_table=True,
                confidence=round(weighted_score, 2),
                reason=reason,
                checks=details,
                needs_review=True,
                weighted_score=weighted_score,
                score_details=scores,
            )
        else:
            # 假表格
            return ClassifyResult(
                page=page_num, is_real_table=False,
                confidence=round(1.0 - weighted_score, 2),
                reason=f"加权评分 {weighted_score:.2f} < {t['review']}，判定为非表格",
                checks=details,
                needs_review=False,
                weighted_score=weighted_score,
                score_details=scores,
            )
