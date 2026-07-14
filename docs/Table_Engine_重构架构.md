# Table Engine 重构技术架构

> **版本**: v0.2（评审稿）  
> **日期**: 2026-06-30  
> **第一目标**: 表格重建**精准度**（行列正确、表文分界正确、cell 可溯源）  
> **三条铁律**: ① 以终为始  ② 不为沉没成本买单  ③ 分步实施、步步可验、验过再走  

---

## 目录

0. [三条铁律（必读）](#0-三条铁律必读)  
1. [以终为始：终态定义](#1-以终为始终态定义)  
2. [现状与必须删除什么](#2-现状与必须删除什么)  
3. [目标架构](#3-目标架构)  
4. [核心数据模型](#4-核心数据模型)  
5. [模块划分](#5-模块划分)  
6. [Pipeline 阶段](#6-pipeline-阶段)  
7. [Layout 插件](#7-layout-插件)  
8. [OCR 接口（预留）](#8-ocr-接口预留)  
9. [分步实施计划（步步可验）](#9-分步实施计划步步可验)  
10. [附录](#10-附录)  

---

## 0. 三条铁律（必读）

### 铁律 1 — 以终为始

所有设计从**用户最终拿到的结果**倒推，而不是从「现有代码能改什么」正推。

**终态一句话**：  
对原生 PDF，liteparse 提取的每个词保留坐标 → Table Engine 重建 `StructuredTable` → 导出给 UI 的表格在行列、表头、表文分界上与 PDF 一致，且每个 cell 可反查源坐标。

任何一步若不能服务于终态，不做。

### 铁律 2 — 不为沉没成本买单

| 错误做法 | 正确做法 |
|----------|----------|
| 在 `hybrid_segmenter` 上继续 patch | **删除**建表/分裂主路径，不在其上开发 |
| 「先 wrapper 旧函数再慢慢换」 | **复制** proven 算法到新模块，验过后**删旧文件** |
| 长期双轨 + feature flag 两套并行维护 | 仅在某一步验收窗口做**短期** A/B，通过后**立刻切单轨** |
| 保留 `rule_based_repair`「以防万一」 | 新引擎 export 稳定后**整文件移除** |
| pdf2docx 与 liteparse 双源融合 | native PDF **只用 liteparse + table_engine** |

**保留白名单（仅基础设施，非表重建逻辑）**：

- `liteparse_extractor/` — 坐标来源  
- `content_segmenter/` — region 段切  
- `ui/` — 壳（只读 export 结果）  
- `main.py` / 打包 / 缓存目录约定  

其余表重建相关旧代码：**迁移算法 → 验证 → 删除**，不「冻结」「deprecated 但留着」。

### 铁律 3 — 分步实施、步步可验

每一步必须同时包含：

1. **交付物**（代码 + 测试脚本）  
2. **手动验证命令**（你本地可跑、可眼看 PDF/输出）  
3. **通过标准**（ checklist，全勾才进入下一步）  
4. **本步删除项**（删旧代码，控制债务）

**禁止**：一步里同时做「建表 + 接 processor + 改 UI + 删旧代码」——耦合过大无法验。

---

## 1. 以终为始：终态定义

### 1.1 用户可见终态（Acceptance）

处理一份**原生文本型 PDF**（如建设银行第三支柱报告）后：

| # | 终态指标 | 如何验 |
|---|----------|--------|
| E1 | 每张表 row/col 与 PDF 视觉一致 | 对比 UI / 导出 CSV 与 PDF 同页 |
| E2 | CC1/CC2/SEC1 等披露表：数额/代码/a/b/c 列不串 | P11/P13/P27 自动化 + 人工 spot check |
| E3 | 表前说明合并为 text，表体为**一张** table | P10/P11 不再出现 3 表 + 10 text |
| E4 | 任意 cell 可溯源到 `pages.json` 的 item | `trace_cell(page, row, col)` 打印 x0/y0 |
| E5 | 单条生产 pipeline | 代码库无 `hybrid_segment_tables` 建表调用 |
| E6 | 无 `_fix_cc*` / `rule_based_repair` 建表依赖 | grep 零命中 |
| E7 | 扫描 PDF | 明确提示需 OCR（stub），不 silently 错 |

### 1.2 技术终态（Architecture Done）

```
PDF → liteparse → pages.json
                    ↓
         table_engine.pipeline.DocumentBuilder.build()
                    ↓
              Document (StructuredTable + TextBlock)
                    ↓
         export.legacy_adapter  →  UI / CSV
```

- 核心类型：`StructuredTable` / `Cell`（含 bbox + source_items）  
- `data[][]`：**仅**出现在 export，不出现在 pipeline 中间态  
- 旧模块**物理删除**：`hybrid_segmenter.py`（建表部分）、`liteparse_table_segmenter.py`（建表部分）、`table_content_splitter.py`（主逻辑）、`rule_based_repair.py`、`coord_row_refiner.py`（迁入后删原文件）

### 1.3 本阶段非目标

- OCR 实现（仅接口 + stub）  
- UI 重写（export adapter 即可）  
- 任意 PDF 100%（扫描件、极端无边框表除外）  

---

## 2. 现状与必须删除什么

### 2.1 根因（简述）

liteparse 有坐标，但在 `hybrid_segmenter._build_table_from_liteparse_fallback` ~L2070 压成 `data[][]` 后，表文分裂/结构分裂/列压缩全部在**字符串域**猜测 → 精准度上限被锁死。

### 2.2 删除清单（终态前必须清空）

| 模块 | 行量级 | 处置 |
|------|--------|------|
| `hybrid_segmenter.py` | ~2800 | Step 7 删除（建表+split 全删） |
| `liteparse_table_segmenter.py` | ~5400 | Step 7 删除（算法已迁入 table_engine） |
| `table_content_splitter.py` | ~1100 | Step 6 删除（split 迁入 table_engine） |
| `rule_based_repair.py` | ~3200 | Step 7 重命名为 `table_structure_repair.py`（仅保留 UI 规则修复，建表路径已删） |
| `coord_row_refiner.py` | ~300 | Step 2 迁入后删除原文件 |
| `header_boundary.py` 压缩/互补列 | ~800 | Step 6 删除相关函数 |
| `processor.py` 中 hybrid/V2 建表路径 | — | Step 7 改为只调 table_engine |
| pdf2docx **建表**主路径 | — | Step 7 移除（非校验） |

**不删除**：`liteparse_extractor/`、`ui/`、`dedup_engine`（段落去重，Step 7 后接 export 即可）。

### 2.3 算法复用原则

可从旧代码**抄写并简化**的算法（验过后删原件）：

- `cell_differ._cluster_items_by_y` → `geometry/row_cluster.py`  
- `coord_row_refiner` → `geometry/row_refiner.py`  
- `_normalize_rows_to_columns` 思路 → `geometry/cell_builder.py`（**输出 Cell，不是 texts**）  
- `infer_cc1/cc2_*` → `layout/pillar_*.py`（**禁止** `_fix_*` 字符串补丁）  

**禁止**：`from codes.table_validator.hybrid_segmenter import ...` 在新 engine 中出现。

---

## 3. 目标架构

```
DocumentBuilder.build(pages.json)
  ├─ source/       liteparse → SourceItem[]
  ├─ scope/        region + gap + 表头 y 扩展
  ├─ geometry/     Y 聚类 → 行精修 → X 分列 → Cell[][]
  ├─ layout/       CC1/CC2/SEC1/Generic 插件（仅几何）
  ├─ split/        表文/结构分裂（几何优先）
  ├─ export/       Document → legacy dict（UI）
  └─ ocr/          stub（扫描 PDF）
```

与旧架构本质区别：**StructuredTable 贯穿 split 之前全流程**。

---

## 4. 核心数据模型

位置：`codes/table_engine/models.py`

```python
@dataclass(frozen=True)
class BBox:
    x0: float; y0: float; x1: float; y1: float

@dataclass
class SourceItem:
    text: str
    bbox: BBox
    page: int
    item_index: str
    y_mid: float = 0.0

@dataclass
class Cell:
    text: str
    bbox: BBox
    row: int
    col: int
    source_items: List[str]

@dataclass
class StructuredTable:
    page: int
    y0: float; y1: float; x0: float; x1: float
    rows: List[List[Optional[Cell]]]
    grid: ColumnGrid
    layout_id: str
    caption: str = ""
    description_text: str = ""
    metadata: dict = field(default_factory=dict)

@dataclass
class Document:
    entries: List[DocumentEntry]   # table | text，阅读顺序
    build_report: BuildReport
```

**硬性规定**：`table_engine` 内部禁止 `List[List[str]]` 作为建表/split 中间态。

Export 层：

```python
def to_legacy_table(st: StructuredTable) -> dict:
    """唯一允许生成 data[][] 的地方。"""
```

---

## 5. 模块划分

```
codes/table_engine/
├── models.py
├── config.py
├── pipeline.py              # 唯一入口 DocumentBuilder
├── source/
│   ├── liteparse_loader.py
│   └── item_normalizer.py
├── scope/
│   ├── region_scope.py
│   ├── gap_capture.py
│   └── header_scope.py
├── geometry/
│   ├── row_cluster.py
│   ├── row_refiner.py
│   ├── column_detect.py
│   └── cell_builder.py
├── layout/
│   ├── base.py
│   ├── registry.py
│   ├── generic.py
│   ├── pillar_cc1.py
│   ├── pillar_cc2.py
│   ├── pillar_sec1.py
│   └── pillar_disclosure.py
├── split/
│   ├── table_text_split.py
│   ├── structure_split.py
│   ├── footnote_strip.py
│   └── y_calibrate.py
├── export/
│   ├── legacy_adapter.py
│   └── cell_trace.py
└── ocr/
    ├── backend.py
    └── stub.py
```

---

## 6. Pipeline 阶段

| Phase | 输入 | 输出 | 坐标 |
|-------|------|------|------|
| A Scope | PageSource | TableScope.items | ✅ |
| B RowCluster | items | RowCluster[] | ✅ |
| C RowRefine | RowCluster[] | RowCluster[] | ✅ |
| D ColumnGrid | rows + LayoutPlugin | ColumnGrid + Cell[][] | ✅ |
| E Assemble | Cell[][] | StructuredTable | ✅ |
| F Split | StructuredTable | DocumentEntry[] | ✅ |
| G Export | Document | legacy dict / CSV | 仅文本视图 |

Split 决策优先级：**Y 间隙 + 列网格变化 > 行号/数据列 profile > 字符串 fallback（须记录原因）**。

---

## 7. Layout 插件

```python
class LayoutPlugin(Protocol):
    layout_id: str
    def score(self, scope, rows) -> float: ...
    def infer_column_grid(self, scope, rows) -> ColumnGrid: ...
```

| layout_id | 表型 |
|-----------|------|
| pillar_cc1 | 行号\|标签\|数额\|代码 |
| pillar_cc2 | 行号\|标签\|a\|b\|代码 |
| pillar_sec1 | 12 列 a–l |
| pillar_disclosure | KM1/OV1 等 |
| generic | x 聚类兜底 |

**禁止** `_fix_cc1_*` / `_fix_cc2_*` 类字符串 replace — 列位错了修插件的 x 锚点，不修 export 字符串。

---

## 8. OCR 接口（预留）

```python
class OcrBackend(Protocol):
    def extract_pages(self, pdf_path) -> List[PageSource]: ...

class StubOcrBackend:
    def extract_pages(self, pdf_path):
        raise ScannedPdfNotSupportedError("扫描 PDF 待 OCR 后端")
```

OCR 输出与 liteparse 同形 → **同一 pipeline**，无 OCR 专用 split。

---

## 9. 分步实施计划（步步可验）

> **规则**：每步末尾「人工验收清单」全部勾选后，才开下一步。  
> 测试脚本放仓库根 `_test_te_stepN_*.py`，输出 PASS/FAIL + 可打印表头前几行。

---

### Step 0 — 终态契约与目录骨架

**交付**

- [ ] `codes/table_engine/models.py`（完整 dataclass）
- [ ] `codes/table_engine/config.py`
- [ ] `source/liteparse_loader.py`（pages.json → SourceItem[]）
- [ ] `_test_te_step0_loader.py`

**手动验证**

```powershell
cd f:\wills\my_softwares\DocuTable
python _test_te_step0_loader.py
# 期望：加载第三支柱缓存，打印 P11 前 20 个 item 的 text + x0/y0
```

**通过标准**

- [ ] loader 读出 item 数量与 pages.json 一致  
- [ ] 每个 SourceItem 有 item_index、bbox、y_mid  
- [ ] **本步不改 processor、不碰旧 hybrid**

**本步删除**：无  

**Go**：你确认 loader 输出坐标合理 → Step 1  

---

### Step 1 — 几何建表内核（单 scope、单表）

**交付**

- [ ] `geometry/row_cluster.py`（自写或自 cell_differ 抄写）  
- [ ] `geometry/row_refiner.py`（自 coord_row_refiner 抄写）  
- [ ] `geometry/cell_builder.py`  
- [ ] `layout/generic.py`  
- [ ] `pipeline.build_table_from_items(scope) -> StructuredTable`（**无 split、无 export**）  
- [ ] `_test_te_step1_geometry.py`  

**手动验证**

```powershell
python _test_te_step1_geometry.py --page 11
# 打印 StructuredTable：前 10 行，每行 col0..col3 的 text + x0
python _test_te_step1_geometry.py --page 13
python _test_te_step1_geometry.py --page 27
```

**通过标准**

- [ ] P11：4 列；行号「1」在 col0；385,621 在数额列非代码列  
- [ ] P13：5 列；a/b/c 表头行 col 正确  
- [ ] P27：12 列；表头 a–l 不丢  
- [ ] 每个非空 Cell 有 source_items  
- [ ] **未生成 data[][]**

**本步删除**

- [ ] 删除 `coord_row_refiner.py`（已迁入 `geometry/row_refiner.py`）

**Go**：三页 golden 全 PASS → Step 2  

---

### Step 2 — Layout 插件（CC1 / CC2 / SEC1）

**交付**

- [ ] `layout/pillar_cc1.py` / `pillar_cc2.py` / `pillar_sec1.py`  
- [ ] `layout/registry.py`  
- [ ] 更新 step1 pipeline 走插件  
- [ ] `_test_te_step2_layout.py`（替代原 `_test_cc1_page11` 等，测 StructuredTable）

**手动验证**

```powershell
python _test_te_step2_layout.py
# 汇总：P11/P13/P27 断言数 / 失败明细
```

**通过标准**

- [ ] P11：18 项原 CC1 断言等价或更严（无 `_fix_cc1`）  
- [ ] P13：表头 资产负债表/代码 列位正确（无 `_fix_cc2`）  
- [ ] P27：9 项 SEC1 断言通过  
- [ ] layout_id 正确写入 StructuredTable  

**本步删除**：无（旧测试可保留对照，但不依赖旧 hybrid）

**Go**：layout 三页全 PASS → Step 3  

---

### Step 3 — Scope + 边界（Phase A）

**交付**

- [ ] `scope/region_scope.py` / `gap_capture.py` / `header_scope.py`  
- [ ] `pipeline.build_page(page_num) -> List[StructuredTable]`（仍无文档级 split）  
- [ ] `_test_te_step3_scope.py`  

**手动验证**

```powershell
python _test_te_step3_scope.py --page 11
# 对比：region 外 a/b/单位/日期 是否进入表 scope
python _test_te_step3_scope.py --page 10
# 表头带是否在表内（非 gap 说明文字）
```

**通过标准**

- [ ] P11/P10：a/b、单位行、日期行在 StructuredTable 表头区  
- [ ] 3.1 长说明**不在**表 rows 内（仍在 gap/text，Step 5 才合并 Document）  
- [ ] scope.y0 小于 region.y0（有上扩）  

**Go** → Step 4  

---

### Step 4 — 披露表 Layout + 表文几何分裂（Phase F1）

**交付**

- [ ] `layout/pillar_disclosure.py`  
- [ ] `split/table_text_split.py`  
- [ ] `pipeline.build_page -> List[DocumentEntry]`（单页）  
- [ ] `_test_te_step4_split.py`  

**手动验证**

```powershell
python _test_te_step4_split.py --pages 5,6,9,10,11
# 每页：text 条数、table 条数、表行数
```

**通过标准**

- [ ] P5/P6/P9：各 1 张披露表 + 说明 text（不碎拆）  
- [ ] P10/P11：1 table + 少量 text（非 3 表 10 text）  
- [ ] 拆分边界 Y 来自 cell bbox，非线性插值  

**Go** → Step 5  

---

### Step 5 — 全文档 Pipeline + Export

**交付**

- [ ] `pipeline.DocumentBuilder.build()` 全页  
- [ ] `split/structure_split.py` / `footnote_strip.py` / `y_calibrate.py`  
- [ ] `export/legacy_adapter.py` + `cell_trace.py`  
- [ ] `_test_te_step5_document.py`  
- [ ] `_test_te_step5_trace.py`（溯源抽查）

**手动验证**

```powershell
python _test_te_step5_document.py
# 第三支柱 43 页：表数、text 数、失败页列表

python _test_te_step5_trace.py --page 11 --row 5 --col 2
# 打印 source item 坐标
```

**通过标准**

- [ ] 43 页无 P0 失败（清单见 `tests/golden/pillar_pages.yaml`）  
- [ ] export 的 `data[][]` 与 StructuredTable 一致  
- [ ] trace 可反查 pages.json  

**Go** → Step 6  

---

### Step 6 — 接入 Processor（单轨切换）

**交付**

- [ ] `processor.py`：**仅**调用 `DocumentBuilder.build()` + `legacy_adapter`  
- [ ] 删除 processor 内 `hybrid_segment_tables` 调用  
- [ ] GUI 跑通第三支柱 PDF 一次  

**手动验证**

```powershell
# 1. 自动化
python _test_te_step5_document.py

# 2. 手动 GUI
python main.py
# 打开第三支柱 PDF → 解析 → 抽查 P11/P13/P27 表格
```

**通过标准**

- [ ] GUI 与 step5 脚本结果一致  
- [ ] 日志无 hybrid / rule_based_repair 建表调用  

**本步删除**

- [ ] `table_content_splitter.py`  
- [ ] `header_boundary.py` 中 compact/互补列（建表相关部分）

**Go** → Step 7  

---

### Step 7 — 物理删除旧引擎 ✅

**交付**

- [x] 删除文件清单（§2.2）全部执行  
- [x] grep 确认无残留 import（`codes/` 零命中）  
- [ ] 更新 `docs/DocuTable_架构技术文档.md` 为 Table Engine 终态  

**手动验证**

```powershell
rg "hybrid_segment_tables|rule_based_repair|table_content_splitter" codes/
# 期望：零命中（或仅 docs/ 历史说明）

python _test_te_step5_document.py
python _test_te_step6_processor.py
python _test_te_step7_cleanup.py
```

**通过标准**

- [x] 旧模块物理删除  
- [x] 全量回归仍 PASS（Step 5/6/7）  
- [x] **无 feature flag 双轨**  

**Go** → Step 8（可选）  

---

### Step 8 — OCR Stub + UI 溯源（可选增强）✅

**交付**

- [x] `ocr/stub.py` + `PdfClassifier`  
- [x] UI 可选：点击 cell 显示 bbox（`trace_bridge` + 选中格 stats/tooltip）

**手动验证**

- [x] 扫描 PDF 分类拦截 → `scanned_pdf_ocr_required` 报告 + processor warning  
- [x] native PDF 行为与 Step 7 一致（Step 5/6/7 回归 PASS）

```powershell
python _test_te_step8_ocr_trace.py
```

---

## 9.1 步骤总览

| Step | 焦点 | 可验输出 | 删旧 |
|------|------|----------|------|
| 0 | 契约 + loader | item 坐标打印 | — |
| 1 | 几何建表 | P11/13/27 StructuredTable | coord_row_refiner 原文件 |
| 2 | Layout 插件 | 三页 layout golden | — |
| 3 | Scope/表头回补 | P10/P11 表头带 | — |
| 4 | 披露表 + 表文 split | P5/6/9/10/11 条目数 | — |
| 5 | 全文档 + export | 43 页 + trace | — |
| 6 | 接 processor/GUI | GUI = 脚本 | content_splitter 等 |
| 7 | 删旧引擎 ✅ | `_test_te_step7_cleanup.py` grep 零残留 | hybrid/liteparse_seg/splitter/coord_row |
| 8 | OCR stub + UI 溯源 ✅ | `_test_te_step8_ocr_trace.py` | — |

---

## 9.2 每步评审模板（请你使用）

```markdown
## Step N 验收 — 日期

- [ ] 自动化：_test_te_stepN_*.py 全 PASS
- [ ] 手动：抽查页码 ______ PDF 对照 OK
- [ ] 删除项已执行
- [ ] 同意进入 Step N+1

签字/确认：（你回复「Step N 通过」即可）
```

---

## 10. 附录

### A. Golden 页清单（第三支柱）

| 页 | 表型 | 引入 Step |
|----|------|-----------|
| P5 | KM1 | 4 |
| P6 | 杠杆率/多节 | 4 |
| P9 | OV1 | 4 |
| P10/P11 | CC1 | 1–4 |
| P13 | CC2 | 1–2 |
| P27 | SEC1 | 1–2 |
| 全 43 页 | 回归 | 5 |

### B. 配置默认值

见 `table_engine/config.py`：`ROW_CLUSTER_Y_TOLERANCE=3.0` 等。

### C. 与旧文档关系

- `DocuTable_架构技术文档.md` / `V2_技术方案.md` = **历史**  
- **本文档** = 重构唯一执行依据  
- Step 7 完成后重写架构文档 §2–§3  

### D. 坐标分列与折行修复（2026-06-30）

详见 **`docs/Table_Engine_坐标分列与折行修复.md`**，涵盖：

- x0/x1 锚点分列、多左文本列网格（P43 六列表）  
- 地址折行合并、表前说明剥离、数值/日期误判修复  
- 变化原因表、公允价值折行、股权投资末列粘连等专题  
- 涉及模块索引与回归测试命令  

---

**当前状态**：Step 0–8 已完成（Table Engine 重构主线交付完毕）。  
**后续**：可选更新 `DocuTable_架构技术文档.md` 为终态说明。
