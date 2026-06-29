# DocuTable V2 — 新一代文档表格精准解析技术方案

> **目标**：在 DocuTable 现有代码基础上，融合业界最优解析策略，构建一套科学、精准、可演进的文档表格解析系统。
>  
> **设计原则**：规则先行（零成本基线）→ 模型增强（精度提升）→ LLM兜底（语义修复），逐级递进，每层独立可替换。

---

## 目录

1. [现状诊断：当前架构的优势与短板](#1-现状诊断)
2. [行业最佳实践总结](#2-行业最佳实践)
3. [V2 总体架构设计](#3-v2-总体架构)
4. [分层技术方案](#4-分层技术方案)
   - [Layer 0：文档预处理与类型识别](#layer-0)
   - [Layer 1：多通道文本提取](#layer-1)
   - [Layer 2：版面分析与表格定位](#layer-2)
   - [Layer 3：表格结构重建](#layer-3)
   - [Layer 4：语义理解与修复](#layer-4)
5. [质量评估体系](#5-质量评估体系)
6. [分阶段实施路线图](#6-实施路线图)

---

## 1. 现状诊断

### 1.1 架构全景

```
PDF 输入
  ├─ PDFContext（共享上下文：页数、尺寸、文本/图片类型标记）
  │
  ├─ 通道 A: pdf2docx → pdfplumber (fallback) → PyMuPDF (fallback)
  │     └─ 输出：2D 字符串表格（坐标信息丢失）
  │
  ├─ 通道 B: liteparse Grid Projection
  │     ├─ RegionDetector：10×10 密度网格 + 财务关键词预筛 → table_regions
  │     └─ 输出：word级坐标 text_items + table_regions（精确空间信息）
  │
  └─ 交叉验证层 → Table Validator
        ├─ table_classifier：4条件AND（数值列≥70% + 目录排除 + 数据行≥3 + 列≥2）
        ├─ rule_based_repair（109KB）：自底向上分层表头修复、锚定列校正、孤儿文本归并
        ├─ liteparse_table_segmenter（192KB）：区域切分 + 跨页拼接 + 多表混合拆分
        └─ llm_table_repair：DeepSeek/豆包，语义修复（仅规则无法处理时）
```

### 1.2 优势评估

| # | 能力 | 实现方式 | 业界对比 |
|---|------|----------|----------|
| 1 | **真假表格分类** | 4条件AND规则分类器 | 业界唯一，MinerU/Docling 无此层 |
| 2 | **多级表头修复** | 自底向上分层修复 + 锚定列校正 | 领先，SMART 论文思路一致但非工业实现 |
| 3 | **双源融合验证** | pdf2docx + liteparse 交叉验证 | 独特，业界无类似 |
| 4 | **LLM语义修复** | 纯 semantic（合并判断、文本断裂、层级推理） | Prompt 工程领先 |
| 5 | **成本控制** | 规则→LLM 递进，80%问题规则处理 | 最优策略 |
| 6 | **跨页拼接** | 列数一致 + 无表头 + 页码连续 | 成熟 |

### 1.3 核心短板

| # | 短板 | 根因 | 影响等级 |
|---|------|------|:---:|
| 1 | **无内置 OCR** | 依赖 pdf2docx/liteparse 仅能处理文本型PDF | 🔴 高 |
| 2 | **布局感知弱** | RegionDetector 仅有密度网格，无深度学习版面模型 | 🔴 高 |
| 3 | **列边界检测粗糙** | GapDetector 纯启发式（median_gap × 1.5），无表格线信息利用 | 🟡 中 |
| 4 | **合并单元格仅靠LLM** | 无视觉/结构模型识别 merge cell，完全依赖语义推理 | 🟡 中 |
| 5 | **无读取顺序重建** | 多栏文档按物理坐标排列，不符合人类阅读顺序 | 🟡 中 |
| 6 | **仅支持PDF** | 不支持 DOCX/PPTX/XLSX/图片 | 🟢 低 |
| 7 | **无法处理公式** | 年报中财务公式（如 EPS 计算）丢失 | 🟢 低 |
| 8 | **质量评估缺失** | 无表格级的置信度评分和验证闭环 | 🟡 中 |

---

## 2. 行业最佳实践

### 2.1 主流工具核心策略对比

| 能力维度 | MinerU (68.6k ⭐) | Docling (62.1k ⭐) | Marker (36.4k ⭐) | PaddleOCR | **DocuTable 当前** |
|----------|:---:|:---:|:---:|:---:|:---:|
| **PDF文本提取** | 自研引擎 + OCR | 自研引擎 | surya OCR | PP-OCRv6 | pdf2docx/pymupdf |
| **版面检测** | 自研YOLO布局模型 | TableFormer (DETR) | surya layout | PP-DocLayout | 密度网格 |
| **表格结构** | TableStructureRec | TableFormer dual-decoder | heuristics | SLANet (seq prediction) | GapDetector + KMeans |
| **合并单元格** | VLM端到端 | TableFormer cell detection | ❌ | SLANet + relation net | **LLM语义推理** ⭐ |
| **多级表头** | ❌ | ❌ | ❌ | ❌ | **规则分层修复** ⭐ |
| **表格分类** | ❌ | ❌ | ❌ | ❌ | **4条件AND** ⭐ |
| **跨页合并** | 版面检测辅助 | ❌ 需后处理 | ❌ | ❌ | **启发式** |
| **LLM集成** | VLM引擎（全量） | Granite-Docling VLM | Gemini/Ollama可选 | ❌ | **规则后LLM兜底** ⭐ |
| **OCR语言** | 109种 | 多语言 | 90+ | 80+ | 依赖外部 |
| **输出格式** | MD/JSON/HTML | MD/HTML/JSON/DocTags | MD/JSON/HTML | MD/JSON | Excel/JSON |
| **许可证** | Apache 2.0变体 | MIT | GPL-3.0 | Apache 2.0 | 私有 |

### 2.2 可借鉴的关键技术

#### 2.2.1 MinerU 的三引擎架构

```
pipeline（传统流水线）
  Layout Detection → Classification → OCR → Structure Reconstruction
  特点：快速稳定，CPU可运行，无幻觉

vlm-engine（端到端VLM）
  MinerU2.5-Pro 直接"看"文档 → 输出结构化 Markdown
  特点：最高精度，处理复杂版面

hybrid-engine（混合引擎）
  原生文本提取 + VLM增强（effort 参数调节）
  特点：平衡速度与精度，低幻觉
```

**借鉴意义**：DocuTable 可引入 `hybrid` 概念——文本型PDF走规则管线，图片型PDF走OCR+规则，复杂表格走VLM/LLM。

#### 2.2.2 Docling 的 TableFormer

```
TableFormer（CVPR 2022，IBM）
  ├─ Encoder: CNN + Transformer 编码器
  ├─ Decoder-1: 单元格检测解码器（输出 bbox + 内容）
  └─ Decoder-2: 结构解码器（输出 HTML 行列关系）
  
  特点：
  - 端到端，同时输出单元格边界和结构
  - 无需额外OCR（直接从PDF源码提取）
  - 支持合并单元格（spanning cells）
```

**借鉴意义**：可替换 GapDetector，提升复杂表格（无线表、合并单元格）的结构识别精度。

#### 2.2.3 Granite-Docling-258M（2025年9月，IBM）

```
端到端 VLM 文档转换模型（258M 参数）
  - 替换传统 OCR + Layout 级联管线
  - 单模型完成：文字识别 + 布局理解 + 表格结构 + 阅读顺序
  - 极轻量（258M），可本地部署
```

**借鉴意义**：作为 LLM 修复层的低成本替代方案，尤其适合本地/离线场景。

#### 2.2.4 PaddleOCR 的 SLANet

```
SLANet（PP-StructureV2/V3 表格识别核心）
  ├─ Backbone: PP-LCNet（CPU友好）
  ├─ Feature Fusion: CSP-PAN（轻量高低层融合）
  └─ Head: 序列预测（HTML 结构序列）
  
  特点：
  - CPU可运行，极轻量
  - 输出 HTML：<table><tr><td colspan="2">...</td></tr></table>
  - 内置行列合并单元格识别
  - RapidTable 已将模型转为 ONNX
```

**借鉴意义**：如果决定引入深度学习表格结构识别，SLANet/ONNX 是最轻量的选择（CPU可运行，无需GPU）。

#### 2.2.5 Marker 的"选择性模型调用"策略

```
按需调用深度学习模型（非全线串行）：
  ├─ 文本型PDF → 直接提取文本（跳过OCR）
  ├─ 扫描件PDF → surya OCR
  ├─ 简单表格 → 启发式规则
  └─ 复杂表格 → 深度学习模型
```

**借鉴意义**：DocuTable 应按文档类型差异化处理管线，避免对所有文档"一视同仁"。

#### 2.2.6 学术前沿：多级表头处理

```
SMART（ACL 2025）：
  Semantic Header Flattening → 多级表头 → 单级 Markdown 表头
  核心思路：LLM理解层级语义后展平

RealHiTBench（ACL 2025）：
  包含嵌套子表、多级表头的基准测试集

ST-Raptor（SIGMOD 2026，清华）：
  Hierarchical Orthogonal Tree (HO-Tree)
  用树结构建模半结构化表格的多级表头
```

**借鉴意义**：DocuTable 的 rule_based_repair 分层修复思路与 SMART 一致，可参考其树结构建模增强。

#### 2.2.7 XY-Cut++ 阅读顺序算法

```
XY-Cut++（arXiv 2025.04）：
  - 传统 XY-Cut 的问题：假设连通性 → 复杂版面失败
  - 增强：pre-mask处理 + 多粒度分割 + 跨模态匹配
  - 准确率提升 15%+
```

**借鉴意义**：卫报/多栏年报需要正确阅读顺序，尤其表格前后的叙述文本。

---

## 3. V2 总体架构

### 3.1 设计思想

```
                     ┌──────────────────────────────────────────┐
                     │         核心理念：分级精准解析              │
                     │                                          │
                     │  简单文档 ──→ 规则管线（快速，零成本）      │
                     │  中等文档 ──→ 规则 + 模型（精度优先）      │
                     │  复杂文档 ──→ 规则 + 模型 + LLM（兜底）    │
                     │                                          │
                     │  每层独立可替换，管道路由按文档类型决定     │
                     └──────────────────────────────────────────┘
```

### 3.2 五层管道架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Layer 0: 文档预处理                           │
│                                                                     │
│  PDF 输入                                                           │
│    ├─ 文档类型识别（文本型 / 扫描件 / 混合型）                       │
│    ├─ 页面分类（封面/目录/正文/附录）                                │
│    ├─ 语言检测（中文/英文/中英混合）                                 │
│    └─ 质量评估（DPI、倾斜度、噪声水平）                              │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Layer 1: 多通道文本提取                           │
│                                                                     │
│  文本型PDF                   扫描件/图片型PDF                        │
│  ├─ 通道A: PyMuPDF            ├─ 渲染为图像                          │
│  │   get_text("words")        ├─ 通道C: PaddleOCR / surya OCR       │
│  │   (word级坐标)             │   (文字行 + 坐标)                    │
│  └─ 通道B: liteparse          └─ 通道D: MinerU pipeline              │
│      Grid Projection              (段落 + 布局)                      │
│      (text_items)                                                   │
│                                                                     │
│  输出：统一 TextItem 格式 {text, x0,y0,x1,y1, page, source, conf}    │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Layer 2: 版面分析与表格定位                       │
│                                                                     │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐ │
│  │ 布局检测          │   │ 表格区域定位      │   │ 阅读顺序重建      │ │
│  │                  │   │                  │   │                  │ │
│  │ 深度学习:        │   │ 规则:            │   │ XY-Cut++:        │ │
│  │  · YOLO布局模型  │   │  · RegionDetector │   │  · 多粒度分割    │ │
│  │  · 11类检测      │   │    (密度网格)     │   │  · 层级遮罩      │ │
│  │                  │   │  · 财务关键词      │   │  · 跨栏匹配      │ │
│  │ 轻量级:          │   │                  │   │                  │ │
│  │  · ONNX轻量版    │   │ 模型:            │   │ 输出：逻辑顺序    │ │
│  │  · 5类即可       │   │  · TableFormer    │   │ 段落 + 表格      │ │
│  │                  │   │    (DETR)         │   │                  │ │
│  └──────────────────┘   └──────────────────┘   └──────────────────┘ │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Layer 3: 表格结构重建                             │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  3a. 列结构推断                                             │    │
│  │  · 表格线感知（有线条表）→ 直接按线切分                      │    │
│  │  · 无线表格 → GapDetector (自适应中位gap) + KMeans          │    │
│  │  · 可选增强: SLANet/TableFormer 序列预测                    │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  3b. 行聚合与单元格归位                                     │    │
│  │  · Y聚类 (liteparse cell_differ 原有逻辑)                  │    │
│  │  · 小数合并 (_merge_split_decimals)                         │    │
│  │  · 多值行拆分 (_split_multi_value_rows)                     │    │
│  │  · 列签名校验 → 错位校正                                    │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  3c. 合并单元格恢复 ⭐ 新增                                   │    │
│  │  · 表格线感知（视觉线索）                                     │    │
│  │  · 文本重复模式检测（相邻格内容相同 → 合并）                  │    │
│  │  · 可选: Formerge Transformer 模型                           │    │
│  │  · LLM确认（复杂情况）                                       │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  3d. 表格分类 ⭐ DocuTable 核心优势                           │    │
│  │  · 真假表格判定（4条件AND → 可配置权重版）                    │    │
│  │  · 目录页排除（原有逻辑增强）                                 │    │
│  │  · 图表/文本误判过滤                                         │    │
│  │  · 财务表格 vs 文本表格分类                                   │    │
│  └─────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Layer 4: 语义理解与修复                           │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  4a. 规则修复层（零成本基线）⭐ DocuTable 核心优势             │    │
│  │  · 表头定位与分层 (_find_header_rows)                        │    │
│  │  · 自底向上修复 (_repair_bottom_up)                          │    │
│  │  · 锚定列校正 (_repair_data_by_anchor)                       │    │
│  │  · 孤儿文本归并 (_merge_orphan_texts)                        │    │
│  │  · 异常标记 (RepairAnomaly 模型，置信度标注)                  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  4b. 跨页拼接                                               │    │
│  │  · 方法1: liteparse region 跨页检测（列结构一致）           │    │
│  │  · 方法2: 内容交叉验证（前页尾部 == 后页头部）                │    │
│  │  · 方法3: LLM判断（前两种方法不确定时）                      │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  4c. LLM 语义修复层（最后防线）                               │    │
│  │  · 仅处理规则无法解决的异常（anomaly confidence < 阈值）     │    │
│  │  · 多级表头层级推理（"资产 → 流动资产 → 货币资金"）          │    │
│  │  · 文本断裂合并（语义完整性检测）                             │    │
│  │  · 合并不确定单元格（规则遗漏的 merge cell）                  │    │
│  │  · 可选本地模型: Granite-Docling-258M（离线+低成本）          │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  4d. 质量评估与置信度 ⭐ 新增                                  │    │
│  │  · 表格级置信度评分（结构完整性 + 内容一致性 + 财务特征）     │    │
│  │  · 单元格级置信度（OCR/text提取源的可信度）                   │    │
│  │  · 可溯源性标注（每个cell的来源通道、修复操作链）             │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. 分层技术方案

### Layer 0：文档预处理与类型识别

#### 4.0.1 文档类型路由

```python
class DocumentRouter:
    """基于文档类型选择最优解析通道"""

    def route(self, pdf_path) -> PipelineConfig:
        doc_type = self._classify_document(pdf_path)
        return PIPELINE_ROUTES[doc_type]

    def _classify_document(self, pdf_path) -> DocumentType:
        # 1. 采样前5-10页，分析文本/图片比例
        text_ratio = self._analyze_text_ratio(pdf_path, sample_pages=10)

        # 2. 图片型判定：text_ratio < 0.1 → IMAGE_PDF
        if text_ratio < 0.1:
            return DocumentType.IMAGE_PDF

        # 3. 混合型判定：0.1 ≤ text_ratio < 0.6 → HYBRID_PDF
        if text_ratio < 0.6:
            return DocumentType.HYBRID_PDF

        # 4. 文本型判定（原有 is_image_pdf 逻辑增强）
        return DocumentType.TEXT_PDF

# 管线路由表
PIPELINE_ROUTES = {
    DocumentType.TEXT_PDF:     PipelineConfig(ocr=None, layout="density_grid", table="gap+kmeans"),
    DocumentType.IMAGE_PDF:    PipelineConfig(ocr="paddleocr", layout="yolo_detector", table="slanet"),
    DocumentType.HYBRID_PDF:   PipelineConfig(ocr="paddleocr_per_page", layout="yolo_detector", table="gap+kmeans+slanet"),
}
```

**增强点**：
- 将现有的 `is_image_pdf()` 从二分类（是/否）升级为三分类（文本/图片/混合）
- 混合型文档按页决策：文本页走规则通道，图片页走OCR通道

#### 4.0.2 页面角色分类

```python
class PageRoleClassifier:
    """页面角色分类：封面 / 目录 / 正文 / 附录"""

    def classify(self, page_text, page_num, total_pages) -> PageRole:
        # 封面: 第1页，文本稀疏，含"年报"/"报告"关键词
        # 目录: 含"目录"/"CONTENTS" + 高比例点线/页码模式
        # 正文: 中间页，含财务关键词+数据表格
        # 附录: 靠后页，含"附注"/"审计报告"关键词
        pass
```

**增强点**：将 table_classifier 中的目录排除逻辑前置到页面级，减少后续表格检测的误判。

---

### Layer 1：多通道文本提取

#### 4.1.1 文本型PDF三层提取策略

```python
class TextPDFExtractor:
    """文本型PDF多引擎并行提取 + 融合"""

    def extract(self, pdf_path, page_num) -> List[TextItem]:
        results = []

        # 引擎1: PyMuPDF words（word级坐标，最精细）
        pymupdf_items = self._extract_pymupdf_words(pdf_path, page_num)
        results.append(("pymupdf", pymupdf_items))

        # 引擎2: liteparse Grid Projection（区域感知，布局意识最强）
        liteparse_items = self._extract_liteparse(pdf_path, page_num)
        results.append(("liteparse", liteparse_items))

        # 引擎3: pdfplumber（表格检测最强）
        pdfplumber_items = self._extract_pdfplumber(pdf_path, page_num)
        results.append(("pdfplumber", pdfplumber_items))

        # 融合策略：liteparse 为主坐标源，pymupdf 补充遗漏，pdfplumber 提供表格框
        return self._fuse_items(results, strategy="liteparse_primary")

    def _fuse_items(self, results, strategy):
        """多源文本融合
        - liteparse 的区域感知布局作为主坐标源（保留阅读顺序）
        - pymupdf 的 word 级坐标补充 liteparse 遗漏的小文本
        - pdfplumber 提取的完整表格框作为验证参考
        """
        pass
```

**增强点**：
- 当前是 pdf2docx → pdfplumber → PyMuPDF 串行 fallback，改为**并行提取+融合**
- liteparse 升级为主坐标源（保留阅读顺序和区域上下文）
- pymupdf words 模式补充遗漏（原 V2 已用 `get_text("words")`）

#### 4.1.2 扫描件/图片型PDF的OCR通道

```python
class ImagePDFExtractor:
    """扫描件PDF的OCR提取通道"""

    # 策略A: PaddleOCR（离线，支持中英文，PP-OCRv6）
    # 策略B: surya OCR（650M参数，90+语言，精度更高）
    # 策略C: MinerU pipeline（包含完整布局→OCR→表格管线）

    def extract_with_ocr(self, pdf_path, page_range, method="paddleocr"):
        # 1. 渲染PDF页面为图像（dpi=200）
        images = self._render_pages(pdf_path, page_range)

        # 2. OCR 识别（按页并行）
        ocr_results = self._ocr_engine.recognize_batch(images)

        # 3. 转换为统一的 TextItem 格式
        return [self._to_text_item(r) for r in ocr_results]
```

**推荐策略**：
- **首选 PaddleOCR**：离线可用，中文优化最好，Apache 2.0许可证
- **可选 surya**：需要更高精度时，90+语言
- **兜底 MinerU**：需要完整版面理解时（如多栏学术文档）

#### 4.1.3 统一 TextItem 格式

```python
@dataclass
class TextItem:
    """统一的文本项数据结构（所有通道输出归一化）"""
    text: str
    x0: float; y0: float; x1: float; y1: float
    page: int
    source: str           # "pymupdf" | "liteparse" | "pdfplumber" | "paddleocr"
    confidence: float     # 提取置信度 0.0-1.0
    font_size: float = 0  # 字号（用于表头/数据行区分）
    is_bold: bool = False # 是否粗体（用于表头识别）
    block_type: str = ""  # "text" | "table_cell" | "header" | "footer"
```

---

### Layer 2：版面分析与表格定位

#### 4.2.1 双层版面分析架构

```python
class HybridLayoutAnalyzer:
    """双层版面分析：规则快速通道 + 模型精确通道"""

    def __init__(self, use_dl=False):
        # 规则通道：RegionDetector（现有，10×10密度网格）
        self.rule_detector = RegionDetector()

        # 模型通道：轻量YOLO布局模型（新增，可选）
        self.dl_detector = None
        if use_dl:
            self.dl_detector = LayoutDetector_ONNX()
            # 使用导出为 ONNX 的轻量布局模型
            # 检测 5 类：table / text / title / image / header_footer

    def analyze(self, page) -> PageLayout:
        """双层检测 + 结果融合"""
        # 1. 规则快速通道（始终运行，零成本）
        rule_regions = self.rule_detector.detect(page)

        # 2. 模型通道（可选，提升精度）
        dl_regions = None
        if self.dl_detector:
            dl_regions = self.dl_detector.detect(page)

        # 3. 融合：DL 结果优先，规则结果作为补充
        return self._fuse_regions(rule_regions, dl_regions)
```

**增强点**：
- 保留 RegionDetector 作为零成本基线
- 可插拔的 ONNX 布局模型（5类即可，不需要11类）
- DL 结果为主，规则结果补充 DL 可能遗漏的小表格

#### 4.2.2 表格区域定位增强

在现有 RegionDetector 基础上增加：

```python
class EnhancedTableLocator:
    """表格定位增强器"""

    def locate_tables(self, page) -> List[TableRegion]:
        # 1. 表格线检测（PyMuPDF drawings）
        table_lines = self._detect_ruled_tables(page)  # 有线条表 → 高置信度

        # 2. 无线表格检测（现有密度网格 + 增强）
        borderless_tables = self._detect_borderless_tables(page)
        # 增强点：
        #   - 文本对齐模式检测（多列左对齐 → 疑似表格）
        #   - 数字密度区域（连续行数字比例 > 50% → 数据表）
        #   - 空白行间隔模式（规律性空行 → 表格行间）

        # 3. 合并线条表 + 无线表，去重
        return self._merge_and_deduplicate(table_lines, borderless_tables)
```

#### 4.2.3 阅读顺序重建（新增）

```python
class ReadingOrderRecovery:
    """基于 XY-Cut++ 的阅读顺序恢复"""

    def recover(self, blocks: List[LayoutBlock]) -> List[LayoutBlock]:
        """
        输入：无序的版面块（按物理坐标排列）
        输出：按人类阅读顺序排列的版面块
        """
        # XY-Cut++ 核心改进：
        # 1. Pre-mask：先用版面模型遮罩非正文区域
        # 2. 多层次分割：大粒度→小粒度递归分割
        # 3. 跨栏匹配：上下文感知的多栏匹配
        return self._xy_cut_plus_plus(blocks)
```

**必要性**：年报中常有双栏排版（如财务摘要页），当前 liteparse 按物理 Y 坐标排列，会将左栏文字与右栏表格交错排列。

---

### Layer 3：表格结构重建

#### 4.3.1 表格线感知的列边界检测（增强）

```python
class EnhancedColumnDetector:
    """增强的列检测器：表格线 + Gap + KMeans 三路融合"""

    def detect_columns(self, page, text_items, table_region):
        columns = []

        # 1. 若页面有表格竖线（drawings），优先使用
        vertical_lines = self._extract_vertical_lines(page, table_region)
        if vertical_lines and len(vertical_lines) >= 2:
            columns.append(("line", vertical_lines, 0.95))  # 置信度95%

        # 2. GapDetector（现有，自适应中位gap）
        gap_columns = self.gap_detector.detect(text_items)
        columns.append(("gap", gap_columns, 0.75))

        # 3. KMeans 聚类（现有，轮廓系数选最优k）
        kmeans_columns = self.kmeans_detector.detect(text_items)
        columns.append(("kmeans", kmeans_columns, 0.70))

        # 4. 融合：高置信度方法优先
        return self._fuse_columns(columns)
```

**增强点**：增加表格线检测（PyMuPDF 的 `get_drawings()`），有线表格直接按线切分，100%准确。

#### 4.3.2 合并单元格恢复（新增）

```python
class SpanningCellRecovery:
    """合并单元格恢复：视觉线索 + 文本模式 + LLM确认"""

    def recover(self, table_2d: List[List[str]], text_items: List[TextItem],
                page_drawings=None) -> List[List[CellInfo]]:
        """
        三阶段恢复策略：
        """

        # 阶段1: 表格线检测（最可靠）
        if page_drawings:
            spans = self._detect_from_lines(page_drawings, table_2d)

        # 阶段2: 文本模式检测（无表格线时）
        for col in range(len(table_2d[0])):
            for row in range(len(table_2d)):
                # 检测模式1: 相邻行内容完全相同 → 纵向合并
                if self._is_same_content(table_2d, row, col, row+1, col):
                    spans.append(MergeSpan(row, col, rowspan=2, colsp=1, conf=0.8))

                # 检测模式2: 某单元格为空且相邻格内容跨列 → 横向合并
                if self._is_empty_beside_span(table_2d, row, col):
                    spans.append(MergeSpan(row, col, rowspan=1, colsp=2, conf=0.6))

        # 阶段3: LLM语义确认（仅前两阶段不确定的候选）
        uncertain = [s for s in spans if s.confidence < 0.7]
        if uncertain:
            spans = self._llm_verify_spans(uncertain, table_2d)

        return self._apply_spans(table_2d, spans)
```

#### 4.3.3 增强的表格分类器

```python
class EnhancedTableClassifier:
    """增强的表格分类器：可配置权重版"""

    def classify(self, table_2d, context) -> ClassifyResult:
        """
        从原有 4条件AND 升级为 加权评分版：
        """
        score = 0.0
        details = {}

        # 条件1: 数值列比例（权重 0.30）
        num_col_ratio = self._calc_numeric_column_ratio(table_2d)
        score += min(num_col_ratio / 0.7, 1.0) * 0.30
        details['numeric_ratio'] = num_col_ratio

        # 条件2: 数据行数量（权重 0.20）
        data_rows = self._count_data_rows(table_2d)
        score += min(data_rows / 5, 1.0) * 0.20
        details['data_rows'] = data_rows

        # 条件3: 列数（权重 0.15）
        cols = len(table_2d[0]) if table_2d else 0
        score += min(cols / 3, 1.0) * 0.15
        details['columns'] = cols

        # 条件4: 目录页排除（反向权重 0.15）
        is_toc = self._is_table_of_contents(context)
        score += (0.0 if is_toc else 1.0) * 0.15
        details['is_toc'] = is_toc

        # 条件5: 表头结构质量（权重 0.20，新增）
        header_score = self._evaluate_header_structure(table_2d)
        score += header_score * 0.20
        details['header_quality'] = header_score

        # 分类决策（可配置阈值）
        if score >= 0.65:
            return ClassifyResult(is_table=True, confidence=score, details=details)
        elif score >= 0.40:
            return ClassifyResult(is_table=True, confidence=score,
                                  needs_review=True, details=details)
        else:
            return ClassifyResult(is_table=False, confidence=score, details=details)
```

**增强点**：
- 从硬性 AND 改为加权评分，避免一刀切
- 新增表头结构质量评估（是否存在有意义的行列标签）
- 引入 `needs_review` 中间状态（低置信度时标记需人工复核）

#### 4.3.4 可选的深度学习表格结构模型

```python
class DLTableStructureRecognizer:
    """深度学习表格结构识别（可选增强）"""

    def __init__(self, model="slanet_onnx"):
        # SLANet ONNX 版：CPU 友好，输出 HTML 结构
        # 或 TableFormer：精度更高，需要 GPU
        self.model = self._load_model(model)

    def recognize(self, table_image) -> str:
        """输入表格区域图像 → 输出 HTML 结构"""
        html = self.model.predict(table_image)
        # <table>
        #   <tr><td colspan="2">流动资产</td><td>2024</td><td>2023</td></tr>
        #   <tr><td>货币资金</td><td>100</td><td>200</td><td>150</td></tr>
        # </table>
        return html
```

**使用策略**：仅在无线表格且 GapDetector+KMeans 冲突时启用（条件触发，非全量调用）。

---

### Layer 4：语义理解与修复

#### 4.4.1 增强的规则修复器

在现有的 `rule_based_repair.py` 基础上：

```python
class EnhancedRuleBasedRepairer(RuleBasedRepair):
    """增强的规则修复器：V1 已完成，V2 增加以下能力"""

    # === V2 新增能力 ===

    def _repair_hierarchical_header_v2(self, header_rows, data_rows):
        """
        多级表头分层增强（借鉴 SMART 思路）：
        1. 识别表头层级关系：
           - 大类别（资产/负债/权益）→ 层1
           - 中类别（流动资产/非流动资产）→ 层2
           - 细分类别（货币资金/应收账款）→ 层3
        2. 构建表头树结构
        3. 空缺填充（父节点值向下传递到子节点空位）
        4. 列归属修正（底层表头列数 = 数据列数）
        """
        pass

    def _detect_mixed_tables_v2(self, all_tables):
        """
        混合表拆分增强：
        1. 检测单页内多独立表格的边界
        2. 标题行识别（"表X"、"项目"、"单位：元"）
        3. 数据区切换检测（数值列布局突变）
        """
        pass

    def _recover_missing_headers_v2(self, table):
        """
        缺失表头恢复增强：
        1. 使用上下文（caption、前置段落）推断
        2. 使用同文档其他表格的列名做参考
        3. LLM 辅助（可选，仅置信度低时）
        """
        pass
```

#### 4.4.2 LLM 修复层优化

```python
class OptimizedLLMRepairer:
    """优化后的大模型修复器"""

    # === V2 优化点 ===

    def should_invoke_llm(self, anomaly: RepairAnomaly) -> bool:
        """智能决策：是否需要调用LLM"""
        # 1. 规则自行解决的高置信度异常 → 跳过
        if anomaly.confidence > 0.8:
            return False

        # 2. 纯位置问题（如空白列插入）→ 规则足够
        if anomaly.type in [ANCHOR_SHIFT, WEAK_ANCHOR]:
            return False

        # 3. 语义问题（文本合并、层级推理）→ 需要LLM
        if anomaly.type in [TRUNCATED_HEADER_MERGED,
                            HEADER_TEXT_MISSING]:
            return True

        # 4. 中等置信度 + 语义相关 → 需要LLM
        return anomaly.confidence < 0.6 and anomaly.severity != ANOMALY_LOW

    def repair_with_llm(self, anomaly, table_context):
        """LLM修复（支持远程API和本地模型）"""
        if self.config.use_local_model:
            # 本地 Granite-Docling-258M（离线，零API成本）
            return self._repair_with_granite_docling(anomaly, table_context)
        else:
            # 远程 DeepSeek/豆包（精度更高）
            return self._repair_with_remote_llm(anomaly, table_context)

    def _repair_with_granite_docling(self, anomaly, table_context):
        """
        本地模型修复流程（新增可选通道）：
        1. 构造多模态输入（表格图像 + 异常描述）
        2. Granite-Docling-258M 推理
        3. 提取修复后的结构
        优势：离线可用、零API成本、速度更快
        局限：精度略低于大模型
        """
        pass
```

**增强点**：
- 智能路由：低复杂度异常不调LLM（节省API成本）
- 本地模型选项：Granite-Docling-258M 作为离线备选
- 批处理优化：同文档多个异常批量提交LLM

#### 4.4.3 质量评估与置信度（新增）

```python
class TableQualityAssessor:
    """表格质量评估与置信度评分"""

    def assess(self, table: StructuredTable) -> QualityReport:
        scores = {}

        # 1. 结构完整性（40%）
        scores['structure'] = self._score_structure(table)
        # - 列数一致性（跨行波动 < 10%）
        # - 表头覆盖率（每个数据列都有表头说明）
        # - 单元格填充率（非空率 > 60%）

        # 2. 内容一致性（30%）
        scores['content'] = self._score_content(table)
        # - 数值格式一致性（同列数字格式统一）
        # - 单位一致性（金额单位不混用）
        # - 文本不重复率

        # 3. 财务特征匹配（20%）
        scores['financial'] = self._score_financial_features(table)
        # - 财务关键词命中率
        # - 典型财务报表模式匹配（资产=负债+权益等）

        # 4. 提取源可信度（10%）
        scores['source'] = self._score_source_reliability(table)
        # - 文本型PDF提取 > OCR提取
        # - 有表格线 > 无线表格
        # - PyMuPDF words > pdfplumber > OCR

        overall = self._weighted_sum(scores)
        return QualityReport(
            overall_score=overall,
            scores=scores,
            grade=self._to_grade(overall),  # A/B/C/D/E
            issues=self._collect_issues(table),
            suggestions=self._generate_suggestions(table),
        )

    def _score_source_reliability(self, table):
        """单元格级别的来源可信度"""
        cell_confidences = []
        for row in table.rows:
            for cell in row.cells:
                source = cell.metadata.get('source', 'unknown')
                if source == 'pymupdf_words':
                    cell_confidences.append(0.95)
                elif source == 'liteparse':
                    cell_confidences.append(0.90)
                elif source == 'pdfplumber':
                    cell_confidences.append(0.85)
                elif source in ('paddleocr', 'surya'):
                    cell_confidences.append(0.75)
                else:
                    cell_confidences.append(0.50)
        return sum(cell_confidences) / len(cell_confidences) if cell_confidences else 0.0
```

---

## 5. 质量评估体系

### 5.1 评估维度

| 维度 | 指标 | 计算方式 | 目标阈值 |
|------|------|----------|:---:|
| **结构准确率** | 列数一致性 | 各行列数标准差 / 平均列数 | < 5% |
| **结构准确率** | 表头覆盖率 | 有表头的列 / 总数据列 | > 95% |
| **内容准确率** | 数值正确率 | 匹配验证集的单元格数 / 总单元格数 | > 98% |
| **语义准确率** | 层级正确率 | 多级表头层级关系正确数 / 总层级数 | > 95% |
| **效率指标** | 单页处理时间 | 平均单页耗时（不含LLM） | < 2秒 |
| **成本指标** | LLM调用率 | 需LLM修复的表格 / 总表格 | < 15% |

### 5.2 测试集设计

```
data/test_bench/
├── simple/           # 简单有线表格（20份）
├── borderless/         # 无线表格（20份）
├── multi_header/       # 多级表头（15份）
├── merged_cells/       # 合并单元格（15份）
├── cross_page/         # 跨页续表（10份）
├── mixed_layout/       # 混合版面（10份）
├── scanned/            # 扫描件（10份）
└── negative/           # 非表格页面（20份，测试误判率）
```

---

## 6. 实施路线图

### Phase 1：基础设施升级（1-2周）

| 任务 | 描述 | 依赖 |
|------|------|------|
| 1.1 TextItem 标准化 | 定义统一 TextItem 格式，改造所有通道输出 | 无 |
| 1.2 文档路由 | 实现 `DocumentRouter`，三分类 + 按页决策 | 无 |
| 1.3 并行提取 | 将串行 fallback 改为并行多通道 | 1.1 |
| 1.4 质量评估器 | 实现 `TableQualityAssessor` 核心评分逻辑 | 1.1 |

### Phase 2：核心解析增强（2-3周）

| 任务 | 描述 | 依赖 |
|------|------|------|
| 2.1 表格线感知 | PyMuPDF `get_drawings()` 提取表格线 + 按线切分 | 1.1 |
| 2.2 阅读顺序 | XY-Cut++ 实现 + 多栏布局处理 | 1.3 |
| 2.3 合并单元格 | 视觉+文本模式合并单元格恢复 | 2.1 |
| 2.4 分类器升级 | AND→加权评分，needs_review 中间状态 | 1.4 |

### Phase 3：AI增强集成（2-3周）

| 任务 | 描述 | 依赖 |
|------|------|------|
| 3.1 OCR集成 | 接入 PaddleOCR，扫描件/图片型PDF通道 | 1.3 |
| 3.2 布局模型 | ONNX 轻量布局检测模型集成 | 1.3 |
| 3.3 LLM优化 | 智能路由 + 本地模型选项 + 批量提交 | Phase 2 |
| 3.4 可选表格模型 | SLANet ONNX 集成（条件触发） | 2.1 |

### Phase 4：测试与优化（1-2周）

| 任务 | 描述 |
|------|------|
| 4.1 测试集构建 | 100+ 份银行年报样本的 ground truth |
| 4.2 基准测试 | 各维度准确率、速度、成本的全面基准 |
| 4.3 调优 | 基于基准测试结果调整各层参数 |

---

## 附录A：技术选型对比

### A.1 OCR引擎选择

| 引擎 | 精度 | 速度(CPU) | 中文 | 部署 | 许可证 | 推荐 |
|------|:---:|:---:|:---:|------|------|:---:|
| PaddleOCR v6 | 92% | 快 | ✅ 最优 | pip install | Apache 2.0 | ⭐ 首选 |
| surya OCR | 94% | 慢 | ✅ | pip install | GPL-3.0 | 备选 |
| Tesseract 5 | 82% | 快 | 🔶 | 系统安装 | Apache 2.0 | 不推荐 |
| EasyOCR | 85% | 慢 | ✅ | pip install | Apache 2.0 | 不推荐 |

### A.2 布局检测模型

| 模型 | 精度 | 大小 | 推理 | 许可证 |
|------|:---:|:---:|------|------|
| doclayout_yolo | 高 | ~50MB | GPU | AGPL-3.0 |
| YOLOv8n-doc | 中 | ~6MB | CPU可 | GPL-3.0 |
| **YOLOv11n 自训练** | 中高 | ~5MB | CPU可 | 自定义 | ⭐ 推荐 |

### A.3 表格结构模型

| 模型 | 有线条 | 无线条 | 合并格 | 速度 | 部署 |
|------|:---:|:---:|:---:|:---:|------|
| SLANet (ONNX) | ✅ | ✅ | ✅ | 快 | CPU | ⭐ 推荐 |
| TableFormer | ✅ | ✅ | ✅ | 中 | GPU |
| RapidTable | ✅ | ✅ | 🔶 | 快 | CPU | 备选 |
| 纯规则 (当前) | ✅ | 🔶 | ❌ | 极快 | CPU |

### A.4 LLM选择

| 模型 | 精度 | 成本 | 速度 | 离线 | 适用场景 |
|------|:---:|:---:|:---:|:---:|------|
| DeepSeek V3 | ⭐⭐⭐⭐⭐ | 💰💰 | 中 | ❌ | 复杂语义修复 |
| 豆包/千问 | ⭐⭐⭐⭐ | 💰 | 快 | ❌ | 常规修复 |
| Granite-Docling-258M | ⭐⭐⭐ | 🆓 | 快 | ✅ | 离线场景 |
| Qwen2.5-7B 本地 | ⭐⭐⭐ | 🆓 | 慢 | ✅ | 隐私敏感 |

---

## 附录B：关键依赖清单

```
# requirements_v2.txt

# === 核心依赖（已有） ===
pymupdf>=1.24.0          # PDF 文本/图片/绘图提取
pdfplumber>=0.10.0       # 表格检测
openpyxl>=3.1.0          # Excel 导出
scikit-learn>=1.3.0      # KMeans 列聚类，轮廓系数

# === 新增：OCR ===
paddleocr>=2.9.0         # PP-OCRv6，中文OCR

# === 新增：布局检测（可选） ===
# onnxruntime>=1.17.0    # ONNX 模型推理
# opencv-python>=4.9.0   # 图像预处理

# === 新增：可视化与调试 ===
matplotlib>=3.8.0        # 版面可视化

# === 可选：本地LLM ===
# transformers>=4.45.0   # Granite-Docling 模型加载
# torch>=2.4.0           # PyTorch 推理
```

---

> **版本**: V2.0 草案  
> **日期**: 2026-06-24  
> **设计原则总结**: 规则先行（零成本基线）→ 模型增强（精度提升）→ LLM兜底（语义修复），每层独立可替换，管道路由按文档类型差异化。
