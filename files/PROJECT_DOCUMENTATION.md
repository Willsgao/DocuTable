# 银行年报PDF解析工具 - 开发文档

## 项目概述

基于 PyQt5 的桌面应用，用于从银行年报 PDF 中提取财务表格数据并导出为 Excel。支持文字型 PDF 直接提取和图片型 PDF（扫描件）的 AI 多模态识别，提供完整的预览、编辑、筛选和导出功能。

---

## 项目结构

```
pdf_table_extractor/
├── main.py                    # 程序入口
├── start.bat                  # Windows 一键启动
├── requirements.txt           # 依赖清单
├── pdf_history.json           # 处理历史记录
├── __init__.py                # 包版本 (v1.0.0)
├── config/
│   └── settings.json          # 应用配置
├── data/
│   └── mid_cache/             # 中间数据缓存（按PDF组织）
├── codes/
│   ├── __init__.py
│   ├── ui/                    # UI 管理层（7个管理器 + 主窗口）
│   │   ├── __init__.py        # 导出所有管理器
│   │   ├── main_window.py     # 主窗口 MainWindow（QMainWindow）
│   │   ├── processing_manager.py  # 处理流程管理
│   │   ├── preview_manager.py     # PDF 预览图管理
│   │   ├── table_compare_manager.py  # 对比预览Tab（核心）
│   │   ├── table_editor.py     # 表格编辑器辅助
│   │   ├── history_manager.py  # 历史记录管理
│   │   ├── export_manager.py   # Excel 导出管理
│   │   ├── settings_manager.py # 设置管理
│   │   └── file_manager.py     # 缓存文件管理
│   ├── pdf_extractor/          # PDF 引擎层
│   │   ├── __init__.py         # 导出 processor/utils/widgets
│   │   ├── processor.py        # PDFProcessor / VisionLLM / ExcelExporter / ProcessingWorker
│   │   ├── utils.py            # 配置/历史/缓存/中间数据工具
│   │   └── widgets.py          # PDFPreviewWidget / ZoomableScrollArea / ZoomableTableWidget
│   └── core/                   # 核心算法层（独立模块）
│       ├── extractor.py        # pdfplumber 文本表格提取
│       ├── exporter.py         # 旧版 Excel 导出
│       ├── table_processor.py  # 表格重建（间隙检测/聚类）
│       ├── column_analyzer.py  # 列边界分析
│       └── gap_detector.py     # 间距检测
├── files/                      # 文档
│   └── PROJECT_DOCUMENTATION.md
├── docs/                       # 附加文档
├── dist/ / build/              # PyInstaller 打包输出
└── 银行年报PDF解析工具.spec    # 打包配置
```

---

## 模块详解

### 1. main.py — 程序入口

**职责**：创建 QApplication，实例化 MainWindow 并启动事件循环。

```python
# 关键代码
app = QApplication(sys.argv)
app.setFont(QFont("Microsoft YaHei", 9))
window = MainWindow()
window.show()
sys.exit(app.exec_())
```

---

### 2. codes/ui/main_window.py — 主窗口

**类**：`MainWindow(QMainWindow)`，约 950 行。

**布局**：4 个 Tab 页

| Tab | 名称 | 功能 |
|-----|------|------|
| Tab 0 | 🔄 处理 | 文件选择、模式切换、启动处理、结果预览文字 |
| Tab 1 | 👁 对比预览 | PDF 预览 + 表格数据编辑区（核心工作区） |
| Tab 2 | 📜 历史记录 | 历史列表、筛选、快速预览加载 |
| Tab 3 | ⚙️ 配置 | API Key / Endpoint / Model / 提取器版本设置 |

**7 个子管理器**：

| 管理器 | 实例名 | 职责 |
|--------|--------|------|
| ProcessingManager | `processing_manager` | 处理流程控制、后台线程管理 |
| FileManager | `file_manager` | 缓存文件管理 |
| TableCompareManager | `table_compare_manager` | 对比预览Tab核心逻辑 |
| PreviewManager | `preview_manager` | PDF 预览图生成管理 |
| HistoryManager | `history_manager` | 历史记录加载/筛选 |
| ExportManager | `export_manager` | Excel 导出 |
| SettingsManager | `settings_manager` | 设置读写 |

**核心属性**：
```python
self.current_file          # 当前PDF路径
self.processed_results     # 解析结果字典（含tables列表）
self.preview_images        # 预览图路径列表
self.config                # 配置字典
self.pdf_history           # 历史记录列表
```

**信号/委托机制**：所有用户交互通过主窗口的委托方法转发到对应管理器，松散耦合。

---

### 3. codes/ui/processing_manager.py — 处理流程管理

**类**：`ProcessingManager(QObject)`

| 方法 | 功能 |
|------|------|
| `select_file()` | 打开文件对话框，检查缓存，自动选择处理模式 |
| `start_processing()` | 启动 ProcessingWorker 后台处理线程 |
| `on_processing_finished(result)` | 处理完成：保存缓存、生成预览图、更新UI |
| `generate_pdf_preview_images()` | 每个PDF独立缓存目录，检查磁盘缓存避免重复生成 |

**流程**：
```
select_file() → 检查 mid_cache 缓存
  → 有缓存: 提示"使用已有缓存" / "重新解析"
  → 无缓存: 启用处理按钮

start_processing() → ProcessingWorker (QThread)
  → PDFProcessor 解析
  → 完成信号 → on_processing_finished()
    → save_mid_data() 写入缓存
    → generate_pdf_preview_images() 生成预览图
    → update_preview_tab() 更新列表
```

---

### 4. codes/ui/table_compare_manager.py — 对比预览核心

**类**：`TableCompareManager(QObject)`，约 1200 行，项目中最大的模块。

**功能分组**：

#### 4.1 表格列表与筛选
- `apply_table_filter()` — 按类型筛选页面（全部/✅表格/❌非表格/人工标记）
- `prev_filtered_page()` / `next_filtered_page()` — 筛选结果翻页
- `filter_table()` — 关键词内容筛选（隐藏/显示行）
- `update_table_list()` — 按页面分组更新列表

#### 4.2 PDF 预览显示
- `update_preview_display()` — 根据当前选中的表格，显示对应的 PDF 预览图
- 图片型 PDF：从 `preview_dir` 加载 PyMuPDF 生成的预览
- 文字型 PDF：从 `preview_dir` 加载缓存预览

#### 4.3 表格数据编辑
- `display_table_data(table)` — 显示表格数据到 `table_widget`
- `insert_row()` / `delete_row()` — 插入/删除行
- `insert_column()` / `delete_column()` — 插入/删除列
- `insert_row_above()` / `insert_row_below()` — 上方/下方插入
- `insert_col_left()` / `insert_col_right()` — 左侧/右侧插入
- `batch_insert()` — 批量插入多行/多列（自定义数量、位置）
- `delete_selected_rows()` / `delete_selected_columns()` — 批量删除选中

#### 4.4 撤销/重做
- `save_current_table_state()` — 保存当前状态到撤销栈
- `undo_change()` / `redo_change()` — 撤销/重做

#### 4.5 编辑模式
- `toggle_edit_mode()` — 切换编辑模式（锁定/解锁表格，启用/禁用类型反转）
- `toggle_current_page_type()` — 反转当前页面类型标记（表格↔非表格）
- `_save_all_changes()` — 保存所有更改到中间缓存

#### 4.6 右键菜单
- `show_table_context_menu(position)` — 含撤销/重做、插入行/列、批量插入、删除、复制/粘贴/剪切/填充

---

### 5. codes/ui/preview_manager.py — PDF 预览管理

**类**：`PreviewManager(QObject)`

| 方法 | 功能 |
|------|------|
| `generate_pdf_preview_images()` | 生成预览图（每个PDF独立 `preview` 子目录，永不自动删除） |
| `update_preview_tab()` | 更新左侧 PDF 预览 |
| `show_loading()` / `hide_loading()` | 加载状态提示 |
| `on_zoom_changed(factor)` | 缩放处理 |

**预览图缓存策略**：
- 每个 PDF 在 `mid_cache/<pdf_name>/preview/` 下独立存储
- 文件名：`preview_0.png`、`preview_1.png` ...
- 图片型 PDF 优先使用 `preview_dir` 中的缓存
- 优先级：预览目录缓存 → 图片缓存目录 → PyMuPDF 逐页生成

---

### 6. codes/ui/history_manager.py — 历史记录管理

**类**：`HistoryManager`

| 方法 | 功能 |
|------|------|
| `refresh_history_list()` | 刷新历史记录表格（支持筛选：全部/成功/图片PDF/部分失败） |
| `on_preview_button_clicked(row)` | 点击预览按钮：加载缓存、切换Tab、显示加载状态 |
| `add_to_history()` | 添加新历史记录 |
| `delete_history_record()` | 删除单条记录 |
| `clear_pdf_history()` | 清空所有历史 |

**历史记录加载流程**：
```
点击"预览"按钮
  → 从 mid_cache 加载 data.json
  → 设置 processed_results
  → generate_pdf_preview_images() 加载/生成预览图
  → update_preview_tab() 刷新列表（自动重置筛选为"全部"）
  → 切换到对比预览Tab
```

---

### 7. codes/ui/export_manager.py — 导出管理

**类**：`ExportManager`

| 方法 | 功能 |
|------|------|
| `export_to_excel()` | 导出 processed_results 到 Excel |
| **导出格式**：每页一个 Sheet，多表格用空行分隔 |
| **输出目录**：`data/out_puts/` |

---

### 8. codes/pdf_extractor/processor.py — PDF 处理引擎

**类**：

| 类名 | 职责 |
|------|------|
| `PDFProcessor` | PDF 类型检测、文本表格提取、图片PDF处理 |
| `VisionLLM` | 调用多模态 API 识别扫描件表格 |
| `ExcelExporter` | 处理结果 → Excel 文件 |
| `ProcessingWorker(QThread)` | 后台线程运行处理器 |

**PDF 类型检测**：通过 `set()` 取前 5 页文本字符去重，字符少于 20 判定为图片型。

---

### 9. codes/pdf_extractor/widgets.py — UI 组件

#### PDFPreviewWidget(QWidget)
| 方法 | 功能 |
|------|------|
| `set_preview(img_path, page_num, pdf_path)` | 设置预览图，加载PDF页面供文字提取 |
| `copy_selection()` | 复制选中区域的文字（文本型PDF）或截图（图片型PDF） |
| `copy_screenshot()` | 从原始预览图裁剪选中区域（高清截图） |
| `copy_all_text()` | 复制当前页全部文字 |
| `contextMenuEvent()` | 右键菜单：复制文字/截图/清除 |
| `_auto_fit_to_viewport()` | 自动缩放适应视口 |

#### ZoomableScrollArea(QScrollArea)
- Ctrl+滚轮缩放预览图

#### ZoomableTableWidget(QTableWidget)
- Ctrl+滚轮缩放，拖拽填充，剪贴板操作（Ctrl+C/V/X）
- 表头右键筛选菜单（按列唯一值勾选过滤行）
- 撤销/重做信号

---

### 10. codes/pdf_extractor/utils.py — 工具函数

| 函数 | 功能 |
|------|------|
| `load_config()` / `save_config()` | 配置读写 (`config/settings.json`) |
| `load_pdf_history()` / `save_pdf_history()` | 历史记录读写 (`pdf_history.json`) |
| `load_mid_data(path)` / `save_mid_data(path, data)` | 中间数据缓存读写 |
| `get_pdf_preview_dir(pdf_path)` | 获取PDF的预览图缓存目录 |
| `get_project_root()` | 获取项目根路径 |
| `cleanup_temp_files()` | 退出时清理临时文件 |

---

### 11. codes/core/ — 核心算法层（独立模块）

可在主程序外独立使用的 PDF 表格提取工具链：

| 模块 | 功能 |
|------|------|
| `extractor.py` | pdfplumber 逐页文本提取、表格检测 |
| `table_processor.py` | 文本位置聚类、表格结构重建（行/列分组） |
| `gap_detector.py` | 基于间距的模式检测（空行判定、段落分隔） |
| `column_analyzer.py` | 列边界分析、对齐方式检测 |
| `exporter.py` | 独立 Excel 导出 |

---

## 数据流

### 主流程

```
PDF文件
  └─→ [ProcessingManager]
        ├─→ 检测是文字型还是图片型
        ├─→ ProcessingWorker (后台线程)
        │     ├─→ PDFProcessor.extract_text_tables() 或 VisionLLM
        │     └─→ 返回 processed_results
        ├─→ save_mid_data() 写入中间缓存
        ├─→ generate_pdf_preview_images() 生成预览图
        ├─→ update_preview_tab() 刷新列表
        └─→ 添加历史记录

用户在预览Tab中
  ├─→ 浏览表格列表 → update_preview_display()
  │     ├─→ 显示 PDF 预览图
  │     └─→ 显示表格数据
  ├─→ 编辑数据 → display_table_data() → undo/redo
  ├─→ 筛选 → apply_table_filter() / filter_table()
  ├─→ 反转类型/标记 → 保存到 processed_results
  └─→ 导出 → ExportManager → Excel
```

### 历史记录加载流程

```
点击"预览"按钮
  ├─→ load_mid_data() 加载缓存
  ├─→ 设置 processed_results
  ├─→ generate_pdf_preview_images()（检测磁盘缓存，避免重复生成）
  ├─→ update_preview_tab()（自动重置筛选为"全部"）
  └─→ 切换到对比预览Tab
```

---

## 数据结构

### processed_results 格式
```python
{
    "success": True/False,
    "is_image_pdf": True/False,        # 是否为图片型PDF
    "tables": [                        # 表格列表
        {
            "page": 1,                 # PDF页码（1-based）
            "type": "ai" / "failed" / "excel" / "manual",
            "parse_status": "success" / "failed",
            "parse_type": "ai" / "text" / "excel",
            "parse_message": "...",
            "data": [                  # 二维表格数据
                ["科目", "金额"],
                ["资产", 1000],
                ...
            ],
            "is_manual": False,        # 是否为人工标记
        },
        ...
    ],
    "total_tables": 174,
    "success_count": 150,
    "empty_count": 10,
    "failed_count": 14,
    "total_pages": 174,
    "image_cache_dir": "...",          # 图片型PDF的原始截图目录
}
```

### 配置格式 (config/settings.json)
```json
{
    "api_key": "sk-xxx",
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "default_mode": "auto"
}
```

---

## 功能清单

### PDF 处理
- [x] 自动检测 PDF 类型（文字型/图片型）
- [x] 文字型 PDF 直接提取表格
- [x] 图片型 PDF 调用多模态 LLM 识别
- [x] 后台线程处理（不阻塞UI）
- [x] 中间数据缓存（避免重复解析）

### 对比预览
- [x] PDF 预览图显示（自动适应视口 + Ctrl+滚轮缩放）
- [x] 表格列表按页浏览
- [x] 三种筛选：页面类型（✅/❌）、关键词搜索、列表翻页
- [x] 表格数据编辑（插入/删除行/列，批量插入，撤销/重做）
- [x] 编辑模式锁定/解锁
- [x] 页面类型反转（表格↔非表格）
- [x] 右键菜单（插入/删除/复制/粘贴/剪切/填充）
- [x] 表头右键列筛选（按唯一值勾选过滤行）
- [x] 选中区域统计计算（总和/平均值/数量）
- [x] 复制选中区域文字或截图

### 历史记录
- [x] 处理历史列表（按文件去重）
- [x] 四种筛选模式（全部/成功/图片/部分失败）
- [x] 一键预览加载（含加载状态动画）
- [x] 单条删除/全部清空

### 导出
- [x] Excel 导出（每页一个 Sheet）
- [x] 自动保存编辑到缓存

### 配置
- [x] API Key / Endpoint / Model 设置
- [x] 提取器版本选择

---

## 信号/槽机制

| 信号 | 发送者 | 接收者 | 用途 |
|------|--------|--------|------|
| `processing_finished(dict)` | ProcessingManager | MainWindow → 全局 | 处理完成 |
| `processing_error(str)` | ProcessingManager | MainWindow → 全局 | 处理错误 |
| `progress_updated(int, str)` | ProcessingManager | MainWindow → 全局 | 进度更新 |
| `file_selected(str, dict)` | ProcessingManager | MainWindow → 全局 | 文件选中 |
| `clicked_rect_changed(QRect)` | PDFPreviewWidget | MainWindow → 信号槽 | 单击选中 |
| `data_about_to_change()` | ZoomableTableWidget | TableCompareManager | 数据变化前 |
| `undo_requested()` / `redo_requested()` | ZoomableTableWidget | TableCompareManager | 撤销/重做 |
| `itemClicked` | table_list_widget | `on_table_selected` | 列表选中 |

---

## 依赖项

```
PyQt5>=5.15          # GUI 框架
PyMuPDF>=1.23.0      # PDF 渲染与文本提取
pdfplumber>=0.10.0   # PDF 表格提取
openpyxl>=3.1.0      # Excel 读写
requests>=2.28.0     # HTTP 请求（LLM API）
```

---

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python main.py

# 或双击 start.bat

# 打包（Windows）
python build.py
```

---

## 缓存目录说明

```
data/mid_cache/
└── <日期>-<证券代码>-<文件名>/
    ├── data.json          # 解析结果（processed_results）
    └── preview/           # PDF 预览图（PNG）
        ├── preview_0.png
        ├── preview_1.png
        └── ...
```

- 每个 PDF 独立目录，永不自动删除
- 预览图在目录存在时直接复用，避免重复生成
- 修改后的数据通过 `save_mid_data()` 覆盖写入

---

## 打包说明

```bash
python build.py
```

使用 PyInstaller 打包为单文件 exe，输出到 `dist/` 目录。打包配置见 `银行年报PDF解析工具.spec`。

---

## 潜在优化方向

1. **性能**：多线程并行处理、pandas 加速数据处理
2. **功能**：批量处理多个 PDF、表格数据校验规则
3. **UI**：数据对比视图、更精细的进度反馈
4. **测试**：单元测试、集成测试
5. **稳定性**：跨PDF切换时的状态清理、异常恢复

---

## 作者

高玉伟
