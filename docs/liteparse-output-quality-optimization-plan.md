# LiteParse 输出质量优化技术方案

> 适用项目：DocuTable  
> 编写目的：指导后续 agent 对当前 `docx + liteparse + segmenter` 流程做可验证、可回滚的质量优化。  
> 背景样本：`data/mid_cache/test_subset7` 与 `data/mid_cache/liteparse_tables/20260607_114745`

## 1. 当前结论

当前 liteparse 分割链路已经能提取出一批结构较好的财务表，但存在系统性硬伤：候选区域、文本段落、页眉页脚、图表标签也会进入主结果和 CSV 导出。因此当前 CSV 结果只能视为“候选表集合”，不能视为“最终可信表格集合”。

以 `test_subset7` 为例：

- 最终 `tables = 37`
- `财务数据表 = 20`
- `数据表(缺表头) = 8`
- `文本列表 = 9`
- `success_count = 37`

问题在于：9 个 `文本列表` 已经被算法识别为非财务表，却仍然被标记为 `parse_status="success"` 并导出为 CSV；部分 `数据表(缺表头)` 其实是图表标签或表前说明，也被误判为真表。

## 2. 优化目标

### 2.1 主要目标

1. 主结果只展示和导出可信财务表。
2. 非表格、文本列表、图表标签、页眉页脚必须进入 rejected/debug 集合，而不是主表格集合。
3. 对“疑似表格但缺表头”的结果给出可解释状态，不能直接混入高可信表格。
4. 修复分割报告覆盖度统计，使 `orphans` 不可能出现负数。
5. 保留调试能力：被过滤的候选表不能丢，应可在报告或调试导出中查看。

### 2.2 非目标

本阶段不重写 liteparse 解析器，不重写 pdf2docx 通道，不引入 LLM 强依赖。优化应优先使用现有字段：

- `is_real_table`
- `is_complete`
- `table_category`
- `financial_confidence`
- `has_header`
- `has_numeric_data`
- `quality_checks`
- `rows`
- `text_items`

## 3. 主要硬伤定位

### 3.1 非真表仍被标为 success

位置：

- `codes/table_validator/liteparse_table_segmenter.py`
- 函数：`liteparse_tables_to_standard`

当前逻辑统一输出：

```python
"parse_status": "success",
"is_real_table": st.get("is_real_table", True),
```

这会导致 `文本列表`、低置信候选表也被主流程当作成功表。

### 3.2 CSV 导出不做过滤

位置：

- `codes/ui/validation_dialog.py`
- 函数：`_export_csv`

当前逻辑直接：

```python
for t in self.tables:
    ...
```

没有过滤 `is_real_table=False`、`table_category="文本列表"` 或低 `financial_confidence`。

### 3.3 真表判定门槛过宽

位置：

- `codes/table_validator/liteparse_table_segmenter.py`
- 函数：`_classify_table_quality`

当前核心判定近似为：

```python
is_real_table = has_numeric_data and col_count >= 2 and row_count >= 3
```

这个规则会把图表标签误判为真表。例如只有百分比、短标签、图表说明的区域，满足“有数值 + 多列 + 多行”，但不是结构化财务表。

### 3.4 覆盖度报告可出现负 orphan

位置：

- `codes/table_validator/liteparse_table_segmenter.py`
- 函数：`_generate_report`

当前用 `(text, x0, y0)` 统计 assigned item。跨页合并、表头恢复、重复 text_items 会使某页 assigned 数超过原始 item 数，导致：

```text
orphans = total - len(assigned)
```

出现负数。报告失去可信性。

## 4. 推荐总体架构

将当前单一 `tables` 集合拆成三层：

```text
seg_tables 原始候选
  |
  |-- accepted_tables     高可信财务表，进入 UI 主结果、普通导出、success_count
  |-- review_tables       疑似表，需要人工确认；默认不进入普通 CSV
  |-- rejected_tables     文本列表、页眉页脚、图表标签、低质候选；不进主结果/普通导出，仅供调试排查
```

建议保留 `processed_results["tables"]` 作为主结果集合，同时新增：

```python
processed_results["candidate_tables"] = [...]
processed_results["rejected_tables"] = [...]
processed_results["review_tables"] = [...]
processed_results["quality_summary"] = {
    "accepted": 0,
    "review": 0,
    "rejected": 0,
    "by_category": {...},
}
```

若暂时不想改 UI 数据结构，可先在 `liteparse_tables_to_standard()` 内将非真表标记为非 success，并在 UI/导出中按字段过滤。

## 5. 分级判定策略

### 5.1 Accepted：默认进入主结果

满足全部条件：

- `is_real_table is True`
- `table_category == "财务数据表"`
- `financial_confidence >= 0.75`
- `has_numeric_data is True`
- `row_count >= 4`
- `max_cols >= 3`
- 非页眉页脚特征

可选增强：

- 表头行含年份、日期、金额、占比、阶段、合计等关键词之一。
- 至少一列数值占比 >= 0.5。
- 第一列或前两列存在稳定中文标签。

### 5.2 Review：默认不进入普通 CSV，但在 UI 可人工确认

满足任一条件：

- `table_category == "数据表(缺表头)"` 且 `financial_confidence >= 0.75` 且 `row_count >= 6` 且 `max_cols >= 4`
- `0.55 <= financial_confidence < 0.75`
- 有数值列但缺表头
- 疑似续表，需要和上一张表合并或继承表头
- 表尾或表前混入说明文字但主体像表

Review 表不能直接丢弃，因为年报里确实存在跨页续表、缺表头续表。

注意：低置信、行列规模过小、结构很弱的 `数据表(缺表头)` 应进入 rejected，而不是增加人工复核负担。

### 5.3 Rejected：默认不进入主结果

满足任一条件：

- `is_real_table is False`
- `table_category in ("文本列表", "目录", "空表")`
- `financial_confidence < 0.55`
- `table_category == "数据表(缺表头)"` 但不满足 review 门槛
- 页眉页脚占比过高
- 图表标签特征明显
- 段落文本行占比过高
- 表格区域内没有稳定列结构

Rejected 不是物理删除。所有 rejected 候选表仍应保留在 `rejected_tables` 或 debug 导出中，便于问题排查、规则调参和回归验证。

## 6. 具体算法改动

### 6.1 新增统一质量决策函数

建议在 `liteparse_table_segmenter.py` 中新增：

```python
def decide_table_acceptance(table: dict) -> dict:
    """返回表格最终去向和理由。

    Returns:
        {
            "decision": "accepted" | "review" | "rejected",
            "reason": "...",
            "score": 0.0,
            "flags": [...]
        }
    """
```

不要让 UI、导出、主流程各写一套过滤规则。所有地方只看：

```python
table["quality_decision"]
table["quality_decision_reason"]
table["quality_flags"]
```

### 6.2 页眉页脚检测

新增行级检测。不要硬编码某一家银行或某一年报的完整页眉文本；应使用通用正则模式，并允许配置扩展。

```python
HEADER_FOOTER_PATTERNS = [
    r"(银行|公司|集团)股份有限公司$",       # 公司/银行页眉
    r"20\d{2}年(度|半年度|年度)?报告",      # 年报/半年报标题
    r"管理层讨论[与和]分析",                # 管理层讨论与分析
    r"财务回顾|经营情况讨论与分析",          # 常见章节标题
    r"财务报表附注",                        # 财报附注章节
    r"（除特别注明外",                      # 单位说明
    r"^\d+$",                              # 独立页码
]
```

规则：

- 前 3 行或后 3 行若大量命中页眉页脚，标记 `has_header_footer_noise=True`。
- 若整张候选表超过 30% 行是页眉页脚/章节标题，直接 `rejected`。
- 若只有表前 1-2 行是页眉页脚，允许 strip 后重评。

### 6.3 段落文本检测

新增 `is_paragraph_row(row)`：

判定特征：

- 单行文本长度 > 40 个中文字符。
- 非空单元格数 <= 2。
- 标点、逗号、句号较多。
- 不具备列对齐数值结构。

表级规则：

- 段落行占比 > 40%：`rejected`
- 表尾连续段落行：strip 到 `_stripped_tail_rows`
- 表前连续段落行：strip 到 `_stripped_leading_rows`

### 6.4 图表标签检测加强

已有 `_filter_chart_like_tables()`，但当前仍漏掉类似：

```text
61.72%, 发放贷款和垫款, 60.23%
26.33%, 金融投资, 25.15%
...
报告期末本集团资产总额（单位：百万元）
下表列出...
```

建议增加特征：

1. 第一列高比例百分比，第二列短中文标签，第三列百分比。
2. 行数较少，且缺少日期/金额/占比表头。
3. caption 或末行含“报告期末...（单位：百万元）”“下表列出...”，说明这是图表说明或表前导语。
4. 没有“金额/占比/合计/阶段/2024年12月31日”等完整表头。

判定：

```python
if percent_label_percent_pattern and not has_header:
    decision = "rejected"
    reason = "图表标签区域，不是结构化表格"
```

### 6.5 缺表头表格处理

`数据表(缺表头)` 不应直接当 accepted。

处理顺序：

1. 尝试 `_recover_missing_headers`。
2. 尝试与上一张 accepted/review 表合并或继承表头。
3. 重新分类。
4. 若结构发生变化，必须重新分类并重新计算 `financial_confidence`。
5. 若仍缺表头：
   - 有强财务结构：`review`
   - 图表/段落特征：`rejected`

当前代码的时序是 `_classify_table_quality -> _refine_table_boundaries -> _recover_missing_headers -> _compute_financial_confidence`，方向正确。后续实现若调整流程，也必须保持“边界/表头变更后重新分类和重新评分”。

### 6.6 修复覆盖度报告

在 `_generate_report()` 中，统计 assigned 时必须限定 item 原始所属页。

推荐给每个 item 增加稳定唯一 ID：

```python
item["_page"] = page_num
item["_uid"] = f"{page_num}:{round(x0,1)}:{round(y0,1)}:{text}"
```

报告统计：

```python
assigned = {
    it["_uid"]
    for table in tables
    for it in table["text_items"]
    if it.get("_page") == pg
}
original = {uid for page raw items}
assigned = assigned & original
orphans = len(original - assigned)
```

至少要保证：

```python
items_assigned <= items_total
orphans >= 0
```

如果短期不想改 item 结构，可在报告层用原页 `items_raw` 作为全集，对 assigned 做交集裁剪。

### 6.7 导出层过滤

修改 `ValidationDialog._export_csv()`：

默认导出：

```python
export_tables = [
    t for t in self.tables
    if t.get("quality_decision") == "accepted"
]
```

同时导出调试文件：

```text
_all_accepted_tables.csv
_review_tables.csv
_rejected_tables.csv
_quality_report.json
```

如果保持现有 `_all_tables.csv`，也应在标题行标注：

```text
# 表#7 P[4] [REJECTED: 图表标签区域]
```

避免使用者误用。

## 7. 主流程改动建议

位置：

- `codes/pdf_extractor/processor.py`
- `ProcessingWorker.run`

当前逻辑：

```python
segmented_results = liteparse_tables_to_standard(seg_tables, results)
results = segmented_results
```

建议改为：

```python
standard_tables = liteparse_tables_to_standard(seg_tables, results)
accepted = [t for t in standard_tables if t["quality_decision"] == "accepted"]
review = [t for t in standard_tables if t["quality_decision"] == "review"]
rejected = [t for t in standard_tables if t["quality_decision"] == "rejected"]

results = accepted + review  # 或只 accepted，取决于 UI 需求
```

推荐 UI 主列表默认显示 accepted，同时提供“待复核”筛选。普通导出只导 accepted，调试导出可导全部。

统计字段：

```python
"total_tables": len(results),
"success_count": len(accepted),
"review_count": len(review),
"rejected_count": len(rejected),
"candidate_count": len(standard_tables),
"rejected_tables": rejected,
"review_tables": review,
```

## 8. 回归样本与验收标准

### 8.1 固定样本

使用以下目录作为第一批回归样本：

- `data/mid_cache/test_subset7/data.json`
- `data/mid_cache/test_subset7/liteparse/pages.json`
- `data/mid_cache/liteparse_tables/20260607_114745`

### 8.2 必须通过的样本判断

应被 rejected：

- `table_001_P1_(续).csv`：正文段落。
- `table_006_P3.csv`：页眉 + 正文段落。
- `table_007_P4_3.31%...csv`：图表标签/表前说明。
- `table_013_P5.csv`：页眉章节。
- `table_016_P6.csv`：页眉章节。
- `table_017_P6_(续).csv`：正文段落。
- `table_021_P7-8_中国建设银行...csv`：页眉、章节、正文混合。
- `table_026_P9.csv`：页眉/说明文本。

应被 accepted 或 review：

- `table_002_P2...csv`：营业收入区域分布。
- `table_003_P2...csv`：利润总额区域分布。
- `table_005_P3_资产负债表分析 资产(续).csv`：资产总额构成主体表，但应剔除尾部正文。
- `table_008_P4...csv`：贷款和垫款构成主体表。
- `table_010_P5...csv`：按区域划分贷款分布。
- `table_012_P5...csv`：贷款损失准备阶段表。

### 8.3 边界样本判断

以下样本用于防止规则过严或过松：

| 样本 | 情景 | 期望行为 |
|------|------|----------|
| `table_005_P3_资产负债表分析 资产(续).csv` | 主体财务表正确，但尾部混入脚注和正文 | strip 尾部噪声后仍为 accepted |
| 缺表头、行数 >= 8、列数 >= 5 的跨页续表 | 主体像续表，但缺少完整表头 | review，不直接 accepted |
| 缺表头、行数 <= 3 或列数 <= 2 的候选 | 多为碎片、图表标签或正文 | rejected |
| 图表百分比标签区域 | 有百分比和短中文标签，但无表头和稳定列结构 | rejected |
| 页眉页脚 + 少量数字 | 有公司名、年度报告、页码等噪声 | rejected 或 strip 后重评 |

### 8.4 数量验收

对 `test_subset7`：

- 普通 CSV 不应导出 37 张。
- `文本列表` 不应出现在普通 CSV。
- `success_count` 不应等于候选表总数。
- `segmentation_report.page_details[*].orphans` 不得为负数。
- `_all_tables.csv` 或调试汇总必须明确标注 accepted/review/rejected。

## 9. 建议实施顺序

### Phase 1：低风险修正

1. 新增 `quality_decision` 字段，先实现基于现有字段的最小决策逻辑。
2. `liteparse_tables_to_standard()` 根据决策设置 `parse_status`。
3. `_export_csv()` 默认只导 accepted。
4. 修复 `success_count` 统计。
5. 修复 orphan 负数。

这一步不大改分割算法，只避免错误结果污染主输出。最小决策逻辑只依赖现有字段：

```python
if table_category in ("文本列表", "目录", "空表") or is_real_table is False:
    decision = "rejected"
elif table_category == "财务数据表" and financial_confidence >= 0.75:
    decision = "accepted"
elif table_category == "数据表(缺表头)" and financial_confidence >= 0.75 and row_count >= 6 and max_cols >= 4:
    decision = "review"
elif financial_confidence >= 0.55:
    decision = "review"
else:
    decision = "rejected"
```

Phase 1 的目标是“止血”，不是一次性解决所有误判。

### Phase 2：分类规则增强

1. 加页眉页脚检测。
2. 加段落行检测。
3. 加图表百分比标签检测。
4. 缺表头表默认进入 review，而不是 accepted。
5. 表前/表尾噪声 strip 后重新分类。

### Phase 3：UI 与调试体验

1. 对比预览 Tab 增加筛选：全部/可信表/待复核/已拒绝。
2. 导出支持普通导出和调试导出。
3. 分割报告展示 accepted/review/rejected 数量。
4. 点击表格时显示 `quality_decision_reason`。

## 10. 伪代码参考

```python
def decide_table_acceptance(table):
    category = table.get("table_category", "")
    conf = table.get("financial_confidence", 0.0)
    is_real = table.get("is_real_table", False)
    rows = table.get("rows", [])
    row_count = len(rows)
    col_count = max((len(r.get("texts", [])) for r in rows), default=0)

    flags = []

    if not rows:
        return reject("空表")

    if category in ("文本列表", "目录", "空表"):
        return reject(f"非财务表类型: {category}")

    if has_page_header_footer_noise(rows):
        flags.append("page_header_footer_noise")
        if header_footer_row_ratio(rows) > 0.3:
            return reject("页眉页脚占比过高")

    if paragraph_row_ratio(rows) > 0.4:
        return reject("段落文本占比过高")

    if is_chart_label_region(table):
        return reject("图表标签区域")

    if not is_real:
        return reject("缺少稳定数值列")

    if category == "数据表(缺表头)":
        if conf >= 0.75 and row_count >= 6 and col_count >= 4:
            return review("疑似续表或缺表头，需要人工确认", flags)
        return reject("缺表头且结构弱")

    if category == "财务数据表" and conf >= 0.75:
        return accept("高可信财务数据表", flags)

    if conf >= 0.55:
        return review("中等置信度财务候选", flags)

    return reject("置信度过低")
```

## 11. 注意事项

1. 不要直接删除 rejected 候选表。应保留到 `rejected_tables`，便于调试和人工复核。
2. 不要只靠 `financial_confidence`。当前置信度受 `is_real_table` 门槛影响，图表标签也可能拿到 0.6+。
3. `数据表(缺表头)` 是高风险类别，不能默认当成功表。
4. 覆盖度报告只用于诊断，不应影响主结果，直到 orphan 统计修复。
5. 所有规则变更必须在 `test_subset7` 上跑固定样本验收。
