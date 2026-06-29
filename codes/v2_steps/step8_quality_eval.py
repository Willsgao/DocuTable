# -*- coding: utf-8 -*-
"""
Step 8: 质量评估 + cell 溯源

多维度质量评分系统，输出表格级综合评级（A/B/C/D/E）。

评估维度：
1. 结构完整性（40%）— 列数一致性、表头覆盖率、单元格填充率
2. 内容一致性（30%）— 数值格式一致性、同列单位统一性
3. 财务特征匹配（20%）— 财务关键词命中、模式匹配
4. 提取源可信度（10%）— cell 级溯源、通道置信度

输出：QualityReport dataclass，包含评分明细和综合评级。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple


# ============================================================
# 数据模型
# ============================================================

@dataclass
class QualityReport:
    """表格质量评估报告"""
    page: int = 0
    overall_score: float = 0.0              # 0~1 综合评分
    grade: str = "E"                        # A/B/C/D/E

    # 子维度
    structure_score: float = 0.0            # 结构完整性 0~1
    content_score: float = 0.0              # 内容一致性 0~1
    financial_score: float = 0.0            # 财务特征 0~1
    source_score: float = 0.0               # 提取源可信度 0~1

    # 明细
    structure_details: Dict[str, Any] = field(default_factory=dict)
    content_details: Dict[str, Any] = field(default_factory=dict)
    financial_details: Dict[str, Any] = field(default_factory=dict)
    source_details: Dict[str, Any] = field(default_factory=dict)

    # 问题清单
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        """是否可靠（B 级以上）"""
        return self.grade in ("A", "B")

    @property
    def needs_review(self) -> bool:
        """是否需要人工复核（C 级及以下）"""
        return self.grade in ("C", "D", "E")

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "overall_score": round(self.overall_score, 3),
            "grade": self.grade,
            "is_reliable": self.is_reliable,
            "structure_score": round(self.structure_score, 3),
            "content_score": round(self.content_score, 3),
            "financial_score": round(self.financial_score, 3),
            "source_score": round(self.source_score, 3),
            "structure_details": self.structure_details,
            "content_details": self.content_details,
            "financial_details": self.financial_details,
            "source_details": self.source_details,
            "issues": self.issues,
            "suggestions": self.suggestions,
        }


# ============================================================
# 工具函数
# ============================================================

def _is_numeric(v: str) -> bool:
    """判断是否为数值"""
    s = str(v).strip().rstrip('%').replace(',', '').replace(' ', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False

def _is_empty(v: Any) -> bool:
    """判断 cell 是否为空"""
    if v is None:
        return True
    return str(v).strip() == ""


# ============================================================
# 评级映射
# ============================================================

def _score_to_grade(score: float) -> str:
    if score >= 0.90:
        return "A"
    elif score >= 0.75:
        return "B"
    elif score >= 0.60:
        return "C"
    elif score >= 0.40:
        return "D"
    return "E"


# ============================================================
# 1. 结构完整性评分
# ============================================================

def _score_structure(data: List[List[str]]) -> Tuple[float, Dict, List[str]]:
    """评估表格结构完整性

    返回: (score 0~1, details dict, issues list)
    """
    if not data or len(data) < 2:
        return 0.0, {"error": "insufficient_data"}, ["数据行不足"]

    max_cols = max(len(r) for r in data)
    if max_cols < 2:
        return 0.0, {"error": "too_few_columns"}, ["列数不足2"]

    details: Dict[str, Any] = {}
    issues: List[str] = []
    scores: Dict[str, float] = {}

    # ---- 列数一致性（0.35 权重）----
    col_counts = [len(r) for r in data]
    if len(set(col_counts)) == 1:
        scores["col_consistency"] = 1.0
    else:
        # 列数波动率 = 标准差 / 均值
        avg_cols = sum(col_counts) / len(col_counts)
        variance = sum((c - avg_cols) ** 2 for c in col_counts) / len(col_counts)
        std_dev = variance ** 0.5
        cv = std_dev / max(avg_cols, 1)
        scores["col_consistency"] = max(0, 1.0 - cv * 10)
        if cv > 0.1:
            issues.append(f"列数不一致(波动率{cv:.1%})")
    details["col_counts"] = col_counts

    # ---- 表头覆盖率（0.35 权重）----
    # 首行非空 cell 占列数的比例
    first_row = data[0][:max_cols] + [""] * (max_cols - len(data[0]))
    header_filled = sum(1 for c in first_row[:max_cols] if not _is_empty(c))
    header_coverage = header_filled / max_cols
    scores["header_coverage"] = header_coverage
    details["header_filled"] = header_filled
    details["total_cols"] = max_cols
    if header_coverage < 0.5:
        issues.append(f"表头覆盖率低({header_coverage:.0%})")

    # ---- 单元格填充率（0.30 权重）----
    total_cells = max_cols * len(data)
    non_empty = sum(
        1 for row in data
        for c in row[:max_cols]
        if not _is_empty(c)
    )
    fill_rate = non_empty / max(total_cells, 1)
    scores["fill_rate"] = fill_rate
    details["fill_rate"] = round(fill_rate, 3)
    if fill_rate < 0.4:
        issues.append(f"填充率低({fill_rate:.0%})")

    # 加权综合
    total = (scores["col_consistency"] * 0.35 +
             scores["header_coverage"] * 0.35 +
             scores["fill_rate"] * 0.30)

    details["sub_scores"] = {k: round(v, 3) for k, v in scores.items()}
    return min(total, 1.0), details, issues


# ============================================================
# 2. 内容一致性评分
# ============================================================

def _score_content(data: List[List[str]]) -> Tuple[float, Dict, List[str]]:
    """评估表格内容一致性

    返回: (score 0~1, details dict, issues list)
    """
    if not data or len(data) < 2:
        return 0.0, {}, ["数据不足"]

    max_cols = max(len(r) for r in data)
    details: Dict[str, Any] = {}
    issues: List[str] = []
    scores: Dict[str, float] = {}

    # ---- 数值列格式一致性（0.50 权重）----
    # 检查每列数值的格式是否统一（小数位数、百分号、千分位等）
    format_consistencies = []
    for c in range(max_cols):
        col_vals = [
            str(row[c]).strip() if c < len(row) else ""
            for row in data[1:]  # 排除表头
        ]
        numeric_vals = [v for v in col_vals if _is_numeric(v)]
        if len(numeric_vals) < 2:
            continue

        # 检测格式模式
        formats = set()
        for v in numeric_vals:
            if '%' in v:
                formats.add('percent')
            elif ',' in v:
                formats.add('comma')
            elif '.' in v:
                # 统计小数位数
                dec_part = v.split('.')[-1].replace('%', '')
                formats.add(f'dec{len(dec_part)}')
            elif v.replace('-', '').isdigit():
                formats.add('integer')
            else:
                formats.add('other')
        consistency = 1.0 / max(len(formats), 1)
        format_consistencies.append(consistency)

    if format_consistencies:
        scores["format_consistency"] = sum(format_consistencies) / len(format_consistencies)
    else:
        scores["format_consistency"] = 0.5  # 无足够数值，中性分
    details["format_consistency"] = round(scores["format_consistency"], 3)

    # ---- 文本不重复率（0.25 权重）----
    # 数据行中相邻行文本完全相同的比例
    duplicate_rows = 0
    for i in range(1, len(data) - 1):
        row1 = [str(c).strip() for c in data[i]]
        row2 = [str(c).strip() for c in data[i + 1]]
        if row1 == row2 and any(c for c in row1):
            duplicate_rows += 1
    data_rows = max(len(data) - 1, 1)
    dup_ratio = duplicate_rows / data_rows
    scores["text_uniqueness"] = max(0, 1.0 - dup_ratio * 3)
    details["duplicate_row_ratio"] = round(dup_ratio, 3)
    if dup_ratio > 0.3:
        issues.append(f"高重复行比例({dup_ratio:.0%})")

    # ---- 同列数据语义一致性（0.25 权重）----
    # 检查每列是否数值/文本类型一致
    type_consistencies = []
    for c in range(max_cols):
        col_vals = [
            str(row[c]).strip() if c < len(row) else ""
            for row in data[1:] if c < len(row) and str(row[c]).strip()
        ]
        if not col_vals:
            continue
        num_count = sum(1 for v in col_vals if _is_numeric(v))
        ratio = num_count / len(col_vals)
        # 接近 0 或 1 = 类型一致
        type_consistencies.append(max(ratio, 1.0 - ratio))

    if type_consistencies:
        scores["type_consistency"] = sum(type_consistencies) / len(type_consistencies)
    else:
        scores["type_consistency"] = 0.5
    details["type_consistency"] = round(scores["type_consistency"], 3)

    total = (scores["format_consistency"] * 0.50 +
             scores["text_uniqueness"] * 0.25 +
             scores["type_consistency"] * 0.25)

    details["sub_scores"] = {k: round(v, 3) for k, v in scores.items()}
    return min(total, 1.0), details, issues


# ============================================================
# 3. 财务特征匹配评分
# ============================================================

FINANCIAL_KEYWORDS = [
    "资产", "负债", "权益", "收入", "利润", "成本", "费用",
    "现金", "资本", "投资", "融资", "股东", "所有者",
    "营业收入", "净利润", "总资产", "流动资产", "非流动",
    "公积", "未分配", "综合收益", "每股",
    # 常见科目名
    "货币资金", "应收账款", "存货", "固定资产", "无形资产",
    "短期借款", "长期借款", "应付账款", "预收款项",
    "实收资本", "资本公积", "盈余公积",
]

FINANCIAL_PATTERNS = [
    (r"资产.*总计|资产.*合计", 3, "资产负债表"),
    (r"负债.*总计|负债.*合计", 3, "负债表"),
    (r"收入|营业.*收入|营业.*总", 2, "利润表"),
    (r"净利润|净亏损", 3, "利润表"),
    (r"现金.*流量|经营.*活动", 2, "现金流量表"),
    (r"股东.*权益|所有者.*权益", 3, "权益表"),
]


def _score_financial(data: List[List[str]]) -> Tuple[float, Dict, List[str]]:
    """评估财务特征匹配度

    返回: (score 0~1, details dict, issues list)
    """
    if not data:
        return 0.0, {}, ["无数据"]

    full_text = " ".join(
        str(c) for row in data for c in row if not _is_empty(c))

    details: Dict[str, Any] = {}
    issues: List[str] = []

    # ---- 财务关键词命中率（0.5 权重）----
    hit_count = sum(1 for kw in FINANCIAL_KEYWORDS if kw in full_text)
    kw_score = min(hit_count / 8, 1.0)  # 命中 8 个即满分
    details["keyword_hits"] = hit_count
    details["keyword_score"] = round(kw_score, 3)

    # ---- 财务报表模式匹配（0.5 权重）----
    pattern_score = 0.0
    matched_patterns = []
    for pattern, weight, name in FINANCIAL_PATTERNS:
        if re.search(pattern, full_text):
            pattern_score += weight
            matched_patterns.append(name)

    pattern_score = min(pattern_score / 10, 1.0)  # 归一化
    details["pattern_score"] = round(pattern_score, 3)
    details["matched_patterns"] = matched_patterns

    if not matched_patterns:
        issues.append("未匹配典型财务报表模式")

    total = kw_score * 0.50 + pattern_score * 0.50
    return min(total, 1.0), details, issues


# ============================================================
# 4. 提取源可信度
# ============================================================

CHANNEL_TRUST = {
    "pymupdf": 0.95,
    "liteparse": 0.90,
    "pdfplumber": 0.85,
    "pymupdf_dict": 0.80,
    "paddleocr": 0.75,
    "unknown": 0.50,
}


def _score_source(data: List[List[str]],
                  source_channel: str = "",
                  text_items: Optional[List[Any]] = None) -> Tuple[float, Dict, List[str]]:
    """评估提取源可信度

    Args:
        data: 2D 表格数据
        source_channel: 主提取通道
        text_items: TextItem 列表（可选，用于 cell 级溯源）

    Returns: (score 0~1, details dict, issues list)
    """
    details: Dict[str, Any] = {}
    issues: List[str] = []

    # 通道基线分数
    base_trust = CHANNEL_TRUST.get(source_channel, 0.50)
    details["channel"] = source_channel or "unknown"
    details["channel_trust"] = base_trust

    # 如有 TextItem 列表，计算 cell 级溯源可信度
    if text_items:
        source_counts: Dict[str, int] = {}
        for ti in text_items:
            src = getattr(ti, "source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

        if source_counts:
            weighted_trust = sum(
                CHANNEL_TRUST.get(s, 0.50) * c
                for s, c in source_counts.items()
            ) / sum(source_counts.values())
            # 混合：通道基线 40% + cell 级 60%
            score = base_trust * 0.4 + weighted_trust * 0.6
            details["cell_sources"] = source_counts
        else:
            score = base_trust
    else:
        score = base_trust

    if score < 0.65:
        issues.append(f"提取源可信度低({score:.0%})")

    return min(score, 1.0), details, issues


# ============================================================
# 综合评估入口
# ============================================================

DEFAULT_WEIGHTS = {
    "structure": 0.40,
    "content": 0.30,
    "financial": 0.20,
    "source": 0.10,
}


class Step8QualityEval:
    """质量评估与 cell 溯源（V2 Step 8）

    使用方式：
        report = Step8QualityEval.evaluate(table_data, page_num=1)
        if report.needs_review:
            print("建议人工复核")
        print(report.to_dict())
    """

    @staticmethod
    def evaluate(data: List[List[str]],
                 page_num: int = 0,
                 source_channel: str = "",
                 text_items: Optional[List[Any]] = None,
                 weights: Optional[Dict[str, float]] = None,
                 ) -> QualityReport:
        """综合质量评估

        Args:
            data: 2D 表格数据
            page_num: 页码
            source_channel: 提取通道名
            text_items: TextItem 列表（用于 cell 溯源）
            weights: 自定义维度权重

        Returns:
            QualityReport
        """
        w = weights or DEFAULT_WEIGHTS
        report = QualityReport(page=page_num)

        # 1. 结构完整性
        report.structure_score, report.structure_details, issues = _score_structure(data)
        report.issues.extend(issues)

        # 2. 内容一致性
        report.content_score, report.content_details, issues = _score_content(data)
        report.issues.extend(issues)

        # 3. 财务特征匹配
        report.financial_score, report.financial_details, issues = _score_financial(data)
        report.issues.extend(issues)

        # 4. 提取源可信度
        report.source_score, report.source_details, issues = _score_source(
            data, source_channel, text_items)
        report.issues.extend(issues)

        # 加权汇总
        report.overall_score = (
            report.structure_score * w.get("structure", 0.40) +
            report.content_score * w.get("content", 0.30) +
            report.financial_score * w.get("financial", 0.20) +
            report.source_score * w.get("source", 0.10)
        )
        report.grade = _score_to_grade(report.overall_score)

        # 生成建议
        if report.structure_score < 0.5:
            report.suggestions.append("表格结构不完整，建议检查列切分")
        if report.content_score < 0.5:
            report.suggestions.append("内容一致性低，可能存在解析错误")
        if report.financial_score < 0.3:
            report.suggestions.append("财务特征弱，可能不是财务数据表")

        return report

    @staticmethod
    def evaluate_batch(tables: List[Dict[str, Any]],
                       ) -> List[QualityReport]:
        """批量评估多个表格

        Args:
            tables: [{page, data, extractor?, text_items?}, ...]

        Returns:
            [QualityReport, ...]
        """
        reports = []
        for t in tables:
            data = t.get("data", [])
            if not data:
                continue

            r = Step8QualityEval.evaluate(
                data=data,
                page_num=t.get("page", 0),
                source_channel=t.get("extractor", ""),
                text_items=t.get("text_items"),
            )
            reports.append(r)

        # 全局汇总
        if reports:
            avg = sum(r.overall_score for r in reports) / len(reports)
            print(f"  [V2 Quality] 批量评估: {len(reports)}个表格, "
                  f"均分={avg:.2f}, 级={_score_to_grade(avg)}")

        return reports
