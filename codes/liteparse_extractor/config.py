# -*- coding: utf-8 -*-
"""
LiteParse Extractor — 配置常量

与 pdf2docx 通道并行，用 liteparse 的 Grid Projection 算法
逐页解析 PDF，保留空间布局文本。
"""

from pathlib import Path

# ============================================================
# liteparse 调用配置
# ============================================================
LITEPARSE_CONFIG = {
    # ---- 解析范围 ----
    "max_pages": 500,          # 单次解析最多页数

    # ---- 表格区域检测（文本密度网格）----
    "density_grid": 10,          # 网格行数（10x10 切分页面）
    "density_threshold": 0.8,    # 行密度超过均值 * 该系数 → 候选表格行
    "table_min_width_ratio": 0.3,  # 表格区域最小宽度 / 页宽
    "table_min_height": 20.0,      # 表格区域最小高度（pt）

    # ---- 上下文提取 ----
    "context_margin_top": 100.0,   # 表格上方取上下文的最大距离（pt）

    # ---- 输出格式 ----
    "preserve_layout": True,       # 保留空格对齐的版式文本
    "include_bbox": True,          # 附带每个 TextItem 的坐标
}

# ============================================================
# 金融关键词 — 用于快速判断某页是否可能包含表格
# 与 V2-Lite 保持一致
# ============================================================
FINANCIAL_KEYWORDS = [
    "万元", "元", "百万", "十亿", "%", "比率",
    "资产", "负债", "收入", "利润", "现金", "股东",
    "资本", "充足率", "率", "额", "数",
]

# ============================================================
# 缓存路径
# ============================================================
# 中间数据存放在 data/mid_cache/<pdf_name>/liteparse/ 下
MID_CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "mid_cache"
CACHE_SUBDIR = "liteparse"
