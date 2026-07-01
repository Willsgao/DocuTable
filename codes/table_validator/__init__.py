# -*- coding: utf-8 -*-
"""
Table Validator — 模块入口

交叉验证模块：
- 规则分类器：判断每一页 PDF 是否包含真正的数据表格
- LLM 验证器：对真表格页调用 LLM 进行 5 维度深度验证
- 规则修复器：自底向上分层表头结构修复（纯规则，零LLM调用）
- 页面布局模型（V3 新增）：自适应阈值推导，替代硬编码魔法数字
- 分块决策器（V3 新增）：集中化表格/文本分块决策，替代分散补丁链
- 统一去重引擎（V3 新增）：单一执法点去重，替代 8 个分散去重函数
"""

from .config import CLASSIFIER_CONFIG, LLM_CONFIG, TOC_KEYWORDS
from .models import (
    ClassifyResult,
    LLMVerifyResult,
    CrossValidateReport,
)
from .table_classifier import classify_page, classify_all
from .llm_checker import verify_table_with_llm
from .validator import run_cross_validation, quick_classify
from .rule_based_repair import (
    repair_table_rules,
    repair_and_split_tables,
    deduplicate_adjacent_tables,
    generate_rules_repair_report,
    RepairAnomaly,
    has_high_severity_anomalies,
    has_medium_or_higher_anomalies,
    prepare_anomalies_for_llm,
    ANCHOR_SHIFT,
    WEAK_ANCHOR,
    HEADER_TEXT_MISSING,
    DATA_HEADER_MISMATCH,
    ORPHAN_HEADER_TEXT,
    TRUNCATED_HEADER_MERGED,
    ANOMALY_LOW,
    ANOMALY_MEDIUM,
    ANOMALY_HIGH,
)

# V3 架构修复模块
from .page_layout_model import PageLayoutModel
from .table_block_decider import (
    TableBlockDecider,
    TableBlock,
    BlockType,
    MergeSignal,
    decide_table_blocks,
)
from .dedup_engine import (
    DeduplicationEngine,
    DedupPolicy,
    dedup_all,
    normalize_cell,
    row_fingerprint,
    jaccard_similarity,
    is_numeric_cell,
    row_numeric_ratio,
)

__all__ = [
    # 配置
    "CLASSIFIER_CONFIG",
    "LLM_CONFIG",
    "TOC_KEYWORDS",
    # 模型
    "ClassifyResult",
    "LLMVerifyResult",
    "CrossValidateReport",
    # 分类器
    "classify_page",
    "classify_all",
    # LLM
    "verify_table_with_llm",
    # 编排
    "run_cross_validation",
    "quick_classify",
    # 规则修复
    "repair_table_rules",
    "repair_and_split_tables",
    "deduplicate_adjacent_tables",
    "generate_rules_repair_report",
    "RepairAnomaly",
    "has_high_severity_anomalies",
    "has_medium_or_higher_anomalies",
    "prepare_anomalies_for_llm",
    # 异常类型常量
    "ANCHOR_SHIFT",
    "WEAK_ANCHOR",
    "HEADER_TEXT_MISSING",
    "DATA_HEADER_MISMATCH",
    "ORPHAN_HEADER_TEXT",
    "TRUNCATED_HEADER_MERGED",
    "ANOMALY_LOW",
    "ANOMALY_MEDIUM",
    "ANOMALY_HIGH",
    # V3 架构修复
    "PageLayoutModel",
    "TableBlockDecider",
    "TableBlock",
    "BlockType",
    "MergeSignal",
    "decide_table_blocks",
    "DeduplicationEngine",
    "DedupPolicy",
    "dedup_all",
    "normalize_cell",
    "row_fingerprint",
    "jaccard_similarity",
    "is_numeric_cell",
    "row_numeric_ratio",
]
