# Table Engine：坐标分列与折行修复说明

> **日期**：2026-06-30  
> **范围**：`codes/table_engine/` 内与「按坐标分列 / 分行 / 折行合并 / 数据守恒」相关的近期修改  
> **关联架构**：`docs/Table_Engine_重构架构.md`  
> **验证脚本**：`_test_p43_branch_table.py`、`_test_percent_reason_split.py`、`_test_fair_value_label_wrap.py`、`_test_equity_investment_value_subject.py`

---

## 1. 设计原则（三条约束）

本次修改始终服从 Table Engine 的三条铁律，并在实现层固化为可执行规则：

| 原则 | 含义 | 实现落点 |
|------|------|----------|
| **数据不丢** | 每个 liteparse `SourceItem` 最终须进入某个 cell 或 TEXT 块，不得静默丢弃 | `cell_builder._cell_text_from_items`、`item_conservation`、折行合并 |
| **顺序不变** | 同行多 item 按 `(y0, x0)` 阅读顺序拼接；不得按 OCR 流顺序重排 | `_cell_text_from_items`、分列前 `sorted(..., key=x0)` |
| **坐标锚点分列** | 文本列看 **x0 左缘**；数值列看 **x1 右缘**；序号强制 col 0 | `column_anchors.py`、`cell_builder._assign_item_to_columns` |

**禁止**：按 Region 扁平文本顺序硬切列；折行一律并入最左列；把 `0`/`12` 等表体整数当日期排除。

---

## 2. 总体处理流程

单页表格自 `PageSource` 到导出矩阵，与本次修改相关的阶段如下：

```
liteparse items (x0,y0,x1,y1)
    ↓
plan_page_scopes          # gap 说明 / 表头回补
    ↓
cluster_items_by_y        # Y 坐标聚类成行
    ↓
refine_clustered_rows     # 表体折行修补（地址列等）
    ↓
infer_constraint_grid     # 从 body item 坐标推断列界
    ↓
assign_rows_to_columns    # 按 x0/x1 锚点落列
    ↓
decompose_row_items       # 粘连格拆分（%/金额/科目等）
    ↓
repair_wrapped_label_suffix_rows  # 矩阵级折行合并
    ↓
apply_structure_split     # 表前说明剥离 → TEXT
    ↓
legacy export (data[][])
```

---

## 3. 坐标分列（列）

### 3.1 单列 item 落列规则

入口：`codes/table_engine/geometry/cell_builder.py` → `_assign_item_to_columns`

| 内容类型 | 锚点 | 函数 |
|----------|------|------|
| 序号（`0`–`18` 等） | x0，且 x0 ≤ 首列右界 + 10pt | 强制 col 0 |
| 表体数值、破折号 | **x1 右缘** | `col_index_by_x1` |
| 中文文本、机构名称、地址 | **x0 左缘** | `col_index_by_anchor` → `item_column_anchor` |
| 变化原因说明（长中文） | x0 不在标签区 → 末列 | `looks_like_change_reason_description_not_label` |
| 可变宽中间描述列 | x0 落在 `mid_label_x0s` 聚类 | `infer_mid_label_column_x0_clusters` |

**纠偏**：文本 item 被判入 col 0，但 `x0 > col_ranges[0].right` 时，改按 `col_index_by_x0` 重新落列（防止机构名称挤进序号列）。

### 3.2 列界推断（CGR / DBCG）

入口：`codes/table_engine/geometry/grid_infer.py`

**问题背景（P43 分支机构表）**：

- 旧逻辑假定「序号 + 1 个标签列 + N 个数值列」，把 x≈60（序号）、x≈98（机构名称）、x≈148（机构地址）压成 2 列；
- `infer_numeric_data_column_splits` 依赖 `is_numeric_data_cell`，而 `0`/`12` 曾被 `is_month_day_cell` 误判为日期 → **机构数整列从网格中消失** → 后处理删空列后只剩 4 列数据。

**修复要点**：

1. **`numeric.py`**：`_MONTH_DAY_CELL_RE` 去掉裸 `\d{1,2}` 分支，避免 `0`、`12` 被当日/月。
2. **`_infer_label_lead_column`**：收集 body 左侧全部 x0 聚类（≥3 次支持），返回 `[60, 98, 148]` 这类多左文本列锚点。
3. **`_ranges_from_lead_and_numeric`**：新增候选网格 `lead_numeric_grid`，在相邻 lead 锚点之间插列界，再衔接数值沟道。
4. **`_left_text_column_merge_violations`**：同一行名称与地址落同一列时计为违反，参与网格打分。
5. **`_expected_value_col_count`**：`len(splits) >= 2` 即认可 3 列数值（不再要求 4 列沟道）。

### 3.3 粘连格拆分（列内多字段）

入口：`codes/table_engine/geometry/cell_decomposition.py` + `cell_numeric_repair.py`

建表阶段 `decompose_row_items` 按固定顺序展开：

1. 标签末尾粘连金额  
2. 复合季度 / 报告期表头  
3. 变化原因表混合格（金额 + % + 说明）  
4. 末列「数值 + 会计科目」  
5. 多数值粘连 token  

矩阵阶段 `decompose_table` / `repair_glued_percent_reason_cells` 做二次分解与错位说明列回迁（`relocate_misplaced_reason_labels`）。

---

## 4. 坐标分行（行）

### 4.1 Y 聚类

入口：`codes/table_engine/geometry/row_dict.py` → `cluster_items_by_y`

按 item 的 `y0` / `y_mid` 聚类；地址折行在 PDF 中 y 略低于主行 → **独立 Y 行**（这是预期行为，不是分列错误）。

### 4.2 表体折行修补（item 阶段）

入口：`codes/table_engine/geometry/row_refiner.py` → `_refine_body_band`

| 类型 | 判定 | 合并方式 |
|------|------|----------|
| 短标签尾片（≤8 字） | `_row_is_continuation_fragment` | 并入上一数据行，要求 x 带重叠 |
| **地址列折行** | `_row_is_mid_column_wrap_fragment` + `text_looks_like_wrapped_address` | 按 **x0 对齐**并入上一行（`_label_items_share_x_band`） |
| 序号 + 名称粘连行 | `_can_merge_label_with_numbered` | 拆并 |

**注意**：地址折行须在 `_row_is_preserved_intra_table_label` 之前识别，否则长地址续文会被当成「表内小节标题」而拒绝合并。

### 4.3 矩阵折行修补（cell 阶段）

入口：`codes/table_engine/split/structure_split.py` → `repair_wrapped_label_suffix_rows`

按优先级尝试合并相邻行：

1. 折行标签首片 → 并入下一行 col 0  
2. 变化原因列尾片 → 并入末列说明格  
3. **地址列折行** → `should_merge_address_column_wrap_pair`，并入**上一行同列**（非 col 0）  
4. 通用标签尾片 → 并入上一行 col 0  

**多轮合并**：一次扫描可能只合并一层折行（如阿坝分行「元 1、2 层」在「省名…」之后），函数在 `remove_at` 非空时递归（深度 ≤ 8）直至稳定。

相关判定：`codes/table_engine/split/boundary_overlap.py`

- `row_is_address_column_wrap_fragment`  
- `should_merge_address_column_wrap_pair`  
- `address_wrap_column_index`

### 4.4 地址折行 vs 表头误判

入口：`codes/table_engine/split/row_classify.py`

**`text_looks_like_wrapped_address(text)`**  
用地址特征词（路/街/号/层/酒店/建筑…）+ 数字组合识别折行续文，**排除**列标判定。

**`_value_text_looks_like_column_header`**  
若文本像地址续文，不再当成「值列表头」，避免地址行被 `is_likely_next_table_header_row` 拦截。

**`row_is_table_intro_caption_row`**  
识别「具体经营网点如下」类表前说明。

**`is_inter_table_narrative_row`**  
表前说明优先剥为 TEXT；`is_intra_table_section_row` 对长说明行返回 false，避免当成表内小节。

### 4.5 gap 捕获折行融合

入口：`codes/table_engine/scope/gap_capture.py` → `_fuse_wrap_row_into_body_row`

**旧逻辑**：折行尾片一律并入 body 行**最左** item。  
**新逻辑**：在 body 行文本 item 中找 `|x0 - wrap_x0|` 最小者并入（地址折行进地址列，不进机构名称列）。

`_collect_label_wrap_tail_item_rows` 同时收集 x∈[115, 280] 的地址型尾片（不再仅限 x < 170 的短标签片）。

---

## 5. 表文分界（说明不进表）

入口：`codes/table_engine/split/structure_split.py` → `apply_structure_split`

对每个 table entry：

1. `_peel_leading_narrative_rows`：首行若为 `is_inter_table_narrative_row` / `row_is_table_intro_caption_row` → 生成 `TextBlock`  
2. `repair_wrapped_label_suffix_rows`：矩阵折行修补  
3. `split_table_by_structure` / `split_structured_table`：表内结构分裂  

典型用例：P43「和下辖的 210 家支行，具体经营网点如下：」→ **文本 2**，不进表格矩阵。

---

## 6. 专题修复一览

### 6.1 P43 分支机构表（6 列）

| 列 | 典型 x0 | 锚点 |
|----|---------|------|
| 序号 | ~60 | x0 |
| 机构名称 | ~98 | x0 |
| 机构地址 | ~148 | x0 |
| 机构数 | ~338 | x1 |
| 员工数 | ~402 | x1 |
| 资产规模 | ~458 | x1 |

测试：`_test_p43_branch_table.py`（含 OCR 乱序、全 19 行、表前说明剥离）。

### 6.2 变化原因表（% / 金额 / 说明粘连）

| 现象 | 处理 |
|------|------|
| `30.69% 拆放同业款项增加` | `peel_trailing_percent_reason` / `split_percent_trailing_text` |
| `30.69% 68,823,341 拆放…` 三字段 | `split_percent_amount_reason_text` |
| 说明文误落首列 | `relocate_misplaced_reason_labels` + `is_item_in_label_column_zone` |

测试：`_test_percent_reason_split.py`、`_test_p33_change_reason_table.py`

### 6.3 公允价值表（多行标签折行）

| 现象 | 处理 |
|------|------|
| `以公允价值计量且其变动计` 折行首片 | `row_is_wrapped_label_head_row` + `should_merge_wrapped_label_head_into_next` |
| x0 在首列范围内强制留 col 0 | `is_item_in_label_column_zone` |
| Y 聚类过宽误吞数值行 | `_split_wrap_head_intruding_value_row` |

测试：`_test_fair_value_label_wrap.py`

### 6.4 股权投资表（末列数值 + 科目粘连）

| 现象 | 处理 |
|------|------|
| `5,780 交易性金融资产` | `split_value_trailing_text_label` / `expand_value_text_glued_row_items` |
| 分解后清空同格 bug | `_apply_fragments_to_row` 不再 `source_cell.text = ""` |

测试：`_test_equity_investment_value_subject.py`

---

## 7. 涉及文件索引

| 模块 | 文件 | 职责 |
|------|------|------|
| 数值判定 | `geometry/numeric.py` | `is_numeric_data_cell`、粘连拆分正则、月日误判修复 |
| 列锚点 | `geometry/column_anchors.py` | `col_index_by_x0/x1`、`item_column_anchor`、`infer_mid_label_column_x0_clusters` |
| 列界推断 | `geometry/grid_infer.py` | CGR/DBCG、多左文本列、`lead_numeric_grid` |
| 落列 | `geometry/cell_builder.py` | `_assign_item_to_columns`、`assign_rows_to_columns` |
| 粘连展开 | `geometry/cell_decomposition.py` | `decompose_row_items`、`decompose_table` |
| 数值修复 | `geometry/cell_numeric_repair.py` | `expand_*_row_items` |
| 行聚类修补 | `geometry/row_refiner.py` | `_refine_body_band`、`_row_is_mid_column_wrap_fragment` |
| 行分类 | `split/row_classify.py` | 表前说明、地址特征、表头/叙述判别 |
| 折行判定 | `split/boundary_overlap.py` | 地址/标签/原因列折行合并对 |
| 结构分裂 | `split/structure_split.py` | `repair_wrapped_label_suffix_rows`、`apply_structure_split` |
| gap | `scope/gap_capture.py` | 按 x0 折行融合、地址尾片收集 |
| 表文分裂 | `split/table_text_split.py` | `build_page_entries`、`split_structured_table` |

---

## 8. 回归测试

```bash
python _test_p43_branch_table.py
python _test_percent_reason_split.py
python _test_fair_value_label_wrap.py
python _test_equity_investment_value_subject.py
python _test_report_date_header_split.py
```

**P43 完整场景断言**（`test_branch_table_caption_peeled_and_no_column_loss`）：

- 6 列齐全，19 行表体无列丢失  
- 表前说明在 TEXT，不在表格  
- 重庆/西安/眉山地址折行并入地址列  
- 无「仅地址列有字」的孤儿行  

---

## 9. 排查清单（坐标分列仍异常时）

1. **item 是否带 bbox**：无坐标的扁平 Region 文本无法正确分列。  
2. **机构数是否被当日期**：检查 `is_numeric_data_cell("0")` 应为 `True`。  
3. **列数是否为 6**：检查 `table.grid.col_count` 与 `grid.ranges`。  
4. **折行是否独立成行**：矩阵修复后行数应 ≈ 表头 + 表体行数，无孤儿地址行。  
5. **说明是否进表**：`build_page` 后应用 `entries` 中 `kind=text` 是否含「经营网点如下」。  

---

## 10. 与 V2 / Hybrid 的关系

- **Table Engine**（本文档）是 native PDF 表格重建的**主路径**（`processor_bridge.run_table_engine_segmentation`）。  
- **V2 步骤**、**Hybrid 分割器**负责上游 region / 边界；坐标分列与折行逻辑在 `table_engine` 内统一执行。  
- 若 UI 仍见「表格 3 丢列」，先确认导出是否走 `table_engine` 而非 liteparse 回退矩阵的直接 tab 拼接。

---

**维护**：新增折行/分列规则时，须同步补充 §3–§4 判定表与 §8 回归脚本，避免单页补丁破坏通用锚点原则。
