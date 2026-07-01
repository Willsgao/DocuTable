# DocuTable 架构技术文档

> 最后更新：2026-06-30 · 分支 `dev/v2-optimize`

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [数据流](#3-数据流)
4. [核心模块详解](#4-核心模块详解)
5. [已解决问题清单](#5-已解决问题清单)
6. [已知问题与风险](#6-已知问题与风险)
7. [附录](#7-附录)

---

## 1. 项目概述

DocuTable 是一个 PDF 表格提取系统，核心目标是从金融年报 PDF 中精确提取结构化表格数据。

- **技术栈**: Python 3 + PyQt5 (桌面 UI) + liteparse (Rust/PyO3) + pdf2docx + PyMuPDF + pdfplumber
- **打包**: PyInstaller 单文件 exe · **运行**: 桌面应用 (QApplication)

### 1.1 双流水线架构

| 流水线 | 入口 | 用途 |
|--------|------|------|
| **V2 Pipeline**（规则引擎） | `V2Pipeline.run()` | 文本型 PDF 的 8 步规则提取 + 10 项后处理优化（18 个子优化项） |
| **Processor 主流水线**（多源融合） | `PDFProcessor` / `ProcessingWorker.run()` | pdf2docx + V2 + liteparse 三源融合 |

两条流水线在 processor 汇聚：V2 是文本型 PDF 的主力提取器，Processor 提供 liteparse 旁路 + Hybrid 混合分割 + V3 自适应阈值/统一去重。

---

## 2. 整体架构

```
main.py (QApplication) → MainWindow
  ├── ProcessingManager → ProcessingWorker.run()
  │     ├── pdf2docx (多进程逐页转换)
  │     └── V2Pipeline (8步规则引擎 + 去重 + 段落)
  │           └── 结果融合 + 分类器
  │     ├── liteparse 旁路 (page_processor → regions → text_items → 缓存)
  │     ├── hybrid_segment_tables() (Phase 1→1.5→2→2.5→3→4)
  │     ├── _extract_paragraphs_for_hybrid() (未覆盖项 → 段落)
  │     ├── _deduplicate_text_against_tables() (3层策略)
  │     └── rule_based_repair + deduplicate_adjacent_tables (可选)
  └── TableCompareManager → ExportManager
```

### 2.1 项目目录结构

```
DocuTable/
├── main.py                          # 应用入口
├── codes/
│   ├── pdf_extractor/               # processor.py (~6100行) + _worker.py + pdf_context.py
│   ├── v2_steps/                    # V2 8 步流水线
│   │   ├── pipeline.py              # V2Pipeline 编排器 (8步+去重+段落)
│   │   ├── config.py / models.py
│   │   ├── step1_column_split.py    # 表格线感知列切分
│   │   ├── step2_merge_detect.py    # 合并单元格检测
│   │   ├── step3_classifier.py      # 加权分类器 (5维度+needs_review)
│   │   ├── step4_llm_router.py      # LLM 智能路由
│   │   ├── step5_triple_channel.py  # 三通道并行提取
│   │   ├── step6_textitem_format.py # TextItem 统一格式
│   │   ├── step7_header_tree.py     # 表头树建模 (HO-Tree)
│   │   └── step8_quality_eval.py    # 质量评估 (4维度+ABCDE评级)
│   ├── table_validator/             # 表格验证/分割/修复
│   │   ├── hybrid_segmenter.py      # 混合分割器 (~1950行) — 最核心+最复杂
│   │   ├── liteparse_table_segmenter.py # Liteparse 表分割 (~2700行)
│   │   ├── rule_based_repair.py     # 规则修复器 (~4200行)
│   │   ├── cell_differ.py           # Cell 差异对比 + Y聚簇增强 (~1400行)
│   │   ├── table_classifier.py / table_boundary.py / liteparse_cell_filler.py
│   │   ├── llm_checker.py / llm_table_repair.py
│   │   ├── page_layout_model.py     # V3: 自适应阈值推导 (~300行) 🆕
│   │   ├── table_block_decider.py   # V3: 集中分块决策器 (~650行) 🆕
│   │   └── dedup_engine.py          # V3: 统一去重引擎 (~680行) 🆕
│   ├── liteparse_extractor/         # Liteparse 集成
│   ├── content_segmenter/           # segmenter.py (~700行) + models.py
│   ├── ui/                          # main_window.py + table_compare_manager.py (~248KB)
│   └── core/                        # column_analyzer.py + exporter.py + extractor.py
├── prompts/ / docs/ / files/
```

---

## 3. 数据流

### 3.1 核心数据类型

```
TextItem (liteparse):
  {text, x0, y0, x1, y1, y_mid, item_index, page, ...}

liteparse 表格:
  {page, y0, y1, text_items: [TextItem],
   rows: [{y_mid, texts, items}], column_x_ranges, caption, confidence}

Processor 结果条目:
  {type: "table"|"paragraph"|"annotation"|"text",
   page, data: [[cell]], y0, y1, x0, x1, bbox,
   extractor: "liteparse"|"hybrid"|"hybrid_liteparse_fallback"|...,
   title, context_text, notes, description_text, row_count, col_count,
   text_items: [TextItem], _source_item_indices: [int],
   page_type: "pure_table"|"mixed"|"pure_text", confidence, ...}
```

### 3.2 表格分割数据流（核心路径）

```
PDF
├→ pdf2docx (多进程逐页) → docx_tables: [{page, data}]
└→ liteparse (Rust/PyO3) → liteparse_data: {pages: [{page_number, text_items, table_regions}]}
      ↓
   hybrid_segment_tables()
   ├── Phase 1: detect_table_boundaries → TableBoundary (column fingerprint + numeric anchor)
   ├── Phase 1.5: _capture_gap_text_items (4级优先级: 方向关键词→遗漏表格→长中文→Y距离)
   ├── Phase 2: fuse_docx_tables_with_boundaries (最佳重叠匹配 + liteparse回退 + 脚注剥离)
   ├── Phase 2.5: _split_fused_table_by_structure (Jaccard+unit+列宽跳变+年份模式)
   ├── Phase 3: _merge_cross_page_hybrid (跨页拼接, 默认关闭)
   └── Phase 4: 合并 gap_entries + 重编号
      ↓
   seg_tables → _extract_paragraphs_for_hybrid (带 _source_item_indices)
      → _deduplicate_text_against_tables (Tier1: item_index精确 → Tier2: bbox → Tier3: token)
      → 最终 results → UI
```

---

## 4. 核心模块详解

### 4.1 hybrid_segmenter.py — 混合分割器 (~1950行)

系统**最核心、最复杂**的模块。融合 liteparse 边界信息与 pdf2docx cell 数据。

**核心理念**: "分割归分割，格式化归格式化"——分割阶段只关心表格边界。

**主入口**: `hybrid_segment_tables(liteparse_data, docx_tables)`

**5-Phase 架构**:

| Phase | 核心函数 | 职责 |
|-------|--------|------|
| 1 | `detect_table_boundaries_from_liteparse()` | liteparse regions → TableBoundary |
| 1.5 | `_capture_gap_text_items()` | 间隙文本分类挂载 (Step 16, ~330行) |
| 2 | `fuse_docx_tables_with_boundaries()` | boundary↔docx 融合 + liteparse回退 + 脚注剥离 |
| 2.5 | `_split_fused_table_by_structure()` | 行级结构分裂防误合并 (Step 13) |
| 3 | `_merge_cross_page_hybrid()` | 跨页拼接 (默认关闭) |
| 4 | gap_entries 合并 + 标题增强 | 收尾 |

**关键子功能**:
- **liteparse 回退** (Step 14): boundary 无 docx 匹配 → `_build_table_from_liteparse_fallback` 重建，标注 `extractor: hybrid_liteparse_fallback`, conf=0.70
- **脚注剥离** (Step 17, v2.1): 数值列画像 → 自底向上扫描 → 行首标记+列数一致+数据列对齐。补丁: 单 text block 脚注 (列0含中文) → 脚注行
- **间隙捕获** (Step 16): 关键词("下表"/"注："→ description_text/notes) + 遗漏表格恢复 + 独立文本保留

### 4.2 liteparse_table_segmenter.py — Liteparse 表格分割 (~2700行)

纯规则驱动，不依赖 pdf2docx，不调用 LLM。

**主入口**: `segment_tables_from_liteparse(liteparse_data)`

**6 个 Phase**:
1. **逐页分割**: `_segment_by_regions` (有region) / `_build_single_table` (全页) / `_detect_tables_from_text_clusters` (无region)
2. **图表过滤**: `_filter_chart_like_tables` (7种特征: 轴刻度/孤立单字符/高孤立数值比/标签列缺失/饼图/瀑布标签/极端稀疏)
3. **跨页拼接**: `_merge_cross_page_tables`
4. **全局编号 + 报告**
5. **质量优化后处理** (8个子步骤: 非财务过滤→多表拆分→相邻合并→标题增强→质量分类→边界精修→表头恢复→置信度评分)
6. **重新编号**

**边界精修停止条件**: Y间隙 > 3×行高 / 全宽文本行 / 新表头行 / 连续无对齐

### 4.3 cell_differ.py — 差异检测器 + Y聚类增强 (~1400行)

**V2 关键增强**:

| 函数 | V2 增强 |
|------|--------|
| `_cluster_items_by_y()` | 动态Y阈值(字体自适应) + 首列X基线 + 列类型冲突检测 + 相邻行合并 |
| `_score_table_row()` | 分值制替代 `len≥3` 判定, 2列行也可能被识别 |
| `classify_rows_with_liteparse()` | 高重叠度幽灵行检测 (Jaccard ≥ 0.6) |
| `_merge_split_decimals()` | 小数后缀合并, 保留首项 index + `_merged_from` |
| `_merge_adjacent_rows_with_same_label()` | 相邻首列同标签行合并 |

### 4.4 processor.py — 主处理流水线 (~6100行)

**hybrid 路径核心流程**:
```python
seg_tables, seg_report = hybrid_segment_tables(liteparse_data, docx_tables)
lp_paragraphs = _extract_paragraphs_for_hybrid(liteparse_data, seg_tables)
# 合并间隙条目 + 页面类型标记
results = _dedup_adjacent_tables_in_pipeline(results)  # A0/A1/A2/V4/V5
results = _deduplicate_text_against_tables(results)    # Tier1/2/3
```

**Pipeline 相邻表去重** (`_dedup_adjacent_tables_in_pipeline`, 6层):

| 优先级 | 策略 |
|:---:|------|
| A0 | 整表子集检测 (三重校验: 同位指纹+集合子集+非空行数) |
| A1 | 相邻表头碎片移除 (header_rows≤1 + data_rows≤1 + 总行≤5) |
| A2 | 行级精确指纹去重 (前8行 vs 后8行 + Jaccard回退) |
| A2+ | 孤立章节标题行移除 (后表有表头时) |
| V4 | 碎片保护: `header_rows_a<=1` 约束 |
| V5 | 第二路径同步: `rule_based_repair.py` 同步碎片保护 |

### 4.5 rule_based_repair.py — 规则修复器 (~4200行)

纯规则驱动，零 LLM 调用。

**`repair_table_rules` (6步)**: 表头合成 → 数据区定位 → 表头区确定 → 数据修复 → 表头修复 → 组装

**V2 新增**:
- `_synthesize_header_if_needed()`: 4条件检测 + 默认表头合成 (Step 10)
- `_detect_orphan_data_rows()`: 多表合并 → `MULTI_TABLE_MERGED` 异常
- `deduplicate_adjacent_tables()`: 精确指纹 + Jaccard + 数值占比判定 (Step 9)
- `deduplicate_cross_tables()`: 尾头方向 + 结构完整性 (Step 9+)
- V5 碎片保护: `_is_fragment_table` 在两个匹配循环中检查

### 4.6 v2_steps/pipeline.py — V2 Pipeline 编排

**执行顺序**: Step 5→6 (数据预处理) → Step 1→2 (网格提取) → Step 3→4 (分类+路由) → Step 7→8 (语义增强+质量) → Step Dedup (相邻去重) + 页面类型标记

### 4.7 V3 架构修复模块（已完成实现，尚未接入主流程）🆕

针对 §6.1/§6.2 的根本性解决方案，3 个模块:

| 模块 | 行数 | 目标 |
|------|------|------|
| `page_layout_model.py` | ~300 | 替代7处硬编码阈值，从页面数据自推导 |
| `table_block_decider.py` | ~650 | 替代"先合并再修补"长链，默认不合并，强证据才合并 |
| `dedup_engine.py` | ~680 | 替代4个独立去重点，单一指纹+单一Jaccard+单一判定 |

---

## 5. 已解决问题清单（共 19 项）

| # | 内容 | 标签 | 完成日期 |
|:---:|------|:---:|:---:|
| 0 | 模块化架构拆分 | 🔥 架构基础 | 06-25 |
| 1 | 表格线感知列切分 | 🔥 ROI最高 | 06-25 |
| 2 | 合并单元格视觉恢复 | 🔥 省LLM | 06-25 |
| 3 | 分类器 AND→加权+needs_review | 🔥 精准度 | 06-27 |
| 4 | LLM 智能路由 | 🔥 省API | 06-27 |
| 5 | 并行三通道提取 | 架构优化 | 06-27 |
| 6 | 统一 TextItem 格式 | 架构收敛 | 06-27 |
| 7 | 表头树结构建模 (HO-Tree) | 语义增强 | 06-27 |
| 8 | 质量评估 + cell 溯源 | 产品化 | 06-27 |
| 9 | 相邻表格边界去重 | 🔥 修复重叠 | 06-27 |
| 9+ | 跨表去重 V3 (尾头方向+结构完整性) | 🔥 | 06-27 |
| 9++ | 跨表去重 V4 (A1 保护 + A2+ 标题) | 🔥 | 06-28 |
| 9+++ | 跨表去重 V5 (碎片强化 + 第二路径) | 🔥 | 06-28 |
| 10 | 表头缺失自动合成 | 🔥 | 06-28 |
| 11 | Hybrid 表格分割器 | 🔥 | 06-29 |
| 12 | 页面类型快速标记 | 🔥 | 06-29 |
| 13 | 表结构差异检测+行级分裂+Y修复 | 🔥 | 06-29 |
| 14 | liteparse 回退重建 | 🔥 | 06-29 |
| 15 | 整表子集检测 A0 | 🔥 | 06-29 |
| 16 | Phase 1.5 间隙文本捕获 | 🔥 | 06-29 |
| 17 | 表格尾部脚注行剥离 (v2.1) | 🔥 | 06-30 |
| 18 | Word Item 全局索引精确去重 | 🔥 | 06-30 |

---

## 6. 已知问题与风险

### 6.1 🔴 表格分块的根本性问题

**系统最严重的反复性问题**。尽管 Hybrid V1-V4+ 多轮迭代，分块仍存在系统性缺陷。

**根因**: 决策链路太长——liteparse regions → group → split → build → refine → split_fused → gap_capture → paragraph_extract，7 步误差累积。硬编码阈值散落各处 (80pt/60pt/30pt/25pt/18pt/3.0×/0.6×)，缺乏适应性。

**已进行的多轮修补** (Steps 11-17): 双信号防误合并、行级分裂、最佳重叠匹配、Y坐标重估、gap捕获、脚注剥离、回退重建。每层修补增加复杂度但不解决根源。

**建议方向**:
| 方向 | 描述 | 风险 |
|------|------|------|
| A. 引入 PDF 矢量辅助 | PyMuPDF drawings 横竖线作为硬边界证据 | 中 |
| B. 双阶段确认机制 | 宽松合并 + 严格确认, 不确定标 needs_review | 低 |
| C. 数据驱动阈值 | 字体大小/行高自推导替代魔法数字 | 低 |
| D. 分块决策集中化 | `TableBlockDecider` 单一入口 (V3已实现) | 高 |

### 6.2 🟡 去重逻辑多层冗余

系统中有 **4 个独立去重点**，判定逻辑不统一:

| 去重点 | 位置 | 处理对象 |
|--------|------|---------|
| `deduplicate_adjacent_tables` | rule_based_repair.py | repair 拆分的子表 |
| `_dedup_adjacent_tables_in_pipeline` | processor.py | hybrid 分割后相邻表 (A0/A1/A2) |
| `deduplicate_cross_tables` | rule_based_repair.py | UI 层跨表去重 |
| `_deduplicate_text_against_tables` | processor.py | 表格→文本去重 |

**V4→V5 暴露的问题**: processor.py 的 dedup 加了碎片保护, 但 rule_based_repair.py 的同名函数没有同步。

**V3 方向**: `DeduplicationEngine` 作为统一执法点已实现, 需接入主流程替代分散去重点。

### 6.3 🟡 去重类型盲区

`_deduplicate_text_against_tables` 只处理 `type == "paragraph"/"annotation"`，不处理 `type == "text"` (gap standalone)。Phase 1.5 独立文本条目永远不会被表格→文本去重。

### 6.4 🟡 liteparse 底层稳定性

processor.py 用 `BaseException` 替代 `Exception` 捕获异常 (Rust/PyO3 `PanicException` 继承 `BaseException`)。说明底层仍存在不稳定因素。

### 6.5 🟢 其他

- **跨页拼接默认关闭**: `enable_cross_page=False`
- **Step 2 仅参考**: 合并单元格检测不应用到 table_data
- **V2_CONFIG 遗留**: `PDFProcessor.V2_CONFIG` 与 `V2Config.STEP1_DEFAULTS` 重复定义
- **V3 模块**: page_layout_model / table_block_decider / dedup_engine 已完成实现但未接入主流程

---

## 7. 附录

### 7.1 关键配置参数

| 配置 | 位置 | 默认值 | 说明 |
|------|------|--------|------|
| `boundary_refine_enabled` | liteparse_extractor/config.py | True | 边界精修开关 |
| `boundary_max_gap_ratio` | 同上 | 3.0 | 最大 Y 间隙 = 行高 × N |
| `boundary_max_consecutive_miss` | 同上 | 2 | 连续无对齐行数上限 |
| `boundary_col_tolerance` | 同上 | 15.0 | 列对齐容差 (pt) |
| `Y_MARGIN_BELOW` | hybrid_segmenter.py | 30.0 | 间隙捕获下边距 (pt) |
| `_Y_MARGIN` | processor.py | 25.0 | 段落提取覆盖边距 (pt) |
| `MIN_GAP_WIDTH_PT` | segmenter.py | 12.0 | 列间最小 gap (pt) |
| `X_COVERAGE_SNAP_THRESHOLD` | segmenter.py | 0.85 | X 覆盖率段落阈值 |
| `dedup_max_check_rows` | processor.py / rule_based_repair.py | 8 | 相邻去重检查行数 |
| `dedup_jaccard_threshold` | rule_based_repair.py | 0.7 | Jaccard 回退相似度阈值 |
| `numeric_anchor_consecutive` | hybrid_segmenter.py | 3 | 数值锚点最小连续行数 |

### 7.2 测试覆盖（共 ~370 项）

| 步骤 | 测试文件 | 检查项 | 覆盖场景 |
|:---:|------|:---:|------|
| 1-2 | `_test_v2_quick.py` | 37 | 管线基础 |
| 3 | `_test_step3.py` | 8 | 财务表/小表/纯文本/目录/多级表头 |
| 4 | `_test_step4.py` | 20 | 单异常×12 + 批量路由×3 + 跨表路由 |
| 5 | `_test_step5.py` | 16 | 三通道存在性/融合去重/边界 |
| 6 | `_test_step6.py` | 21 | TextItem转换/置信度/字体增强 |
| 7 | `_test_step7.py` | 23 | 2/3级表头/空缺填充/列修正/展平 |
| 8 | `_test_step8.py` | 26 | 结构/内容/财务/可信度/评级 |
| 9 | `_test_step_dedup.py` | 55 | 碎片/数据重叠/三表/指纹容错 |
| 10 | `_test_synthesize_header.py` | 29 | 延续型/KPI混合/极简/边界 |
| 13 | `_test_hybrid_structure_split.py` | 20 | token/Jaccard/unit/Y坐标/年份/列宽 |
| 16 | `_test_gap_phase15.py` | 12 | 下表引导/脚注/页首页尾/遗漏表格 |
| 17 | `_test_footnote_strip.py` | 27 | 多行脚注/排名保留/合计保留/单列 |
| 18 | `_test_dedup_text_vs_tables.py` | 20 | 索引精确去重三层策略 |
| 18+ | 全部回归 | 102 | dedup+text_vs_tables+footnote 全量回归 |

### 7.3 变更频率热力图（近 30 天）

```
模块                         近30天变更行数
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
hybrid_segmenter.py          ████████████ ~1200
liteparse_table_segmenter.py ██████████   ~1200
processor.py                 ████████     ~790
dedup_engine.py              ████████     ~680  🆕 V3
table_block_decider.py       ██████       ~650  🆕 V3
rule_based_repair.py         ████         ~60
page_layout_model.py         ██           ~300  🆕 V3
cell_differ.py               ██           ~200
pipeline.py                  █            ~17
segmenter.py                 █            ~10
```

**hybrid_segmenter 和 liteparse_table_segmenter 是变更最频繁的模块**，说明表格分块逻辑仍在持续迭代，稳定度不足。V3 三个新模块 (dedup_engine / table_block_decider / page_layout_model) 已完成实现，但尚未接入主流程，接入后将显著减少 hybrid_segmenter 和 processor.py 的去重/分块修补代码。
