# PDF表格提取算法 v2 技术规格书

> 作者：AI架构师
> 日期：2026-05-12
> 目标：为 CodeBuddy 提供可落地的技术方案，替换 `processor.py` 中的表格提取逻辑
> 约束：纯CPU · 无外部API · 无LLM · 不限网络 · 仅处理文本类PDF · PyMuPDF + pdfplumber

---

## 0. 总体目标与原则

### 目标
从文本型PDF（银行年报等金融文档）中准确提取财务表格，要求：
- 行列不混淆：每个单元格内容精确归位
- 数据不丢失：无法精确提取时降级兜底，不允许跳过
- 速度快：单页 < 0.3s
- 可维护：每个方法职责单一，方便人工调参

### 设计原则
1. **多信号融合**：文字位置 + 表格线 + 字体特征，不依赖单一信号
2. **渐进式降级**：最精确的方析法优先→不行再降级→兜底保障
3. **三保"一"**：保准确 > 保速度 > 保召回
4. **可观测**：每一步输出中间数据，方便定位问题

---

## 1. 架构概览

### 文件位置
替换 `F:\wills\my_softwares\pdf_table_extractor\codes\pdf_extractor\processor.py`

### 新增/修改的方法

```
PDFProcessor 类 (processor.py)
│
├── 现有方法（不动）
│   ├── __init__()
│   ├── is_image_pdf()
│   ├── pdf_to_images()
│   ├── _normalize_table_columns()     ← 已有，保留
│   └── _merge_tables_on_same_page()   ← 已有，保留
│
├── 修改方法
│   ├── extract_text_tables()          ← 入口不变，内部替换调用
│   └── _reconstruct_table_from_blocks_improved()  ← 整个替换
│
└── 新增方法（3个 + 4个辅助）
    ├── _detect_horizontal_lines()      ← 水平表格线检测
    ├── _detect_vertical_lines()        ← 垂直线检测 + 对齐检测 + gap兜底
    ├── _assign_words_to_grid()         ← 重叠面积单元格分配
    │
    ├── _compute_dynamic_y_threshold()  ← 动态行阈值计算（辅助方法）
    ├── _group_words_into_rows()        ← 行分组（辅助方法）
    ├── _build_grid()                   ← 网格构建（辅助方法）
    └── _compute_table_confidence()     ← 置信度评分（辅助方法）
```

### 数据流

```
extract_text_tables(pdf_path)
  │
  ├─ 遍历每页
  │    │
  │    ├─ 1. 提取文字word + 绘制对象
  │    │     page.get_text("words") + page.get_drawings()
  │    │
  │    ├─ 2. 表格区域定位
  │    │     → 有框：用drawing检测外框矩形
  │    │     → 无框：用金融关键词检测文本区域
  │    │
  │    ├─ 3. 行边界检测 _detect_horizontal_lines()
  │    │     → 优先：水平线坐标
  │    │     → 降级：动态阈值基线聚类
  │    │
  │    ├─ 4. 列边界检测 _detect_vertical_lines()
  │    │     → 优先：垂直线坐标
  │    │     → 次优：文本对齐聚簇
  │    │     → 兜底：gap中位数阈值
  │    │
  │    ├─ 5. 网格填充 _assign_words_to_grid()
  │    │     → word分配到单元格（重叠面积判定）
  │    │     → 同cell文本合并
  │    │
  │    └─ 6. 置信度评分 _compute_table_confidence()
  │
  └─ 返回 results[]
```

---

## 2. 数据结构定义

### 2.1 Word 格式

从 `page.get_text("words")` 获取，转换为统一格式：

```python
# 原始格式（PyMuPDF）:
#   (x0, y0, x1, y1, "word_text", block_no, line_no, word_no)

# 统一格式（自定义NamedTuple或dict）:
Word = {
    "x0": float,    # 左边界
    "y0": float,    # 上边界（基线）
    "x1": float,    # 右边界
    "y1": float,    # 下边界
    "text": str,    # 文字内容
    "font_size": float,  # 字号（可选，用于字体分析）
    "baseline": float,   # = y1（文字基线，用于行分组）
}
```

### 2.2 Drawing 格式

从 `page.get_drawings()` 获取，分类为：

```python
Drawing = {
    "type": "line" | "rect",      # 线或矩形
    "direction": "h" | "v",       # 水平或垂直（仅type="line"时有效）
    "x0": float, "y0": float,     # 起点
    "x1": float, "y1": float,     # 终点
    "color": tuple,               # RGB颜色
    "width": float,               # 线宽
    "fill": tuple | None,          # 填充色
}
```

### 2.3 表格结构

```python
TableCandidate = {
    "page": int,                        # 页码
    "type": "table",                    # 固定
    "data": [[str]],                    # 二维数组，[行][列] = 文本
    "text": str,                        # 原始文本合并（用于过滤判断）
    "extractor": str,                   # 使用的提取器标识
    "confidence": float,                # 置信度 0-1
    "rows": int,                        # 行数
    "cols": int,                        # 列数
    "has_border": bool,                 # 是否有表格线框
    "empty_ratio": float,               # 空值率（用于质量判断）
}
```

---

## 3. 详细算法规范

### 3.1 表格区域定位

#### 方法A：有框表格（靠drawing检测）

```python
def _detect_table_region(page_drawings, page_width, page_height):
    """
    从drawing中检测表格区域
    输入：页面绘图对象列表
    输出：[(x0, y0, x1, y1), ...] 每个表格的外框矩形
    """
    # 1. 从drawings中提取所有矩形
    rectangles = [d for d in page_drawings 
                  if d["type"] == "rect" 
                  and d["x1"] - d["x0"] > page_width * 0.3  # 宽度>30%页宽
                  and d["y1"] - d["y0"] > 20]  # 高度>20pt
    
    # 2. 提取所有水平线和垂直线
    h_lines = [d for d in page_drawings 
               if d["type"] == "line" and d["direction"] == "h"
               and d["x1"] - d["x0"] > page_width * 0.3]
    v_lines = [d for d in page_drawings 
               if d["type"] == "line" and d["direction"] == "v"
               and d["y1"] - d["y0"] > 20]
    
    # 3. 表格区域判定条件：
    #    - 有外框矩形 或
    #    - 至少有2条水平线 + 2条垂直线
    regions = []
    
    # 条件A：外部矩形
    for rect in rectangles:
        regions.append((rect["x0"], rect["y0"], rect["x1"], rect["y1"]))
    
    # 条件B：线框网格
    if len(h_lines) >= 2 and len(v_lines) >= 2:
        x0 = min(l["x0"] for l in v_lines)
        x1 = max(l["x1"] for l in v_lines)
        y0 = min(l["y0"] for l in h_lines)
        y1 = max(l["y1"] for l in h_lines)
        
        # 检查是否与已有区域重叠
        if not regions or not _has_overlap((x0, y0, x1, y1), regions):
            regions.append((x0, y0, x1, y1))
    
    return regions
```

#### 方法B：无框表格（靠文本密度 + 金融关键词）

```python
def _detect_table_region_by_text(words, page_width, page_height):
    """
    无框表格区域检测
    输入：words列表
    输出：[(x0, y0, x1, y1), ...]
    """
    if not words or len(words) < 20:
        return []
    
    # 1. 全文关键词过滤
    full_text = " ".join(w["text"] for w in words)
    financial_keywords = [
        "万元", "元", "百万", "十亿", "%", "比率",
        "资产", "负债", "收入", "利润", "现金", "股东",
        "资本", "充足率", "率", "额", "数"
    ]
    has_financial = any(kw in full_text for kw in financial_keywords)
    if not has_financial or len(full_text) < 50:
        return []  # 没有金融内容，跳过
    
    # 2. 文本密度分析
    #    将页面分成网格，统计每个网格的文本密度
    grid_rows, grid_cols = 10, 10
    cell_h = page_height / grid_rows
    cell_w = page_width / grid_cols
    
    density = [[0] * grid_cols for _ in range(grid_rows)]
    for w in words:
        col = int((w["x0"] + w["x1"]) / 2 / cell_w)
        row = int((w["y0"] + w["y1"]) / 2 / cell_h)
        if 0 <= row < grid_rows and 0 <= col < grid_cols:
            density[row][col] += 1
    
    # 文本密度高的区域可能存在表格
    # 对行求和：表格行通常有多个文本格
    row_density = [sum(density[r]) for r in range(grid_rows)]
    
    # 找到高密度行区域
    table_rows = []
    for r in range(grid_rows):
        # 阈值：平均密度的2倍
        avg = sum(row_density) / max(len(row_density), 1)
        avg = max(avg, 3)  # 至少3个word/格
        if row_density[r] > avg * 0.8:
            table_rows.append(r)
    
    if not table_rows:
        return []
    
    # 3. 合并连续的高密度行
    table_row_ranges = _merge_consecutive(table_rows)
    
    # 4. 返回页面的全宽区域（无框表格通常占全页宽）
    regions = []
    for start, end in table_row_ranges:
        y0 = start * cell_h
        y1 = (end + 1) * cell_h
        regions.append((0, y0, page_width, y1))
    
    return regions
```

---

### 3.2 行边界检测

#### 核心方法：`_detect_horizontal_lines()`

```python
def _detect_horizontal_lines(page, words, page_drawings):
    """
    检测行边界
    返回值：[(y_top, y_bottom), ...] 每行的上下边界列表
    """
    # ----- 策略A：水平表格线（最精确） -----
    h_line_ys = sorted(set(
        d["y0"] for d in page_drawings
        if d["type"] == "line" and d["direction"] == "h"
    ))
    
    # 过滤掉太短的线（短于页宽30%的不是表格线）
    h_lines = []
    for d in page_drawings:
        if d["type"] == "line" and d["direction"] == "h":
            if d["x1"] - d["x0"] > page.rect.width * 0.3:
                h_lines.append(d["y0"])
    h_lines = sorted(set(h_lines))
    
    if len(h_lines) >= 2:
        # 有水平线 → 直接按线位置建立行边界
        row_bounds = []
        for i in range(len(h_lines) - 1):
            row_bounds.append((h_lines[i], h_lines[i + 1]))
        return row_bounds
    
    # ----- 策略B：动态阈值行分组（无水平线时） -----
    y_threshold = _compute_dynamic_y_threshold(words)
    rows = _group_words_into_rows(words, y_threshold)
    
    # 从行组计算出行边界
    row_bounds = []
    for row_words in rows:
        if row_words:
            y_top = min(w["y0"] for w in row_words)
            y_bot = max(w["y1"] for w in row_words)
            row_bounds.append((y_top, y_bot))
    
    return row_bounds
```

#### 辅助方法：`_compute_dynamic_y_threshold()`

```python
def _compute_dynamic_y_threshold(words):
    """
    动态计算行分组阈值
    原理：分析页面文字的y坐标分布，找出最小典型行间距
    输入：words列表
    输出：阈值（float）
    """
    if not words or len(words) < 3:
        return 5  # 兜底
    
    # 提取所有文字的y0（上边界）
    y_positions = sorted(set(w["y0"] for w in words if w["text"].strip()))
    
    if len(y_positions) < 5:
        return 5
    
    # 计算相邻y的差值
    gaps = []
    for i in range(len(y_positions) - 1):
        gap = y_positions[i + 1] - y_positions[i]
        # 只保留合理范围内的gap（0.5pt ~ 50pt）
        if 0.5 < gap < 50:
            gaps.append(gap)
    
    if len(gaps) < 3:
        return 5
    
    import statistics
    # 使用中位数（比均值更抗异常值）
    median_gap = statistics.median(gaps)
    
    # 行分组阈值取中位gap×0.4
    # 理由：同一行文字y0差异通常 < 行间距×0.3
    #        不同行y0差异通常 > 行间距×0.7
    #        0.4在两者之间提供安全区间
    threshold = median_gap * 0.4
    
    # 限制范围：最小2pt，最大15pt
    return max(2.0, min(15.0, threshold))
```

#### 辅助方法：`_group_words_into_rows()`

```python
def _group_words_into_rows(words, y_threshold):
    """
    按y坐标对words进行行分组
    输入：words列表, y_threshold阈值
    输出：[[word, ...], [word, ...], ...] 每组是一行
    """
    if not words:
        return []
    
    # 按y0排序
    sorted_words = sorted(words, key=lambda w: w["y0"])
    
    rows = []
    current_row = [sorted_words[0]]
    current_y = sorted_words[0]["y0"]
    
    for w in sorted_words[1:]:
        if abs(w["y0"] - current_y) <= y_threshold:
            # 同一行
            current_row.append(w)
            # 更新当前行y0为平均值
            current_y = (current_y + w["y0"]) / 2
        else:
            # 新行
            rows.append(sorted(current_row, key=lambda w: w["x0"]))
            current_row = [w]
            current_y = w["y0"]
    
    if current_row:
        rows.append(sorted(current_row, key=lambda w: w["x0"]))
    
    return rows
```

---

### 3.3 列边界检测

#### 核心方法：`_detect_vertical_lines()`

```python
def _detect_vertical_lines(page, words, page_drawings):
    """
    检测列边界（三指令融合）
    返回值：[x0, x1, x2, ...] 列分割线位置
    """
    # ----- 指令1：垂直线（最精确） -----
    v_lines = sorted(set(
        d["x0"] for d in page_drawings
        if d["type"] == "line" and d["direction"] == "v"
    ))
    
    if len(v_lines) >= 3:
        # 有3条以上垂直线 → ≥2列
        # 检查是否有两条线在页面中间（排除左右边框）
        inner_lines = [x for x in v_lines 
                       if page.rect.width * 0.05 < x < page.rect.width * 0.95]
        if len(inner_lines) >= 2:
            return v_lines  # 直接用垂直线坐标
    
    # ----- 指令2：文本对齐聚簇 -----
    x0_list = [w["x0"] for w in words if w["text"].strip()]
    x1_list = [w["x1"] for w in words if w["text"].strip()]
    
    if x0_list:
        # x0对齐点检测
        left_aligns = _cluster_1d(x0_list, tolerance=4)
        right_aligns = _cluster_1d(x1_list, tolerance=4)
        
        # 合并左右对齐点
        all_aligns = sorted(set(left_aligns + right_aligns))
        
        # 如果对齐点多于2个，用对齐点作为列边界
        if len(all_aligns) >= 3:
            return all_aligns
    
    # ----- 指令3：gap检测（兜底） -----
    all_x = sorted(set(x0_list + x1_list))
    
    if len(all_x) < 3:
        return [0, page.rect.width]
    
    # 计算gap
    gaps = []
    gap_positions = []
    for i in range(len(all_x) - 1):
        gap = all_x[i + 1] - all_x[i]
        if gap > 0:
            gaps.append(gap)
            gap_positions.append((all_x[i], all_x[i + 1]))
    
    if not gaps:
        return [0, page.rect.width]
    
    import statistics
    # 用中位数 + 标准差作为阈值（比纯中位数更鲁棒）
    median_gap = statistics.median(gaps)
    stdev_gap = statistics.stdev(gaps) if len(gaps) >= 2 else median_gap * 0.5
    gap_threshold = max(median_gap + stdev_gap * 0.3, 10)
    
    # 找到gap大于阈值的位置
    boundaries = [0]
    for (left, right), gap in zip(gap_positions, gaps):
        if gap > gap_threshold:
            boundaries.append((left + right) / 2)
    boundaries.append(page.rect.width)
    
    return boundaries
```

#### 辅助方法：`_cluster_1d()`

```python
def _cluster_1d(values, tolerance=4):
    """
    一维坐标聚簇
    输入：[x1, x2, x3, ...]
    输出：[c1, c2, c3, ...] 聚类中心
    
    作用：找出文本对齐位置
    例：多个word的x0都在72, 73, 72.5 → 聚类中心 ≈ 72.5
    """
    if not values:
        return []
    
    sorted_vals = sorted(values)
    clusters = []
    current_cluster = [sorted_vals[0]]
    
    for v in sorted_vals[1:]:
        if v - current_cluster[-1] <= tolerance:
            current_cluster.append(v)
        else:
            # 只有≥3个点才认为是有意义的对齐
            if len(current_cluster) >= 3:
                clusters.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [v]
    
    if len(current_cluster) >= 3:
        clusters.append(sum(current_cluster) / len(current_cluster))
    
    return clusters
```

---

### 3.4 单元格填充

#### 核心方法：`_assign_words_to_grid()`

```python
def _assign_words_to_grid(words, row_bounds, col_bounds):
    """
    将words分配到行列网格中
    输入：
        words: [Word, ...]
        row_bounds: [(y0, y1), ...] 每行的上下边界
        col_bounds: [x0, x1, x2, ...] 列分割线
    输出：
        [[str]] 二维数组 row_count × col_count
    """
    n_rows = len(row_bounds)
    n_cols = len(col_bounds) - 1
    
    if n_rows == 0 or n_cols == 0:
        return []
    
    # 初始化网格
    grid = [[[] for _ in range(n_cols)] for _ in range(n_rows)]
    
    for w in words:
        wx0 = w["x0"]
        wy0 = w["y0"]
        wx1 = w["x1"]
        wy1 = w["y1"]
        text = w["text"]
        
        if not text.strip():
            continue
        
        # ---- 行分配 ----
        row_idx = None
        for r, (y_top, y_bot) in enumerate(row_bounds):
            # 使用文字中心点是否在行区间内
            center_y = (wy0 + wy1) / 2
            margin = (y_bot - y_top) * 0.2  # 允许20%的越界
            if (y_top - margin) <= center_y <= (y_bot + margin):
                row_idx = r
                break
        
        # ---- 列分配（重叠面积法）----
        col_idx = None
        max_overlap = 0
        
        for c in range(n_cols):
            col_left = col_bounds[c]
            col_right = col_bounds[c + 1]
            
            # 计算word与列的重叠宽度
            overlap = max(0.0, min(wx1, col_right) - max(wx0, col_left))
            
            if overlap > max_overlap:
                max_overlap = overlap
                col_idx = c
        
        if row_idx is not None and col_idx is not None:
            grid[row_idx][col_idx].append(text)
    
    # 合并单元格文本
    result = []
    for r in range(n_rows):
        row_data = []
        for c in range(n_cols):
            cell_texts = grid[r][c]
            if cell_texts:
                # 同一单元格的文本按x坐标排序后合并
                row_data.append(" ".join(cell_texts))
            else:
                row_data.append("")
        result.append(row_data)
    
    return result
```

---

### 3.5 置信度评分

```python
def _compute_table_confidence(table_data, has_border, page_words):
    """
    计算表格提取结果的置信度
    输入：
        table_data: [[str]] 二维表格数据
        has_border: bool 是否有表格线框
        page_words: [Word] 当前页所有word（用于数值比例计算）
    输出：
        confidence: float (0-1)
    """
    if not table_data or len(table_data) < 2:
        return 0.0
    
    import statistics
    
    scores = []
    
    # ----- 因子1：列数一致性（权重0.35）-----
    col_counts = [len(row) for row in table_data if row]
    if col_counts and len(col_counts) >= 2:
        mean_cols = statistics.mean(col_counts)
        # 计算列数变异系数
        cv = statistics.stdev(col_counts) / mean_cols if mean_cols > 0 else 1.0
        col_consistency = max(0.0, 1.0 - cv * 2)  # cv越小越好
        scores.append((col_consistency, 0.35))
    else:
        scores.append((0.5, 0.35))
    
    # ----- 因子2：空值率（权重0.25）-----
    total_cells = sum(len(row) for row in table_data)
    empty_cells = sum(1 for row in table_data for cell in row if not str(cell).strip())
    empty_ratio = empty_cells / max(total_cells, 1)
    # 表格应该有少量空值，完全没空值或太多空值都不可信
    if empty_ratio < 0.05:
        empty_score = 0.7  # 几乎没有空值，可能是一整段文字
    elif empty_ratio > 0.5:
        empty_score = 0.3  # 半数以上空值
    else:
        empty_score = 1.0 - empty_ratio
    scores.append((empty_score, 0.25))
    
    # ----- 因子3：数值占比（权重0.25）-----
    def is_numeric(text):
        text = str(text).strip().replace(",", "").replace("(", "-").replace(")", "")
        if not text:
            return False
        try:
            float(text)
            return True
        except:
            if text.endswith("%"):
                try:
                    float(text[:-1])
                    return True
                except:
                    return False
            return False
    
    numeric_count = sum(1 for row in table_data for cell in row 
                        if is_numeric(str(cell).strip()))
    numeric_ratio = numeric_count / max(total_cells, 1)
    # 金融表格数值占比通常在0.3-0.8之间
    numeric_score = min(numeric_ratio * 2, 1.0) if numeric_ratio < 0.5 else 1.0
    scores.append((numeric_score, 0.25))
    
    # ----- 因子4：有表格线加分（权重0.15）-----
    line_bonus = 0.15 if has_border else 0.0
    scores.append((line_bonus, 1.0))  # 直接加，不参与加权
    
    # 加权综合
    weighted_sum = sum(score * weight for score, weight in scores[:-1])
    weighted_total = sum(weight for _, weight in scores[:-1])
    confidence = weighted_sum / weighted_total + line_bonus
    
    return min(1.0, max(0.0, confidence))
```

---

## 4. `extract_text_tables()` 修改指引

```python
def extract_text_tables(self, pdf_path, max_pages=None):
    """
    提取文本型PDF中的表格 — v2版本
    修改点：每页内部的表格检测+重建逻辑
    """
    import fitz
    import pdfplumber
    import statistics  # 新增：用于统计计算
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    if max_pages:
        total_pages = min(max_pages, total_pages)
    
    results = []
    
    for page_num in range(total_pages):
        page = doc[page_num]
        page_rect = page.rect
        
        # ---- 提取原始数据 ----
        words_data = page.get_text("words")
        # words_data格式: [(x0, y0, x1, y1, "word", block_no, line_no, word_no)]
        
        # 转为统一格式
        words = []
        for w in words_data:
            words.append({
                "x0": w[0], "y0": w[1],
                "x1": w[2], "y1": w[3],
                "text": w[4],
                "baseline": w[3],  # y1 = baseline
            })
        
        drawings_raw = page.get_drawings()
        drawings = []
        for d in drawings_raw:
            rect = d["rect"]
            w = rect.width
            h = rect.height
            drawing = {
                "type": "line" if (w < h * 0.3 or h < w * 0.3) else "rect",
                "direction": "h" if w > h * 5 else ("v" if h > w * 5 else None),
                "x0": rect.x0, "y0": rect.y0,
                "x1": rect.x1, "y1": rect.y1,
                "color": d.get("color"),
                "width": d.get("width", 1),
                "fill": d.get("fill"),
            }
            drawings.append(drawing)
        
        # ---- 金融关键词过滤（复用现有逻辑）----
        full_text = " ".join(w["text"] for w in words)
        financial_keywords = ["万元", "元", "百万", "十亿", "%", "比率",
                             "资产", "负债", "收入", "利润", "现金", "股东",
                             "资本", "充足率", "率", "额", "数"]
        has_financial = any(kw in full_text for kw in financial_keywords)
        if not has_financial or len(full_text) < 50:
            continue  # 跳过非金融页
        
        # ---- 表格区域定位 ----
        table_regions = self._detect_table_region(drawings, page_rect.width, page_rect.height)
        if not table_regions:
            # 无框表格区域检测
            table_regions = self._detect_table_region_by_text(words, page_rect.width, page_rect.height)
        
        if not table_regions:
            continue  # 无表格区域，跳过
        
        # ---- 为每个表格区域提取数据 ----
        for region in table_regions:
            rx0, ry0, rx1, ry1 = region
            
            # 过滤出区域内的words
            region_words = [w for w in words 
                           if rx0 <= w["x0"] <= rx1 and ry0 <= w["y0"] <= ry1]
            
            if len(region_words) < 3:
                continue
            
            # ---- 行边界检测 ----
            row_bounds = self._detect_horizontal_lines(page, region_words, drawings)
            
            if len(row_bounds) < 2:
                # 行数太少，不是表格
                continue
            
            # ---- 列边界检测 ----
            col_bounds = self._detect_vertical_lines(page, region_words, drawings)
            
            if len(col_bounds) < 3:
                # 少于2列，不是表格
                continue
            
            # ---- 网格填充 ----
            table_data = self._assign_words_to_grid(region_words, row_bounds, col_bounds)
            
            if not table_data or len(table_data) < 2:
                continue
            
            # ---- 行列规范化 ----
            table_data = self._normalize_table_columns(table_data)
            
            # ---- 置信度评分 ----
            has_border = bool([d for d in drawings 
                              if d["direction"] in ("h", "v")])
            confidence = self._compute_table_confidence(table_data, has_border, words)
            
            results.append({
                "page": page_num + 1,
                "type": "table",
                "data": table_data,
                "text": full_text,
                "extractor": "v2_position_based",
                "confidence": confidence,
                "rows": len(table_data),
                "cols": len(col_bounds) - 1,
                "has_border": has_border,
            })
    
    doc.close()
    
    # ---- 保留现有的合并逻辑 ----
    results = self._merge_tables_on_same_page(results)
    
    return results
```

**新方法需要添加到类中**（这些方法需要在 `PDFProcessor` 类内部或作为模块级函数）：

```python
# 在 PDFProcessor 类内部新增以下方法：
# _detect_table_region()
# _detect_table_region_by_text()
# _detect_horizontal_lines()
# _compute_dynamic_y_threshold()
# _group_words_into_rows()
# _detect_vertical_lines()
# _cluster_1d()
# _assign_words_to_grid()
# _compute_table_confidence()
```

---

## 5. 保留不动的方法

以下现有方法保持不动，v2版本依然使用：

| 方法 | 文件位置 | 用途 |
|------|---------|------|
| `__init__()` | PDFProcessor类 | 加载配置 |
| `is_image_pdf()` | PDFProcessor类 | 图片PDF检测 |
| `_normalize_table_columns()` | PDFProcessor类 | 列数标准化 |
| `_merge_tables_on_same_page()` | PDFProcessor类 | 同页多表格合并 |
| `pdf_to_images()` | PDFProcessor类 | PDF转图片 |
| `extract_text_tables()` | PDFProcessor类 | **入口，v2版本修改内部调用** |

---

## 6. 参数调优指南

设计为「方便人工修改」，关键可调参数集中在以下位置：

```python
# === v2参数配置（集中在此处，方便调参）===
V2_CONFIG = {
    # 行分组
    "y_threshold_factor": 0.4,       # 动态阈值：中位gap × 因子
    "y_threshold_min": 2.0,         # 最小值
    "y_threshold_max": 15.0,        # 最大值
    
    # 列检测
    "align_tolerance": 4.0,         # 对齐聚簇容差(pt)
    "gap_factor": 0.3,              # gap阈值：中位gap + stdev × 因子
    "gap_min": 10.0,                # gap最小值
    
    # 表格区域
    "table_min_width_ratio": 0.3,   # 表格最小宽度/页宽
    "table_min_height": 20.0,       # 表格最小高度
    "density_grid": 10,             # 文本密度网格数
    "density_threshold": 0.8,       # 密度阈值(×平均值倍数)
    
    # 单元格分配
    "row_margin_factor": 0.2,       # 行分配允许越界比例
    
    # 置信度
    "confidence_col_weight": 0.35,  # 列数一致性权重
    "confidence_empty_weight": 0.25, # 空值率权重
    "confidence_num_weight": 0.25,  # 数值占比权重
    "confidence_line_bonus": 0.15,  # 表格线加分
    
    # 过滤
    "financial_keywords": [         # 金融关键词
        "万元", "元", "百万", "十亿", "%", "比率",
        "资产", "负债", "收入", "利润", "现金", "股东",
        "资本", "充足率", "率", "额", "数"
    ],
    "min_text_length": 50,          # 最小文本长度(用于关键词过滤)
    
    # pdfplumber降级
    "pdfplumber_min_words": 20,     # 单页最低word数
    "pdfplumber_min_row_words": 3,  # 每行最低word数
}
```

---

## 7. 验证与测试

### 7.1 单元测试

```python
# 测试动态阈值
def test_dynamic_y_threshold():
    words = [{"y0": 100}, {"y0": 104}, {"y0": 108},  # 行1: 100,104,108 → gap≈4
             {"y0": 115}, {"y0": 119}, {"y0": 123},  # 行2: 115,119,123 → gap≈4
             {"y0": 130}, {"y0": 134}, {"y0": 138}]  # 行3: 130,134,138 → gap≈4
    threshold = compute_dynamic_y_threshold(words)
    assert 3.0 < threshold < 6.0, f"Expected ~4×0.4=1.6, got {threshold}"

# 测试列检测
def test_column_detection():
    # 情景：3列，x0分布在72, 200, 350
    page = MockPage(width=500)
    words = [
        {"x0": 72, "x1": 150, "text": "资产"},
        {"x0": 200, "x1": 280, "text": "2023"},
        {"x0": 350, "x1": 430, "text": "2024"},
    ]
    cols = detect_vertical_lines(page, words, [])
    assert len(cols) >= 4  # 应该有3列 → 4条线
```

### 7.2 端到端测试

无需修改 `run_complete_workflow.py` 或 `main.py`，只需：
1. 运行程序打开一个已处理过的 PDF（从缓存中清除该文件的缓存）
2. 观察提取结果是否行列正确
3. 对比处理前后的 Excel 输出

---

## 8. 回退方案

如果 v2 在某些 PDF 上表现不如原版，修改 `processor.py` 的 `extract_text_tables()` 中一行即可回退：

```python
# 在对应页码的回调中：
# v2方法
table_data = self._reconstruct_table_v2(page, words, drawings)
# 或回退到原版方法
# table_data = self._reconstruct_table_from_blocks_improved(text_blocks, page_rect)
```

建议保留原版方法作为备用，必要时可在配置中设置版本切换：
```python
self.config = load_config()
version = self.config.get("extraction_version", "v2")  # 或回退 "v1"
```
