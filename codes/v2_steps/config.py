# -*- coding: utf-8 -*-
"""V2 Steps 全局配置系统

支持从 JSON 文件加载配置覆盖默认值。
每步有自己的 CONFIG 字典，运行时可覆盖。
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional


class V2Config:
    """V2 全局配置管理

    使用方式：
        cfg = V2Config()
        cfg.load_json("config.json")      # 从文件覆盖
        step1_cfg = cfg.get("step1")

    也可以直接访问类属性：
        V2Config.STEP1_DEFAULTS
    """

    # ============================================================
    # Step 1 默认配置：表格线感知列切分
    # ============================================================
    STEP1_DEFAULTS: Dict[str, Any] = {
        # 行分组
        "y_threshold_factor": 0.4,       # 动态阈值：中位gap × 因子
        "y_threshold_min": 2.0,          # 最小值
        "y_threshold_max": 15.0,         # 最大值

        # 列检测
        "align_tolerance": 8.0,          # 对齐聚簇容差(pt)：需>同列抖动(~6pt)且<列间距(~27pt)
        "gap_factor": 0.3,               # gap阈值因子
        "gap_min": 10.0,                 # gap最小值
        "line_merge_tolerance": 2.0,     # 竖线去重容差(pt)
        "column_line_min_count": 2,      # 指令1最少竖线条数

        # 表格区域
        "table_min_width_ratio": 0.3,    # 表格最小宽度/页宽
        "table_min_height": 20.0,        # 表格最小高度
        "density_grid": 10,              # 文本密度网格数
        "density_threshold": 0.8,        # 密度阈值(×平均值倍数)

        # 单元格分配
        "row_margin_factor": 0.2,        # 行分配允许越界比例
        "preserve_label_indent": True,     # 标签列保留缩进层次（前导空格）
        "indent_step_pt": 12.0,          # 每档缩进对应 x0 偏移(pt)
        "indent_threshold_pt": 5.0,      # 低于此偏移视为 0 级
        "indent_spaces_per_level": 2,    # 每级缩进空格数
        "indent_max_level": 4,           # 最大缩进层级

        # 置信度
        "confidence_col_weight": 0.35,
        "confidence_empty_weight": 0.25,
        "confidence_num_weight": 0.25,
        "confidence_line_bonus": 0.15,

        # 过滤
        "financial_keywords": [
            "万元", "元", "百万", "十亿", "%", "比率",
            "资产", "负债", "收入", "利润", "现金", "股东",
            "资本", "充足率", "率", "额", "数",
        ],
        "min_text_length": 50,

        # pdfplumber 降级
        "pdfplumber_min_words": 20,
        "pdfplumber_min_row_words": 3,
    }

    # ============================================================
    # Step 2 默认配置：合并单元格检测（参考用）
    # ============================================================
    STEP2_DEFAULTS: Dict[str, Any] = {
        "enable_line_detection": True,
        "enable_text_detection": True,
        "enable_coord_detection": True,
        "line_merge_tolerance": 2.0,
        "text_confidence": 0.55,
        "line_confidence": 0.80,
        "coord_confidence": 0.75,
        "row_margin_factor": 0.15,
    }

    # ============================================================
    # Step 3 默认配置：分类器
    # ============================================================
    STEP3_DEFAULTS: Dict[str, Any] = {
        "table_threshold": 0.70,       # ≥ 此值为确定表格
        "review_threshold": 0.45,      # ≥ 此值为待复核
        "weight_numeric_col_ratio": 0.30,
        "weight_data_rows": 0.20,
        "weight_column_count": 0.15,
        "weight_toc_exclude": 0.15,
        "weight_header_quality": 0.20,
        "min_numeric_col_ratio": 0.70,
        "min_data_rows": 3,
        "min_columns": 2,
    }

    # ============================================================
    # Step 4 默认配置：LLM 智能路由
    # ============================================================
    STEP4_DEFAULTS: Dict[str, Any] = {
        "rule_confidence_threshold": 0.8,
        "llm_confidence_threshold": 0.6,
        "max_batch_size": 5,
        "enable_batch": True,
    }

    # ============================================================
    # Step 5 默认配置：并行三通道
    # ============================================================
    STEP5_DEFAULTS: Dict[str, Any] = {
        "enable_pymupdf": True,
        "enable_liteparse": True,
        "enable_pdfplumber": True,
        "fusion_strategy": "liteparse_primary",
        "parallel_workers": 3,
    }

    # ============================================================
    # Step 6 默认配置：TextItem 统一格式
    # ============================================================
    STEP6_DEFAULTS: Dict[str, Any] = {
        "default_confidence": 0.85,
    }

    # ============================================================
    # Step 7 默认配置：表头树
    # ============================================================
    STEP7_DEFAULTS: Dict[str, Any] = {
        "max_header_rows": 5,
        "enable_tree_export": True,
    }

    # ============================================================
    # Step 8 默认配置：质量评估
    # ============================================================
    STEP8_DEFAULTS: Dict[str, Any] = {
        "weight_structure": 0.40,
        "weight_content": 0.30,
        "weight_financial": 0.20,
        "weight_source": 0.10,
    }

    # ---- 实例方法 ----

    def __init__(self, config_path: Optional[str] = None):
        self._overrides: Dict[str, Dict[str, Any]] = {}
        if config_path:
            self.load_json(config_path)

    def load_json(self, path: str) -> None:
        """从 JSON 文件加载配置覆盖"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, val in data.items():
            if isinstance(val, dict):
                self._overrides[key] = val

    def get(self, step_name: str) -> Dict[str, Any]:
        """获取某步的配置（默认值 + 覆盖）"""
        attr_name = f"STEP{step_name.replace('step', '')}_DEFAULTS"
        defaults = getattr(self, attr_name, {})
        result = dict(defaults)
        override_key = step_name.lower()
        if override_key in self._overrides:
            result.update(self._overrides[override_key])
        return result

    @classmethod
    def get_default(cls, step_name: str) -> Dict[str, Any]:
        """类方法：获取某步的默认配置（不加载覆盖）"""
        attr_name = f"STEP{step_name.replace('step', '')}_DEFAULTS"
        return dict(getattr(cls, attr_name, {}))

    # ---- 兼容旧代码的快捷访问 ----
    @classmethod
    def all_step1_defaults(cls) -> Dict[str, Any]:
        """兼容旧 processor.V2_CONFIG 的只读访问"""
        return dict(cls.STEP1_DEFAULTS)
