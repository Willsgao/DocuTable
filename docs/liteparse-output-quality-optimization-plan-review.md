# 《litesparse 输出质量优化方案》审核意见

> 审核日期：2026-06-07  
> 审核对象：`docs/liteparse-output-quality-optimization-plan.md`  
> 审核方式：逐条核对源代码，验证每个诊断是否确有对应的 bug / 遗漏

---

## 一、问题定位准确度：4 个硬伤全部验证通过 ✅

### 3.1 `parse_status` 恒为 success ✅

**结论：文档判断正确。**

证据位置：`codes/table_validator/liteparse_table_segmenter.py` 第 3421 行附近

```python
"parse_status": "success",
```

`liteparse_tables_to_standard()` 在构建输出字典时，无论 `is_real_table` 是否为 False、`table_category` 是否为 `"文本列表"`，所有表都被硬编码为 `success`。调用方 `processor.py` 也因此将全部表的 `success_count` 加 1。

---

### 3.2 CSV 导出无过滤 ✅

**结论：文档判断正确。**

证据位置：`codes/ui/validation_dialog.py` 第 793–804 行附近

导出逻辑直接遍历 `self.tables` 全量写入 CSV，没有任何对 `is_real_table`、`table_category`、`financial_confidence` 的过滤判断。

---

### 3.3 `is_real_table` 判定过宽 ✅

**结论：文档判断正确。**

证据位置：`codes/table_validator/liteparse_table_segmenter.py` 第 2316 行附近

```python
is_real_table = has_numeric_data and col_count >= 2 and row_count >= 3
```

图表标签（如 `61.72%, 发放贷款和垫款, 60.23%`）同时满足"有数值 + 多列 + 多行"，会被误判为真表。文档中 Section 6.4 对此的分析完全吻合。

---

### 3.4 orphan 可为负数 ✅

**结论：文档判断正确。**

证据位置：`codes/table_validator/liteparse_table_segmenter.py` 第 1602–1603 行附近

```python
page_details[pg]["items_assigned"] = len(assigned)
page_details[pg]["orphans"] = total - len(assigned)
```

跨页表的 text_items 坐标可能与原始 `items_raw` 不匹配，导致 assigned 数超过该页原始 item 数，`orphans` 出现负值。这是裸减法导致的。

---

## 二、架构设计方案：方向正确 ✅

文档提出的三层架构：
- `accepted`（可信）— 无需人工复核
- `review`（待复核）— 需要人工确认
- `rejected`（已拒绝）— 直接丢弃

映射到 UI 的"可信 / 待复核 / 已拒绝"很自然，与现有交互流程兼容。

---

## 三、算法细节：2 处需要修正 ⚠️

### ⚠️ 问题 1：Section 5.2 与 Section 10 对"数据表(缺表头)"的处理规则不一致

| 位置 | 规则 |
|------|------|
| **Section 5.2**（决策规则速查表） | 只要 `table_category == "数据表(缺表头)"` → **review** |
| **Section 10**（伪代码） | `"数据表(缺表头)"` 且 `conf >= 0.75` 且 `row >= 6` 且 `col >= 4` → **review**；否则 → **reject** |

**Section 10 的伪代码版本更合理**——不应该把只有 3 行 2 列、毫无结构可言的"缺表头"也放进 review 增加人工负担。

**修正建议：以 Section 10 的伪代码为准，修改 Section 5.2 的规则速查表。** 修改后 Section 5.2 中该行应改为：

> | 数据表(缺表头) | review | conf ≥ 0.75 且 row ≥ 6 且 col ≥ 4 → review；否则 → reject |

---

### ⚠️ 问题 2：Section 6.2 页眉页脚模式过于硬编码

文档中提出的：

```python
HEADER_FOOTER_PATTERNS = [
    "中国建设银行股份有限公司",
    "2024年度报告",
    "管理层讨论与分析",
    ...
]
```

这个列表全是某一家银行（建设银行 2024 年报）的专有文本。DocuTable 的目标是处理**多家银行**的年报，硬编码特定银行的文本会让其他银行的报告完全匹配不上。

**修正建议：改为通用正则模式**，例如：

```python
HEADER_FOOTER_PATTERNS = [
    r"(银行|公司|集团)股份有限公司$",        # 页眉公司名
    r"20\d{2}年(度|半年度)报告",            # "2024年度报告"通用模式
    r"管理层讨论[与和]分析",                 # 章节标题
    r"财务报表附注",                        # 章节标题
    r"（除特别注明外",                       # 单位说明
    r"^\d+$",                              # 独立页码
]
```

---

## 四、遗漏与可增强之处 💡

### 4.1 Section 6.5：应补充说明 `financial_confidence` 也需重新计算

我核实的当前 pipeline 执行顺序：

```
_classify_table_quality
  → _refine_table_boundaries
    → _recover_missing_headers
      → _compute_financial_confidence
```

`financial_confidence` 基于最终状态计算，时序上没有 bug。但文档 Section 6.5 只说"恢复表头后重新分类"，应**补充说明置信度也需要重新计算**（目前代码已做到，但文档表述有歧义）。

---

### 4.2 Section 8 验收标准：建议补充边界样本

当前只列了"应当 rejected"和"应当 accepted/review"的样本。建议再补一段**边界样本（灰色地带）**，例如：

| 样本 | 情景 | 期望行为 |
|------|------|----------|
| `table_005` 尾部正文剥离后行数从 15→8 | 是否仍保持 accepted | accepted（行数列数仍达标） |
| 缺表头、行数 ≥ 8、列数 ≥ 5 的跨页续表 | 是否从 review 正确流向 accepted | review（缺表头永远是 review） |

---

### 4.3 Section 9 Phase 1 与 Phase 2 的依赖关系需更清晰

Phase 1 说"新增 `quality_decision` 字段"，但这依赖于 Phase 2 中才会实现的完整检测规则。

**建议**：在 Phase 1 中先实现一个**基于现有字段的最小决策逻辑**（只用 `is_real_table` + `table_category` + `financial_confidence`），后续在 Phase 2 中增强。文档目前这一点是隐含的，应明确写出。

---

## 五、总结

| 维度 | 评价 | 说明 |
|------|------|------|
| 问题定位准确度 | ✅ 优秀 | 4 个硬伤全部确实存在，与代码吻合 |
| 架构设计合理性 | ✅ 合理 | 三层分级 + 统一决策函数，方向正确 |
| 算法细节 | ⚠️ 良好 | 两处需修正（Section 5.2 规则不一致、Section 6.2 模式硬编码） |
| 边界情况覆盖 | ⚠️ 良好 | 建议补充灰色地带样本 |
| 实施顺序 | ⚠️ 良好 | Phase 1/2 依赖关系需说明 |

**总体评价**：这份文档可以作为开发蓝图，修正上述 2 处问题后即可按 Phase 1 → 2 → 3 顺序实施。

---

## 六、修改Checklist（供人工确认）

- [ ] **Section 5.2**：`"数据表(缺表头)"` → review 修改为 → `conf ≥ 0.75 且 row ≥ 6 且 col ≥ 4 则 review，否则 reject`（与 Section 10 伪代码对齐）
- [ ] **Section 6.2**：`HEADER_FOOTER_PATTERNS` 从硬编码改为通用正则模式
- [ ] **Section 6.5**：补充说明 `financial_confidence` 也需重新计算
- [ ] **Section 8**：补充边界样本验证用例
- [ ] **Section 9**：明确 Phase 1 使用最小决策逻辑（仅现有字段），Phase 2 引入新检测规则增强
