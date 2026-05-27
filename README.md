# DocuTable — 银行年报 PDF 表格提取工具

> 桌面端 AI 辅助 PDF 财务表格提取工具，基于 PyQt5 + PyMuPDF，专为金融分析师设计。

自动从银行年报 PDF 中提取结构化表格，支持表格提取、AI 纠错、对比预览、单元格编辑，一键导出 Excel。

[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/PyQt5-5.15-blue)](https://pypi.org/project/PyQt5/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 核心功能

### 📄 多引擎 PDF 表格提取
- **双引擎并行**：PyMuPDF + pdfplumber 同时提取，自动融合最优结果
- **V2 增强算法**：多信号融合策略（文字坐标 + 表格线条 + 字体特征），不依赖单一信号
- **渐进式降级**：精确方法优先，逐步降级确保不丢数据
- **自动类型检测**：识别文本型 PDF / 扫描件，分别采用不同策略

### 🤖 四层 AI 纠错引擎（核心亮点）

独创的四层递增式纠错管线，对提取结果进行深度优化：

| 层级 | 名称 | 作用 | API 成本 |
|:---:|------|------|:---:|
| L1 | **规则预检** | 10 项规则自动检测（空行、列不对齐、数值格式等） | 免费 |
| L2 | **规则自动修复** | 确定性规则自动修正（删除空行、统一数值格式等） | 免费 |
| L3 | **LLM 深度分析** | AI 命名表格、识别层级关系、判断区域完整性 | 需 API |
| L4 | **数值交叉验证** | 验证合计行与子项之和是否一致 | 免费 |

LLM 纠错产出：每张表的**规范名称**、**层级结构**（表头/分类/小计/总计）、**区域判断**（合并/拆分建议）、**数值修正**，并附置信度标记。

### 👁 对比预览与编辑
- **左右分栏**：PDF 原页面 ↔ 提取的表格数据，实时对比
- **PDF 预览**：支持缩放（Ctrl+滚轮）、拖拽，自动定位到当前表格页码
- **单元格编辑**：双击直接修改提取错误的单元格
- **撤销/重做**：完整编辑历史（最多 100 步）
- **筛选导航**：按页面、提取器类型、关键字筛选，快速定位目标表格

### 📊 导出
- 一键导出为格式化 Excel（`.xlsx`），含表头加粗、边框、自动列宽
- 支持多 Sheet 导出（每页一个 Sheet）

---

## 工作流程

```
选择 PDF → 自动检测类型 → 多引擎提取表格 → 对比预览核对 → AI 纠错优化 → 导出 Excel
```

---

## 界面概览

### 1. 🔄 处理 — PDF 自动解析
选择 PDF → 配置提取参数 → 后台解析 → 实时进度反馈。

![PDF Automatic Parsing](screenshots/1_PDF自动解析.png)

### 2. 👁 对比预览 — 数据核对与编辑
左 PDF 原页面，右提取表格，逐页对照，双击编辑单元格。

![Data Comparison Preview](screenshots/2_数据对比预览.png)

### 3. 🔍 AI 优化 — AI 纠错审核
查看 L1-L4 的纠错结果，按置信度接受/拒绝修正，支持 Prompt 预览编辑。

### 4. 📜 历史 — 解析历史记录
管理历史任务，按状态筛选，一键重新加载或清理。

![Parsing History](screenshots/3_解析历史记录.png)

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python main.py
```

> **提示**：如需使用 AI 纠错功能，请先在「⚙️ 配置」Tab 中填写 DeepSeek 或豆包 API Key。无 Key 时仍可使用规则预检与自动修复。

---

## 技术栈

| 组件 | 技术 | 用途 |
|------|------|------|
| GUI | PyQt5 | 桌面界面 |
| PDF 解析 | PyMuPDF (fitz) + pdfplumber | 文字提取 + 线条检测 |
| 列聚类 | numpy + scikit-learn | K-Means 列边界分析 |
| Excel | openpyxl | 格式化导出 |
| AI 后端 | DeepSeek / 豆包 API | LLM 深度纠错 |
| 文档转换 | pdf2docx + python-docx | 降级通道 |

---

## 项目结构

```
DocuTable/
├── main.py                      # 入口
├── codes/
│   ├── core/                    # 核心提取引擎
│   │   ├── extractor.py         #   基础表格提取（pdfplumber + PyMuPDF）
│   │   ├── table_processor.py   #   表格结构重建（间隙检测、聚类）
│   │   ├── gap_detector.py      #   自适应列边界检测
│   │   ├── column_analyzer.py   #   K-Means 聚类列分析
│   │   └── exporter.py          #   Excel/JSON 导出
│   ├── pdf_extractor/           # PDF 提取 + AI 纠错
│   │   ├── processor.py         #   V2 提取算法 + VisionLLM + ProcessingWorker
│   │   ├── ai_correction.py     #   四层 AI 纠错引擎（L1→L2→L3→L4）
│   │   ├── widgets.py           #   PDF 预览控件
│   │   ├── utils.py             #   配置、缓存、历史管理
│   │   └── pdf_context.py       #   PDF 共享上下文
│   └── ui/                      # 界面层
│       ├── main_window.py       #   主窗口（5 个 Tab）
│       ├── processing_manager.py #   PDF 处理流程管理
│       ├── table_compare_manager.py # 表格对比预览（最大模块）
│       ├── preview_manager.py   #   PDF 预览渲染
│       ├── ai_correction_dialog.py  # AI 纠错交互界面
│       ├── history_manager.py   #   历史记录
│       ├── export_manager.py    #   导出流程
│       ├── file_manager.py      #   缓存管理
│       ├── settings_manager.py  #   API Key 配置
│       └── table_editor.py      #   单元格编辑
├── build.txt                    # PyInstaller 打包说明
├── requirements.txt
└── screenshots/
```

---

## 系统要求

- Python 3.8+
- PyQt5 ≥ 5.15
- PyMuPDF ≥ 1.23
- openpyxl ≥ 3.1
