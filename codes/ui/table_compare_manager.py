# -*- coding: utf-8 -*-
"""
对比预览Tab管理模块
负责表格对比预览、筛选、导航、编辑等功能
"""
import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QApplication,
    QListWidget, QListWidgetItem, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QSplitter, QMenu, QApplication, QSizePolicy, QMessageBox, QShortcut,
    QDialog, QRadioButton, QSpinBox, QDialogButtonBox, QInputDialog,
    QTextBrowser
)
from PyQt5.QtGui import QKeySequence, QColor
from PyQt5.QtCore import Qt, QEvent, QObject, QTimer, QThread, pyqtSignal

from codes.pdf_extractor import (
    ZoomableTableWidget, save_mid_data,
    load_ai_correction_cache, save_ai_correction_cache
)
from codes.pdf_extractor.processor import _auto_merge_split_tables
from codes.pdf_extractor.ai_correction import RuleChecker, LLMCorrector
from codes.ui.ai_correction_dialog import PromptEditDialog
from codes.ui.validation_dialog import CrossValidationDialog, CrossValidationWorker, LiteparseTableDialog
from codes.table_validator.cell_differ import diff_table_with_liteparse, classify_rows_with_liteparse, _cluster_items_by_y, _normalize_for_search


class TableCompareManager(QObject):
    """对比预览Tab管理器"""
    
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        
        # 撤销/重做
        self.undo_stack = []
        self.redo_stack = []
        self.max_history = 100
        
        # 筛选
        self.filtered_indices = []
        self.filtered_index = 0
        
        # 原始/清洗数据切换（三层：raw → original → data）
        self.showing_data_layer = 0  # 0=data(清洗后), 1=original_data(清洗前), 2=raw_data(提取原样)
        
        # 差异标注模式
        self.diff_mode = False
        self.diff_highlight_btn = None
        self.diff_stats_label = None
        self._liteparse_cache = None  # 当前 PDF 的 liteparse 缓存
        
        # 控件引用（由 init_ui 设置）
        self.table_widget = None
        self.table_list_widget = None
        self.table_type_filter = None
        self.table_type_label = None
        self.filter_nav_label = None
        self.filter_count_label = None
        self.filter_input = None
        self.stats_label = None
        self.page_info_label = None
        self.prev_page_btn = None
        self.next_page_btn = None
        self.first_page_btn = None
        self.last_page_btn = None
        self.prev_filtered_btn = None
        self.next_filtered_btn = None
        self.toggle_table_type_btn = None
        self.save_status_btn = None
        self.goto_export_btn = None
        
        # 编辑状态
        self.edit_mode = False
        self.has_unsaved_changes = False
        
        # 锁定表格状态
        self.table_locked = False
        self.locked_table_data = None  # 锁定时保持的表格数据
        
        # 自动保存定时器
        self._auto_save_timer = QTimer()
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.timeout.connect(self._do_auto_save)
        self._auto_save_interval = 2000  # 2秒延迟保存
        
        # PDF预览 - 从main_window获取
        self.pdf_preview_widget = None
        self.pdf_scroll_area = None
    
    def init_ui(self, table_group):
        """初始化表格相关的UI组件，添加到表格区域"""
        table_layout = table_group.layout()
        if table_layout is None:
            table_layout = QVBoxLayout(table_group)
        
        # 创建分割器（列表 + 表格）
        self.table_splitter = QSplitter(Qt.Vertical)
        
        # header区域
        header_widget = QWidget()
        header_widget.setMinimumHeight(120)  # 拖手柄时保留最小区域，不会消失
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        # 筛选工具栏
        filter_widget = QWidget()
        filter_layout = QHBoxLayout(filter_widget)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        
        # 页面类型筛选
        filter_layout.addWidget(QLabel("筛选类型:"))
        self.table_type_filter = QComboBox()
        self.table_type_filter.addItems(["全部", "✅ 表格", "❌ 非表格", "✅ 表格-人工", "❌ 非表格-人工"])
        self.table_type_filter.setToolTip("筛选显示的页面类型")
        self.table_type_filter.currentIndexChanged.connect(self.on_table_type_filter_changed)
        self.table_type_filter.setMinimumWidth(100)
        filter_layout.addWidget(self.table_type_filter)
        
        # 筛选类别内的翻页按钮
        self.prev_filtered_btn = QPushButton("◀ 上一页")
        self.prev_filtered_btn.setToolTip("上一个筛选页面")
        self.prev_filtered_btn.setMaximumWidth(100)
        self.prev_filtered_btn.setMinimumHeight(32)
        self.prev_filtered_btn.setStyleSheet("""
            QPushButton { border: 2px solid #3498DB; border-radius: 6px;
                          background-color: #EBF5FB; font-weight: bold; color: #2C3E50;
                          padding: 4px 8px; font-size: 13px; }
            QPushButton:hover { background-color: #D4E6F1; border-color: #2980B9; }
            QPushButton:pressed { background-color: #A9CCE3; }
            QPushButton:disabled { background-color: #f0f0f0; color: #aaa; border-color: #ddd; }
        """)
        self.prev_filtered_btn.clicked.connect(self.prev_filtered_page)
        filter_layout.addWidget(self.prev_filtered_btn)
        
        self.next_filtered_btn = QPushButton("下一页 ▶")
        self.next_filtered_btn.setToolTip("下一个筛选页面")
        self.next_filtered_btn.setMaximumWidth(100)
        self.next_filtered_btn.setMinimumHeight(32)
        self.next_filtered_btn.setStyleSheet("""
            QPushButton { border: 2px solid #3498DB; border-radius: 6px;
                          background-color: #EBF5FB; font-weight: bold; color: #2C3E50;
                          padding: 4px 8px; font-size: 13px; }
            QPushButton:hover { background-color: #D4E6F1; border-color: #2980B9; }
            QPushButton:pressed { background-color: #A9CCE3; }
            QPushButton:disabled { background-color: #f0f0f0; color: #aaa; border-color: #ddd; }
        """)
        self.next_filtered_btn.clicked.connect(self.next_filtered_page)
        filter_layout.addWidget(self.next_filtered_btn)
        
        filter_layout.addSpacing(10)
        
        # 切换页面类型按钮
        self.toggle_table_type_btn = QPushButton("🔄 反转类型")
        self.toggle_table_type_btn.setToolTip("切换当前页面类型的标记（表格↔非表格）")
        self.toggle_table_type_btn.clicked.connect(self.toggle_current_page_type)
        self.toggle_table_type_btn.setEnabled(False)  # 初始禁用，进入编辑模式后才可用
        self.toggle_table_type_btn.setStyleSheet("""
            QPushButton { background-color: #3498DB; color: white; padding: 2px 10px; border-radius: 4px; }
            QPushButton:hover { background-color: #2980B9; }
            QPushButton:disabled { background-color: #BDC3C7; color: #ecf0f1; }
        """)
        filter_layout.addWidget(self.toggle_table_type_btn)
        
        # 页面类型标签
        self.table_type_label = QLabel("状态: 表格页面")
        self.table_type_label.setStyleSheet("""
            QLabel { color: #27AE60; font-weight: bold; padding: 2px 8px;
                     background-color: #E8F8F5; border-radius: 4px; border: 1px solid #27AE60; }
        """)
        filter_layout.addWidget(self.table_type_label)
        
        # 编辑模式按钮
        self.edit_mode_btn = QPushButton("✏️ 开始编辑")
        self.edit_mode_btn.setToolTip("进入编辑模式，可修改页面类型标记")
        self.edit_mode_btn.clicked.connect(self.toggle_edit_mode)
        self.edit_mode_btn.setStyleSheet("""
            QPushButton { background-color: #E67E22; color: white; padding: 2px 10px; border-radius: 4px;
                          font-weight: bold; }
            QPushButton:hover { background-color: #D35400; }
        """)
        filter_layout.addWidget(self.edit_mode_btn)
        
        # 导航标签
        self.filter_nav_label = QLabel("第0/0页")
        self.filter_nav_label.setStyleSheet("color: #666; padding-left: 10px; font-weight: bold;")
        filter_layout.addWidget(self.filter_nav_label)
        
        self.filter_count_label = QLabel("共 0 页")
        self.filter_count_label.setStyleSheet("color: #888; padding-left: 10px;")
        filter_layout.addWidget(self.filter_count_label)
        
        # 保存修改状态按钮
        self.save_status_btn = QPushButton("💾 保存")
        self.save_status_btn.setToolTip("保存人工修改的页面状态标记")
        self.save_status_btn.clicked.connect(self.save_page_status)
        self.save_status_btn.setEnabled(False)  # 初始禁用，进入编辑模式后才可用
        self.save_status_btn.setStyleSheet("""
            QPushButton { background-color: #27AE60; color: white; padding: 2px 12px; border-radius: 4px; }
            QPushButton:hover { background-color: #229954; }
            QPushButton:disabled { background-color: #BDC3C7; color: #ecf0f1; }
        """)
        filter_layout.addWidget(self.save_status_btn)
        
        filter_layout.addStretch(1)
        header_layout.addWidget(filter_widget)
        
        # 上下文文本展示区
        self.context_text_browser = QTextBrowser()
        self.context_text_browser.setReadOnly(True)
        self.context_text_browser.setFixedHeight(80)
        self.context_text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.context_text_browser.setPlaceholderText("选中表格后显示标题和上下文...")
        self.context_text_browser.setStyleSheet("""
            QTextBrowser {
                border: 2px solid #F39C12; border-radius: 4px;
                background-color: #FEF9E7; color: #7D6608;
                font-size: 12px; padding: 4px;
            }
        """)
        header_layout.addWidget(self.context_text_browser)
        
        # 表格列表
        self.table_list_widget = QListWidget()
        self.table_list_widget.setMinimumHeight(30)
        self.table_list_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.table_list_widget.itemClicked.connect(self.on_table_selected)
        self.table_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_list_widget.customContextMenuRequested.connect(self._show_table_list_menu)
        header_layout.addWidget(self.table_list_widget)
        
        # 操作按钮行
        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_widget.setFixedHeight(40)

        # 向前合并按钮
        self.merge_prev_btn = QPushButton("⬅️ 向前合并")
        self.merge_prev_btn.setToolTip("将当前表格数据追加到前一个表格的左下方，然后删除当前表格")
        self.merge_prev_btn.setFocusPolicy(Qt.NoFocus)
        self.merge_prev_btn.clicked.connect(self.merge_to_previous)
        self.merge_prev_btn.setStyleSheet("""
            QPushButton { background-color: #3498DB; color: white; border: none; border-radius: 4px; padding: 6px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #2980B9; }
        """)
        btn_layout.addWidget(self.merge_prev_btn)

        # 原始/清洗数据切换按钮
        self.toggle_original_btn = QPushButton("📋 查看原始数据")
        self.toggle_original_btn.setToolTip("切换: 清洗数据 → 清洗前 → 提取原样 (三层追溯)")
        self.toggle_original_btn.setFocusPolicy(Qt.NoFocus)
        self.toggle_original_btn.clicked.connect(self.toggle_original_view)
        self.toggle_original_btn.setStyleSheet("""
            QPushButton { background-color: #8E44AD; color: white; border: none; border-radius: 4px; padding: 6px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #7D3C98; }
        """)
        btn_layout.addWidget(self.toggle_original_btn)

        btn_layout.addSpacing(10)

        # LLM 边界优化按钮（新：LLM边界检测 + liteparse文本填充）
        self.llm_boundary_btn = QPushButton("🔮 LLM 边界优化")
        self.llm_boundary_btn.setToolTip(
            "LLM 分析 liteparse 整页文本，识别表格精确边界\n"
            "自动合并同页碎片 + 跨页续表拼接\n"
            "用 liteparse 精确文本填充每个单元格\n"
            "⚠ 需要已配置 DeepSeek API（配置Tab）"
        )
        self.llm_boundary_btn.setFocusPolicy(Qt.NoFocus)
        self.llm_boundary_btn.clicked.connect(self.on_llm_boundary_clicked)
        self.llm_boundary_btn.setStyleSheet("""
            QPushButton { background-color: #D35400; color: white; padding: 2px 10px; border-radius: 4px;
                          font-weight: bold; font-size: 11px; }
            QPushButton:hover { background-color: #BA4A00; }
            QPushButton:disabled { background-color: #BDC3C7; color: #ecf0f1; }
        """)
        btn_layout.addWidget(self.llm_boundary_btn)

        btn_layout.addSpacing(8)

        # liteparse 表格分割按钮（纯规则，零 API 成本）
        self.segmenter_btn = QPushButton("📊 表格分割验证")
        self.segmenter_btn.setToolTip(
            "仅用 liteparse 内置 table_regions 精确切分表格区域\n"
            "生成完整覆盖度报告，验证分割是否准确完整\n"
            "零 API 成本，纯规则驱动"
        )
        self.segmenter_btn.setFocusPolicy(Qt.NoFocus)
        self.segmenter_btn.clicked.connect(self.on_segmenter_clicked)
        self.segmenter_btn.setStyleSheet("""
            QPushButton { background-color: #16A085; color: white; padding: 2px 10px; border-radius: 4px;
                          font-weight: bold; font-size: 11px; }
            QPushButton:hover { background-color: #138D75; }
            QPushButton:disabled { background-color: #BDC3C7; color: #ecf0f1; }
        """)
        btn_layout.addWidget(self.segmenter_btn)

        btn_layout.addSpacing(8)

        # 差异标注按钮（liteparse 对比，零成本、规则驱动）
        self.diff_highlight_btn = QPushButton("🔍 差异标注 ☐")
        self.diff_highlight_btn.setToolTip(
            "用 liteparse 文本数据行/列对齐对比 pdf2docx 表格\n"
            "标红：同行同列值不一致\n"
            "标黄：疑似多余行（liteparse 中无此行）\n"
            "标橙：疑似多余列（liteparse 该行无此列数据）\n"
            "统计栏：缺失行/列数量\n"
            "零 API 成本，纯规则驱动"
        )
        self.diff_highlight_btn.setFocusPolicy(Qt.NoFocus)
        self.diff_highlight_btn.setCheckable(True)
        self.diff_highlight_btn.clicked.connect(self.toggle_diff_mode)
        self.diff_highlight_btn.setStyleSheet("""
            QPushButton { background-color: #E74C3C; color: white; padding: 2px 10px; border-radius: 4px;
                          font-weight: bold; font-size: 11px; }
            QPushButton:hover { background-color: #CB4335; }
            QPushButton:checked { background-color: #C0392B; color: white; font-weight: bold;
                                  border: 2px solid #F1C40F; }
        """)
        btn_layout.addWidget(self.diff_highlight_btn)
        
        # 删除空格按钮
        self.remove_spaces_btn = QPushButton("🧹 删除空格")
        self.remove_spaces_btn.setToolTip("仅删除每行左右两端的空单元格，内部的保留")
        self.remove_spaces_btn.setFocusPolicy(Qt.NoFocus)
        self.remove_spaces_btn.clicked.connect(self.remove_spaces)
        self.remove_spaces_btn.setStyleSheet("""
            QPushButton { background-color: #E67E22; color: white; border: none; border-radius: 4px; padding: 6px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #D35400; }
        """)
        btn_layout.addWidget(self.remove_spaces_btn)

        # 清洗数据按钮
        self.clean_data_btn = QPushButton("🧼 清洗数据")
        self.clean_data_btn.setToolTip("选中区域只保留数值、千分位、小数点、负号、括号、百分号")
        self.clean_data_btn.setFocusPolicy(Qt.NoFocus)
        self.clean_data_btn.clicked.connect(self.clean_data)
        self.clean_data_btn.setStyleSheet("""
            QPushButton { background-color: #27AE60; color: white; border: none; border-radius: 4px; padding: 6px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #1E8449; }
        """)
        btn_layout.addWidget(self.clean_data_btn)
        
        btn_layout.addSpacing(10)
        
        # 撤销/重做按钮
        self.undo_btn = QPushButton("↩️ 撤销")
        self.undo_btn.setToolTip("撤销上一步操作 (Ctrl+Z)")
        self.undo_btn.setFocusPolicy(Qt.NoFocus)
        self.undo_btn.clicked.connect(self.undo_change)
        self.undo_btn.setMaximumWidth(60)
        btn_layout.addWidget(self.undo_btn)
        
        self.redo_btn = QPushButton("↪️ 重做")
        self.redo_btn.setToolTip("重做操作 (Ctrl+Y)")
        self.redo_btn.setFocusPolicy(Qt.NoFocus)
        self.redo_btn.clicked.connect(self.redo_change)
        self.redo_btn.setMaximumWidth(60)
        btn_layout.addWidget(self.redo_btn)
        
        btn_layout.addSpacing(10)
        
        # 筛选功能
        filter_label2 = QLabel("🔍 筛选:")
        filter_label2.setStyleSheet("color: #555; font-weight: bold;")
        btn_layout.addWidget(filter_label2)
        
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("输入关键词筛选...")
        self.filter_input.setMinimumWidth(150)
        self.filter_input.setStyleSheet("""
            QLineEdit { border: 1px solid #ddd; border-radius: 4px; padding: 2px 8px; background-color: white; }
            QLineEdit:focus { border: 1px solid #5D6D7E; }
        """)
        self.filter_input.textChanged.connect(self.filter_table)
        btn_layout.addWidget(self.filter_input)
        
        self.clear_filter_btn = QPushButton("✕")
        self.clear_filter_btn.setToolTip("清除筛选")
        self.clear_filter_btn.setMaximumWidth(25)
        self.clear_filter_btn.setStyleSheet("""
            QPushButton { border: none; background-color: transparent; color: #999; font-size: 14px; }
            QPushButton:hover { color: #E74C3C; }
        """)
        self.clear_filter_btn.clicked.connect(self.clear_filter)
        btn_layout.addWidget(self.clear_filter_btn)
        
        header_layout.addWidget(btn_widget)
        
        # header区域添加到splitter
        self.table_splitter.addWidget(header_widget)
        self.table_splitter.setChildrenCollapsible(False)  # 拖拽时面板不会消失
        
        # 设置splitter分割线宽度和初始大小
        self.table_splitter.setHandleWidth(8)
        self.table_splitter.setSizes([200, 500])
        
        # 创建表格控件
        self.table_widget = ZoomableTableWidget()
        self.table_widget.setMinimumHeight(80)  # 拖手柄时保留最小区域，不会消失
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.table_widget.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectItems)  # 行列都可选
        self.table_widget.itemSelectionChanged.connect(self.on_selection_changed)
        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self.show_table_context_menu)
        self.table_widget.installEventFilter(self)
        # 点击表格外区域时自动取消选中
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        # 注意：移除 cellClicked 连接，因为 on_table_selected 期望 QTableWidgetItem 参数
        # 选择变化已由 itemSelectionChanged.on_selection_changed 处理
        self.table_widget.cellChanged.connect(self.on_cell_changed)
        self.table_widget.currentCellChanged.connect(self.save_current_table_state)
        
        # 添加快捷键（确保子控件聚焦时也能触发）
        self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self.table_widget)
        self._undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._undo_shortcut.activated.connect(self.undo_change)
        self._redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self.table_widget)
        self._redo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._redo_shortcut.activated.connect(self.redo_change)
        # 连接撤销/重做信号
        self.table_widget.undo_requested.connect(self.undo_change)
        self.table_widget.redo_requested.connect(self.redo_change)
        self.table_widget.setStyleSheet("""
            QTableWidget { gridline-color: #e0e0e0; background-color: white; }
            QTableWidget::item:selected { background-color: #5D6D7E; color: white; }
        """)
        
        self.table_splitter.addWidget(self.table_widget)

        # liteparse 原文展示区（差异标注模式下显示，默认隐藏）
        self.liteparse_browser = QTextBrowser()
        self.liteparse_browser.setReadOnly(True)
        self.liteparse_browser.setMinimumHeight(40)
        self.liteparse_browser.setMaximumHeight(180)
        self.liteparse_browser.setPlaceholderText("liteparse 解析原文（按行聚类）")
        self.liteparse_browser.setOpenExternalLinks(False)
        self.liteparse_browser.anchorClicked.connect(self._on_liteparse_view_toggle)
        self.liteparse_browser.setStyleSheet("""
            QTextBrowser {
                border: 1px solid #5DADE2; border-radius: 4px;
                background-color: #EBF5FB; color: #1A5276;
                font-size: 11px; padding: 4px;
            }
        """)
        self.liteparse_browser.hide()
        # liteparse 视图状态
        self._liteparse_view_mode = "clustered"  # "clustered" | "fulltext"
        self._liteparse_full_text = ""
        self._liteparse_text_items_cache = []
        self.table_splitter.addWidget(self.liteparse_browser)

        # 设置分割线样式
        self.table_splitter.setStyleSheet("""
            QSplitter::handle { background-color: #BDC3C7; height: 3px; }
            QSplitter::handle:hover { background-color: #99A3A4; }
        """)
        table_layout.addWidget(self.table_splitter)
        
        # 统计结果标签
        self.stats_label = QLabel("选中单元格查看统计信息")
        self.stats_label.setStyleSheet("""
            QLabel { background-color: #f8f9fa; border: 1px solid #BDC3C7;
                     border-radius: 4px; padding: 2px 8px; color: #2C3E50; }
        """)
        self.stats_label.setFixedHeight(22)
        table_layout.addWidget(self.stats_label)
        
        # 差异标注统计标签（默认隐藏）
        self.diff_stats_label = QLabel("")
        self.diff_stats_label.setStyleSheet("""
            QLabel { background-color: #FDEDEC; border: 1px solid #E74C3C;
                     border-radius: 4px; padding: 2px 8px; color: #C0392B;
                     font-weight: bold; }
        """)
        self.diff_stats_label.setFixedHeight(22)
        self.diff_stats_label.hide()
        table_layout.addWidget(self.diff_stats_label)
        
        # 锁定表格按钮
        self.lock_table_btn = QPushButton("🔓 解锁")
        self.lock_table_btn.setToolTip("锁定后翻页只更新PDF，表格内容固定不变")
        self.lock_table_btn.setCheckable(True)
        self.lock_table_btn.clicked.connect(self.on_lock_table_toggled)
        self.lock_table_btn.setStyleSheet("""
            QPushButton { background-color: #95A5A6; color: white; padding: 2px 12px; border-radius: 4px; }
            QPushButton:checked { background-color: #E74C3C; color: white; font-weight: bold; }
        """)
        table_layout.addWidget(self.lock_table_btn)
        
        self.goto_export_btn = QPushButton("💾 批量导出")
        self.goto_export_btn.setToolTip("批量导出所有标记为表格的页面数据")
        self.goto_export_btn.clicked.connect(self.batch_export_tables)
        self.goto_export_btn.setEnabled(False)
        table_layout.addWidget(self.goto_export_btn)
        
        # 内部变量
        self.current_preview_index = 0
        self._last_displayed_table_idx = None  # 当前显示在表格控件中的页面对应的原始索引
    
    # ==================== 数据同步 ====================
    
    def _get_current_table_index(self):
        """获取当前显示的表格在 processed_results 中的原始索引"""
        row = self.table_list_widget.currentRow()
        if row < 0 or row >= len(self.filtered_indices):
            return None
        table_idx = self.filtered_indices[row]
        tables = self.main_window.processed_results.get('tables', [])
        if table_idx >= len(tables):
            return None
        return table_idx
    
    def _sync_ui_to_processed_results(self):
        """将UI中的表格数据写回 processed_results（自动保存编辑）"""
        if not self.main_window.processed_results:
            return

        # 表格还没填充过数据，跳过（避免空数据覆盖原始数据）
        if self.table_widget.rowCount() == 0:
            return

        table_idx = self._get_current_table_index()
        if table_idx is None:
            return
        
        tables = self.main_window.processed_results.get('tables', [])
        data = []
        for i in range(self.table_widget.rowCount()):
            row_data = []
            for j in range(self.table_widget.columnCount()):
                item = self.table_widget.item(i, j)
                row_data.append(item.text() if item else "")
            data.append(row_data)
        
        # 根据当前视图状态写入对应的数据键，避免原始数据覆盖清洗数据
        data_key = self._get_current_data_key()
        tables[table_idx][data_key] = data
        self._last_displayed_table_idx = table_idx
        
        # 标记有未保存的更改，并安排自动保存
        self.has_unsaved_changes = True
        self._schedule_auto_save()
    
    def _schedule_auto_save(self):
        """安排延迟自动保存（避免频繁写入磁盘）"""
        # 如果定时器已经在运行，重置它
        if self._auto_save_timer.isActive():
            self._auto_save_timer.stop()
        self._auto_save_timer.start(self._auto_save_interval)
    
    def _do_auto_save(self):
        """执行自动保存到磁盘"""
        if not self.has_unsaved_changes:
            return
        if not self.main_window.processed_results or not self.main_window.current_file:
            return
        try:
            save_mid_data(self.main_window.current_file, self.main_window.processed_results)
            self.has_unsaved_changes = False
            self.main_window.status_bar.showMessage("✓ 已自动保存", 2000)
        except Exception as e:
            print(f"[WARN] 自动保存失败: {e}")

    def _save_previous_page_data(self):
        """保存上次显示的页面数据到 processed_results（用 _last_displayed_table_idx 避免行号变更问题）"""
        if not self.main_window.processed_results or self._last_displayed_table_idx is None:
            return
        if self.table_widget.rowCount() == 0:
            return
        tables = self.main_window.processed_results.get('tables', [])
        if self._last_displayed_table_idx >= len(tables):
            return
        data = []
        for i in range(self.table_widget.rowCount()):
            row_data = []
            for j in range(self.table_widget.columnCount()):
                item = self.table_widget.item(i, j)
                row_data.append(item.text() if item else "")
            data.append(row_data)
        # 根据当前视图状态写入对应的数据键，避免原始数据覆盖清洗数据
        data_key = self._get_current_data_key()
        tables[self._last_displayed_table_idx][data_key] = data
    
    # ==================== 事件处理 ====================
    
    def _on_focus_changed(self, old, new):
        """表格失去焦点时自动取消选中"""
        if old and old == self.table_widget:
            self.table_widget.clearSelection()

    def eventFilter(self, obj, event):
        """事件过滤器"""
        if obj == self.table_widget.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.save_current_table_state()
        return False
    
    def on_table_selected(self, item):
        """表格选中（先保存当前页编辑，再切换显示）"""
        if item:
            self._save_previous_page_data()
            self.filtered_index = self.table_list_widget.currentRow()
            self.update_filter_nav_buttons()
            self.update_preview_display()

    def on_lock_table_toggled(self):
        """锁定表格切换"""
        self.table_locked = self.lock_table_btn.isChecked()
        if self.table_locked:
            self.lock_table_btn.setText("🔒 已锁定")
            # 保存当前表格数据用于锁定
            row = self.table_list_widget.currentRow()
            if row >= 0 and row < len(self.filtered_indices):
                table_idx = self.filtered_indices[row]
                tables = self.main_window.processed_results.get('tables', [])
                if table_idx < len(tables):
                    self.locked_table_data = tables[table_idx]
        else:
            self.lock_table_btn.setText("🔓 解锁")
            self.locked_table_data = None
            self.update_preview_display()

    def _show_table_list_menu(self, pos):
        """表格列表右键菜单：插入/删除表格"""
        item = self.table_list_widget.itemAt(pos)
        if not item:
            return
        row = self.table_list_widget.row(item)
        menu = QMenu()
        change_page_action = menu.addAction("✏️ 修改页号")
        menu.addSeparator()
        insert_before = menu.addAction("在上方插入新表")
        insert_after = menu.addAction("在下方插入新表")
        menu.addSeparator()
        delete_action = menu.addAction("🗑️ 删除此表")
        action = menu.exec_(self.table_list_widget.mapToGlobal(pos))
        if action == change_page_action:
            self._change_table_page(row)
        elif action == insert_before:
            self._insert_blank_table(row, before=True)
        elif action == insert_after:
            self._insert_blank_table(row, before=False)
        elif action == delete_action:
            self._delete_table(row)

    def _insert_blank_table(self, list_row, before=True):
        """在指定列表行位置插入空白表格"""
        tables = self.main_window.processed_results.get('tables', [])
        if list_row < 0 or list_row >= len(self.filtered_indices):
            return
        origin_idx = self.filtered_indices[list_row]
        if origin_idx >= len(tables):
            return
        page = tables[origin_idx].get('page', 0)
        seq = sum(1 for i in range(origin_idx) if tables[i].get('page') == page) + 1
        if before:
            insert_idx = origin_idx
        else:
            insert_idx = origin_idx + 1

        # 创建空白表格（4行×3列起步）
        blank_data = [[""] * 3 for _ in range(4)]
        new_table = {
            "page": page,
            "type": "text",
            "data": blank_data,
            "extractor": "manual",
            "title": f"手动添加-P{page}",
            "parse_status": "success",
            "parse_message": "手动添加",
            "is_manual": True
        }
        tables.insert(insert_idx, new_table)
        self.main_window.processed_results['tables'] = tables
        self.main_window.processed_results['total_tables'] = len(tables)
        self.has_unsaved_changes = True

        # 重建筛选
        self.apply_table_filter(preserve_selection=insert_idx)
        print(f"  [手动] 在P{page}页插入空白表格(位置{insert_idx})")

    def _delete_table(self, list_row):
        """删除指定列表行的表格"""
        tables = self.main_window.processed_results.get('tables', [])
        if list_row < 0 or list_row >= len(self.filtered_indices):
            return
        origin_idx = self.filtered_indices[list_row]
        if origin_idx >= len(tables):
            return
        deleted = tables.pop(origin_idx)
        self.main_window.processed_results['tables'] = tables
        self.main_window.processed_results['total_tables'] = len(tables)
        self.has_unsaved_changes = True

        # 重建筛选，选中相邻项
        new_idx = min(origin_idx, len(tables) - 1) if tables else 0
        self.apply_table_filter(preserve_selection=new_idx)
        print(f"  [手动] 已删除 P{deleted.get('page',0)} 页的表格")
    
    def on_cell_changed(self, item):
        """单元格改变 - 自动保存编辑到数据源"""
        self.save_current_table_state()
        self._sync_ui_to_processed_results()
    
    def on_selection_changed(self):
        """选择改变 - 自动计算选中区域统计"""
        selection = self.table_widget.selectedRanges()
        if selection:
            self.calculate_selected()  # 自动计算统计数据
        else:
            self.stats_label.setText("选中单元格查看统计信息")
    
    # ==================== 筛选功能 ====================
    
    def on_table_type_filter_changed(self):
        """筛选类型改变"""
        self.apply_table_filter()
    
    def apply_table_filter(self, preserve_selection=None):
        """应用筛选 - 只显示匹配类型的表格
        Args:
            preserve_selection: 可选，筛选后保持选中的原始表格索引
        """
        print(f"\n[DEBUG] === apply_table_filter 开始 ===")
        print(f"[DEBUG] processed_results 存在: {self.main_window.processed_results is not None}")
        if not self.main_window.processed_results:
            print(f"[DEBUG] processed_results 为空，提前返回")
            return

        # 重置页面跟踪状态，防止跨PDF污染
        # (_save_previous_page_data 不在此处调用，因为每次编辑时 _sync_ui_to_processed_results 已即时保存，
        #  且切换PDF后 _last_displayed_table_idx 指向旧PDF的索引，会错误地将旧数据写入新PDF)
        self._last_displayed_table_idx = None

        filter_text = self.table_type_filter.currentText()
        tables = self.main_window.processed_results.get('tables', [])
        print(f"[DEBUG] 筛选类型: {filter_text}, tables数量: {len(tables)}")
        
        # 收集所有原始索引（用于映射）
        all_indices = [i for i in range(len(tables))]
        
        self.filtered_indices = []
        for i, table in enumerate(tables):
            is_success = table.get('parse_status') == 'success'
            
            if filter_text == "全部":
                self.filtered_indices.append(i)
            elif filter_text == "✅ 表格" and is_success:
                self.filtered_indices.append(i)
            elif filter_text == "❌ 非表格" and not is_success:
                self.filtered_indices.append(i)
            elif filter_text == "✅ 表格-人工" and is_success and table.get('is_manual', False):
                self.filtered_indices.append(i)
            elif filter_text == "❌ 非表格-人工" and not is_success and table.get('is_manual', False):
                self.filtered_indices.append(i)
        
        self.filtered_index = 0
        self.update_filter_nav_buttons()
        
        # 清空并重新添加匹配的项
        self.table_list_widget.blockSignals(True)
        self.table_list_widget.clear()
        
        # 按页号分配序号
        page_seq = {}
        for idx in self.filtered_indices:
            table = tables[idx]
            page = table.get('page', 0)
            page_seq[page] = page_seq.get(page, 0) + 1
            is_text = table.get('type') == 'text'
            status_icon = "📝" if is_text else ("✔" if table.get('parse_status') == 'success' else "✘")
            ext = table.get('extractor', '')
            if ext == "manual":
                ext_tag = "M"
            elif ext == "docx_text":
                ext_tag = "T"
            elif ext.startswith("docx"):
                ext_tag = "D"
            else:
                ext_tag = "V2"
            # 标题优先级：llm_title > title > context首行 > data第一格
            title = table.get('llm_title', '')
            if not title:
                title = table.get('title', '')
            if not title:
                ctx = table.get('context_text', '')
                if ctx:
                    title = ctx.split('\n')[0].strip()[:12]
            if not title:
                data = table.get('data', [])
                for row in data:
                    for cell in row:
                        if cell and str(cell).strip():
                            title = str(cell).strip()[:8]
                            break
                    if title:
                        break
            if not title:
                ctx = table.get('context_text', '')
                if ctx:
                    title = ctx.split('\n')[0].strip()[:8]
            title_str = f" {title}" if title else ""
            # LLM 生成的标题加星号标记
            llm_mark = "✨" if table.get('llm_title') else ""
            # 建议合并标记（同页拆分检测）
            merge_mark = ""
            merge_tooltip = ""
            if table.get('_suggest_merge_to') is not None:
                merge_mark = "🔗"
                reason = table.get('_merge_reason', '疑似被拆分的同一表格')
                merge_tooltip = f"建议合并到上一个表格（{reason}）"
                merge_to = table.get('_suggest_merge_to')
                if merge_to is not None:
                    merge_tooltip += f" → 表格#{merge_to}"

            item_text = f"{status_icon} P{page}_{page_seq[page]} [{ext_tag}]{llm_mark}{merge_mark}{title_str}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, idx)  # 保存原始索引
            if merge_tooltip:
                item.setToolTip(merge_tooltip)
            self.table_list_widget.addItem(item)
        
        self.table_list_widget.blockSignals(False)
        
        # 选中目标项
        if preserve_selection is not None and preserve_selection in self.filtered_indices:
            # 在过滤后的列表中找到原始索引对应的位置
            target_row = self.filtered_indices.index(preserve_selection)
            self.table_list_widget.setCurrentRow(target_row)
            self.filtered_index = target_row
            self.update_filter_nav_buttons()
        elif preserve_selection is not None:
            # preserve_selection 不在筛选列表中了（比如反转类型后被过滤掉）
            # 尝试选中下一个位置，而不是跳回第一页
            # 找到 preserve_selection 在原始列表中的位置，然后找下一个在筛选列表中的项
            original_idx = preserve_selection
            next_target = None
            for i, idx in enumerate(self.filtered_indices):
                if idx > original_idx:
                    next_target = i
                    break
            if next_target is not None:
                self.table_list_widget.setCurrentRow(next_target)
                self.filtered_index = next_target
                self.update_filter_nav_buttons()
            elif self.filtered_indices:
                # 没有下一个了，选中最后一个
                last_idx = len(self.filtered_indices) - 1
                self.table_list_widget.setCurrentRow(last_idx)
                self.filtered_index = last_idx
                self.update_filter_nav_buttons()
        elif self.filtered_indices:
            # 默认选中第一个
            self.table_list_widget.setCurrentRow(0)
        
        print(f"[DEBUG] filtered_indices 长度: {len(self.filtered_indices)}")
        print(f"[DEBUG] 当前选中行: {self.table_list_widget.currentRow()}")
        print(f"[DEBUG] === apply_table_filter 结束 ===\n")
        self.update_preview_display()
    
    def update_filter_nav_buttons(self):
        """更新导航按钮（按 PDF 页号跳转）"""
        tables = self.main_window.processed_results.get('tables', [])
        all_pages = sorted(set(tables[i].get('page', 0) for i in self.filtered_indices))

        current_page = self._current_filter_page()
        current_page_idx = all_pages.index(current_page) if current_page in all_pages else 0

        has_prev = current_page_idx > 0
        has_next = current_page_idx < len(all_pages) - 1
        self.prev_filtered_btn.setEnabled(has_prev)
        self.next_filtered_btn.setEnabled(has_next)

        self.filter_nav_label.setText(f"P{current_page} ({current_page_idx + 1}/{len(all_pages)}页)")
        self.filter_count_label.setText(f"共 {len(self.filtered_indices)} 个表格")

    def _current_filter_page(self):
        """当前中文 PDF 页号"""
        tables = self.main_window.processed_results.get('tables', [])
        if 0 <= self.filtered_index < len(self.filtered_indices):
            idx = self.filtered_indices[self.filtered_index]
            if idx < len(tables):
                return tables[idx].get('page', 0)
        return 0

    def _jump_to_first_of_page(self, target_page):
        """跳到目标页的第一个表格"""
        tables = self.main_window.processed_results.get('tables', [])
        for i, fi in enumerate(self.filtered_indices):
            if fi < len(tables) and tables[fi].get('page', 0) == target_page:
                return i
        return self.filtered_index

    def prev_filtered_page(self):
        """上一PDF页（跳到前一页的第一个表格，用于对照PDF）"""
        self._save_previous_page_data()
        tables = self.main_window.processed_results.get('tables', [])
        cur_page = self._current_filter_page()
        # 找小于当前页的最大页码
        prev_pages = sorted(set(
            tables[i].get('page', 0) for i in self.filtered_indices
            if tables[i].get('page', 0) < cur_page
        ))
        if prev_pages:
            target = prev_pages[-1]
            self.filtered_index = self._jump_to_first_of_page(target)
            self.update_filter_nav_buttons()
            self.table_list_widget.setCurrentRow(self.filtered_index)
            self.update_preview_display()

    def next_filtered_page(self):
        """下一PDF页（跳到下一页的第一个表格，用于对照PDF）"""
        self._save_previous_page_data()
        tables = self.main_window.processed_results.get('tables', [])
        cur_page = self._current_filter_page()
        # 找大于当前页的最小页码
        next_pages = sorted(set(
            tables[i].get('page', 0) for i in self.filtered_indices
            if tables[i].get('page', 0) > cur_page
        ))
        if next_pages:
            target = next_pages[0]
            self.filtered_index = self._jump_to_first_of_page(target)
            self.update_filter_nav_buttons()
            self.table_list_widget.setCurrentRow(self.filtered_index)
            self.update_preview_display()
    
    # ==================== 预览显示 ====================
    
    def update_preview_display(self):
        """更新预览显示"""
        print(f"\n[DEBUG] === update_preview_display 开始 ===")
        print(f"[DEBUG] processed_results 存在: {self.main_window.processed_results is not None}")
        print(f"[DEBUG] preview_images 存在: {hasattr(self.main_window, 'preview_images')}")
        if hasattr(self.main_window, 'preview_images'):
            print(f"[DEBUG] preview_images 长度: {len(self.main_window.preview_images) if self.main_window.preview_images else 0}")
        
        if not self.main_window.processed_results:
            print(f"[DEBUG] processed_results 为空，提前返回")
            return
        
        row = self.table_list_widget.currentRow()
        # 始终同步 filtered_index，保证筛选翻页与 sheet 翻页联动
        self.filtered_index = row
        print(f"[DEBUG] currentRow: {row}")
        print(f"[DEBUG] filtered_indices 长度: {len(self.filtered_indices)}")
        print(f"[DEBUG] filtered_indices 前5个: {self.filtered_indices[:5] if self.filtered_indices else '空'}")
        if row < 0:
            print(f"[DEBUG] row < 0，提前返回")
            return
        
        tables = self.main_window.processed_results.get('tables', [])
        print(f"[DEBUG] tables 数量: {len(tables)}")
        
        # 使用filtered_indices映射到原始表格索引
        if row < len(self.filtered_indices):
            table_idx = self.filtered_indices[row]
            print(f"[DEBUG] table_idx: {table_idx}")
        else:
            print(f"[DEBUG] row >= len(filtered_indices)，提前返回")
            return
        
        if table_idx >= len(tables):
            print(f"[DEBUG] table_idx >= len(tables)，提前返回")
            return
        
        # 锁定时：表格内容用冻结数据，PDF页码用实际列表位置
        if self.table_locked and self.locked_table_data:
            table = self.locked_table_data
            pdf_page = tables[table_idx].get('page', 1)
        else:
            table = tables[table_idx]
            pdf_page = table.get('page', 1)
        print(f"[DEBUG] 当前页: {pdf_page}, 类型: {table.get('type')}, 状态: {table.get('parse_status')}")
        print(f"[DEBUG] table data 前3行: {table.get('data', [])[:3] if table.get('data') else '无数据'}")
        
        # 更新表格类型标签
        parse_status = table.get('parse_status', '')
        if table.get('type') == 'text' or parse_status == 'text':
            status_text = "📝 文本"
        else:
            status_text = "✅ 表格" if parse_status == 'success' else "❌ 非表格"
        self.table_type_label.setText(f"状态: {status_text}")
        
        # 更新上下文文本展示区
        context_text = table.get('context_text', '')
        llm_title = table.get('llm_title', '')
        llm_summary = table.get('llm_summary', '')
        
        if hasattr(self, 'context_text_browser') and self.context_text_browser:
            if llm_title or context_text:
                text = ""
                if llm_title:
                    text += f"📌 {llm_title}"
                if llm_summary:
                    text += f"\n{llm_summary}" if text else llm_summary
                if context_text:
                    text += f"\n{context_text}" if text else context_text
                self.context_text_browser.setPlainText(text)
            else:
                self.context_text_browser.setPlainText("（无上下文描述文字）")
        
        # 根据状态设置标签颜色
        if table.get('type') == 'text' or parse_status == 'text':
            self.table_type_label.setStyleSheet("""
                QLabel { color: #8E44AD; font-weight: bold; padding: 2px 8px;
                         background-color: #F4ECF7; border-radius: 4px; border: 1px solid #8E44AD; }
            """)
        elif parse_status == 'success':
            self.table_type_label.setStyleSheet("""
                QLabel { color: #27AE60; font-weight: bold; padding: 2px 8px;
                         background-color: #E8F8F5; border-radius: 4px; border: 1px solid #27AE60; }
            """)
        else:
            self.table_type_label.setStyleSheet("""
                QLabel { color: #E74C3C; font-weight: bold; padding: 2px 8px;
                         background-color: #FDEDEC; border-radius: 4px; border: 1px solid #E74C3C; }
            """)
        
        # 显示PDF预览
        print(f"[DEBUG] 准备显示PDF预览")
        print(f"[DEBUG] 当前页: {pdf_page}")
        try:
            if hasattr(self.main_window, 'pdf_preview_widget') and self.main_window.pdf_preview_widget:
                print(f"[DEBUG] pdf_preview_widget 存在")
                if hasattr(self.main_window, 'preview_images') and self.main_window.preview_images:
                    print(f"[DEBUG] preview_images 存在，长度: {len(self.main_window.preview_images)}")
                    if pdf_page <= len(self.main_window.preview_images):
                        img_path = self.main_window.preview_images[pdf_page - 1]
                        print(f"[DEBUG] img_path: {img_path}")
                        print(f"[DEBUG] img_path 是否存在: {os.path.exists(img_path) if img_path else False}")
                        if img_path and os.path.exists(img_path):
                            print(f"[DEBUG] 准备设置预览")
                            self.main_window.pdf_preview_widget.set_preview(
                                img_path, pdf_page - 1,
                                pdf_path=self.main_window.current_file
                            )
                            print(f"[DEBUG] 预览已设置")
                            if self.main_window.pdf_preview_widget.current_pixmap and not self.main_window.pdf_preview_widget.current_pixmap.isNull():
                                print(f"[DEBUG] pixmap 有效，设置尺寸")
                                self.main_window.pdf_preview_widget.setMinimumSize(
                                    self.main_window.pdf_preview_widget.current_pixmap.width(),
                                    self.main_window.pdf_preview_widget.current_pixmap.height()
                                )
                            else:
                                print(f"[WARN] pixmap 无效或为空")
                        else:
                            print(f"[WARN] img_path为空或文件不存在: {img_path}")
                    else:
                        print(f"[WARN] page {pdf_page} 超出 preview_images 范围")
                else:
                    print(f"[WARN] preview_images 不存在或为空")
            else:
                print(f"[WARN] pdf_preview_widget 不存在")
        except Exception as e:
            print(f"[WARN] PDF预览显示失败: {e}")
        
        print(f"[DEBUG] === update_preview_display 结束 ===\n")
        
        # 显示表格数据
        self.display_table_data(table)
        
        # 更新页面导航信息
        mw = self.main_window
        total = len(self.filtered_indices)
        if hasattr(mw, 'page_info_label') and mw.page_info_label:
            mw.page_info_label.setText(f"第 {row + 1}/{total} 页")
        if hasattr(mw, 'prev_page_btn') and mw.prev_page_btn:
            mw.prev_page_btn.setEnabled(row > 0)
        if hasattr(mw, 'next_page_btn') and mw.next_page_btn:
            mw.next_page_btn.setEnabled(row < total - 1)
        if hasattr(mw, 'first_page_btn') and mw.first_page_btn:
            mw.first_page_btn.setEnabled(row > 0)
        if hasattr(mw, 'last_page_btn') and mw.last_page_btn:
            mw.last_page_btn.setEnabled(row < total - 1)
        # 更新跳转控件启用状态
        if hasattr(mw, 'goto_page_input') and mw.goto_page_input:
            mw.goto_page_input.setEnabled(total > 0)
            mw.goto_page_input.setMaximum(max(total, 1))
            if total > 0:
                mw.goto_page_input.setValue(row + 1)
        if hasattr(mw, 'goto_page_btn') and mw.goto_page_btn:
            mw.goto_page_btn.setEnabled(total > 0)

        # 记录当前显示的表格索引，供 _save_previous_page_data 使用
        self._last_displayed_table_idx = table_idx
        
        # 同步筛选导航，保证筛选翻页与 sheet 翻页联动
        self.update_filter_nav_buttons()
    
    def display_table_data(self, table):
        """显示表格数据"""
        if not hasattr(self, 'table_widget'):
            return
        
        self.table_widget.blockSignals(True)
        self.table_widget.clear()
        
        # 根据状态选择数据源：0=清洗后data, 1=original_data, 2=raw_data
        data_key = self._get_current_data_key()
        # 降级策略：优先使用当前层，没有则降级到下一层
        data = table.get(data_key, [])
        if not data:
            if data_key == 'raw_data':
                data = table.get('original_data', []) or table.get('data', [])
            elif data_key == 'original_data':
                data = table.get('data', [])
        parse_type = table.get('type', '')
        parse_message = table.get('parse_message', '')
        
        if not data:
            # 文本条目：直接显示上下文文本
            if parse_type == 'text':
                ctx = table.get('context_text', '')
                lines = ctx.split('\n') if ctx else ['（无文本内容）']
                self.table_widget.setRowCount(len(lines))
                self.table_widget.setColumnCount(1)
                for i, line in enumerate(lines):
                    item = QTableWidgetItem(line)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.table_widget.setItem(i, 0, item)
                self.table_widget.resizeColumnsToContents()
                self.table_widget.blockSignals(False)
                tip = f"📝 文本段落，共 {len(lines)} 行"
                if self.table_locked:
                    tip = "🔒 已锁定 | " + tip
                self.stats_label.setText(tip)
                return
            # 图片类页面虽然没有数据，但也正常显示
            if parse_type == 'failed':
                hint_text = "[图片型页面，无表格数据]"
            elif parse_type == 'ai':
                hint_text = f"[AI识别失败：{parse_message}，无表格数据]"
            else:
                hint_text = f"[{parse_type}类型，{parse_message}，无Excel数据]"
            
            # 设置一个空表格（1行1列提示信息），表示该页无表格数据
            self.table_widget.setRowCount(1)
            self.table_widget.setColumnCount(1)
            empty_item = QTableWidgetItem(hint_text)
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemIsEditable)  # 设为只读
            self.table_widget.setItem(0, 0, empty_item)
            self.table_widget.resizeColumnsToContents()
            self.table_widget.blockSignals(False)
            tip = hint_text
            if self.table_locked:
                tip = "🔒 已锁定 | " + tip
            self.stats_label.setText(tip)
            return
        
        rows = len(data)
        cols = max(len(row) for row in data) if data else 0
        
        self.table_widget.setRowCount(rows)
        self.table_widget.setColumnCount(cols)
        
        for i, row in enumerate(data):
            for j, cell in enumerate(row):
                item = QTableWidgetItem(str(cell) if cell else "")
                self.table_widget.setItem(i, j, item)
        
        self.table_widget.resizeColumnsToContents()
        self.table_widget.blockSignals(False)
        
        # 差异标注高亮（开启时）
        if self.diff_mode:
            self._apply_diff_highlight(table, data)
        
        # 更新统计标签（注明锁定状态或差异标注状态）
        tip = "选中单元格查看统计信息"
        if self.table_locked:
            tip = "🔒 已锁定 | " + tip
        self.stats_label.setText(tip)
    
    # ==================== 差异标注 ====================
    
    def toggle_diff_mode(self):
        """切换差异标注模式开关"""
        self.diff_mode = not self.diff_mode
        if self.diff_mode:
            self.diff_highlight_btn.setText("🔍 差异标注 ☑")
            self.diff_stats_label.show()
            # 确保 liteparse 缓存已加载
            self._load_liteparse_cache()
            if hasattr(self, 'liteparse_browser'):
                self.liteparse_browser.show()
        else:
            self.diff_highlight_btn.setText("🔍 差异标注 ☐")
            self.diff_stats_label.hide()
            if hasattr(self, 'liteparse_browser'):
                self.liteparse_browser.clear()
                self.liteparse_browser.hide()
        # 刷新当前表格
        self.update_preview_display()
    
    def _load_liteparse_cache(self):
        """懒加载当前 PDF 的 liteparse 缓存（自动感知文件切换）"""
        pdf_path = getattr(self.main_window, 'current_file', None)
        if not pdf_path:
            self._liteparse_cache = None
            return
        
        # 检查缓存对应的文件是否已变化
        if self._liteparse_cache is not None:
            cached_path = self._liteparse_cache.get("pdf_path", "")
            if cached_path == pdf_path:
                return  # 缓存有效
        
        try:
            from codes.liteparse_extractor.cache_manager import load_parse_result
            result = load_parse_result(pdf_path)
            if result is not None:
                self._liteparse_cache = result.to_dict()
            else:
                self._liteparse_cache = None
        except Exception as e:
            print(f"[Diff] 加载 liteparse 缓存失败: {e}")
            self._liteparse_cache = None
    
    def _get_liteparse_page(self, page_num: int):
        """从 liteparse 缓存获取指定页的数据"""
        if not self._liteparse_cache:
            return None
        pages = self._liteparse_cache.get("pages", [])
        for p in pages:
            if p.get("page_number") == page_num:
                return p
        return None

    def _show_liteparse_rows(self, text_items, full_text: str = ""):
        """在 liteparse_browser 中显示 liteparse 原文（按行聚类）。

        增强功能：
        - x0 缩进检测：用 ▸ 标记 + CSS padding 显示行缩进层次
        - 每个 cell 鼠标悬停 tooltip 显示精确的 x0/y0 坐标
        - 支持"聚类视图" ↔ "原文视图"切换（保留空格对齐的原始格式）

        Args:
            text_items: liteparse 的 text_items 列表
            full_text:   liteparse 的 full_text（保留原始空格排版），用于原文视图
        """
        if not hasattr(self, 'liteparse_browser') or not self.liteparse_browser:
            return
        if self.liteparse_browser.isHidden():
            return

        # 缓存数据，供视图切换时重渲染
        self._liteparse_text_items_cache = text_items
        self._liteparse_full_text = full_text

        # 原文视图模式
        if self._liteparse_view_mode == "fulltext":
            self._show_liteparse_fulltext_view(full_text)
            return

        # ---- 聚类视图 ----
        if not text_items:
            self.liteparse_browser.setPlainText("（无 liteparse 数据）")
            return

        # 构建 items（与 cell_differ 相同的格式）
        items = []
        for ti in text_items:
            if isinstance(ti, dict):
                t = ti.get("text", "").strip()
                y0 = ti.get("y0", 0)
                y1 = ti.get("y1", 0)
                if t and y1 > y0:
                    items.append({
                        "text": t,
                        "x0": ti.get("x0", 0),
                        "x1": ti.get("x1", 0),
                        "y0": y0,
                        "y1": y1,
                        "y_mid": (y0 + y1) / 2,
                    })

        if not items:
            self.liteparse_browser.setPlainText("（无有效 liteparse 数据）")
            return

        # 按 Y 聚类成行
        lp_rows = _cluster_items_by_y(items)

        # ---- 计算 x0 基线，用于缩进检测 ----
        # 取所有行首列的 x0 最小值作为左对齐基线
        first_cell_x0s = []
        for row in lp_rows:
            if row["items"]:
                first_cell_x0s.append(row["items"][0]["x0"])
        baseline_x0 = min(first_cell_x0s) if first_cell_x0s else 0
        indent_threshold = 5.0  # pt，超过此值视为有缩进

        # ---- 构建 HTML 显示 ----
        view_links = (
            '<span style="font-size:10px;color:#5DADE2;">'
            '[<b>聚类视图</b>] '
            '<a href="liteparse:fulltext" style="color:#7FB3D8;text-decoration:none;">原文视图</a>'
            '</span>'
        )
        html_parts = [
            '<div style="font-family: Consolas, monospace; font-size: 11px;">',
            f'<b>\U0001f4c4 liteparse 解析原文（按行聚类，左→右）</b> {view_links}<br>',
            '<table cellpadding="2" cellspacing="0" style="font-size: 11px;">',
        ]

        for idx, row in enumerate(lp_rows):
            texts = row["texts"]
            y_avg = round((row["y_min"] + row["y_max"]) / 2, 1)
            bg = "#F0F8FF" if idx % 2 == 0 else "#FAFAFA"

            # ---- 计算该行缩进 ----
            first_item_x0 = row["items"][0]["x0"]
            indent_pt = max(0, first_item_x0 - baseline_x0)
            indent_px = int(indent_pt * 0.75)  # pt → px 近似

            # 缩进标记
            indent_html = ""
            if indent_pt > indent_threshold:
                indent_html = (
                    f'<td style="color:#2980B9;font-size:10px;padding-left:{indent_px}px;'
                    f'title="缩进 {indent_pt:.1f}pt">\u25b8</td>'
                )

            # 每个 cell 带 tooltip
            cells_html = []
            for item in row["items"]:
                t = item["text"]
                x0 = item.get("x0", 0)
                y0 = item.get("y0", 0)
                escaped = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                title_attr = f' title="x0={x0:.1f} y0={y0:.1f}"'
                cells_html.append(f'<td{title_attr}>{escaped}</td>')

            html_parts.append(
                f'<tr style="background-color:{bg}">'
                f'<td style="color:#999;font-size:10px;padding-right:8px;white-space:nowrap">'
                f'[行{idx} Y≈{y_avg}]</td>'
                f'{indent_html}'
                f'<td>{"</td><td>|</td><td>".join(cells_html) if cells_html else "<td>-</td>"}</td>'
                f'</tr>'
            )

        html_parts.append('</table>')
        html_parts.append(
            f'<br><span style="color:#999;font-size:10px;">'
            f'共 {len(lp_rows)} 行, {len(items)} 个文本片段'
            f' | 基线 x0={baseline_x0:.0f}pt, 缩进阈值={indent_threshold:.0f}pt'
            f'</span>'
        )
        html_parts.append('</div>')

        self.liteparse_browser.setHtml("".join(html_parts))

    def _show_liteparse_fulltext_view(self, full_text: str):
        """显示 liteparse 原文视图（保留空格对齐的版式文本）。"""
        view_links = (
            '<span style="font-size:10px;color:#5DADE2;">'
            '<a href="liteparse:clustered" style="color:#7FB3D8;text-decoration:none;">聚类视图</a> '
            '[<b>原文视图</b>]'
            '</span>'
        )
        if not full_text:
            html = (
                '<div style="font-family: Consolas, monospace; font-size: 11px;">'
                f'<b>\U0001f4c4 liteparse 解析原文（版式文本）</b> {view_links}<br>'
                '<p style="color:#999;">（该页无 full_text 数据，请切换回聚类视图）</p>'
                '</div>'
            )
        else:
            # 转义 HTML 特殊字符，保留原始空格
            escaped = full_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            html = (
                '<div style="font-family: Consolas, monospace; font-size: 11px;">'
                f'<b>\U0001f4c4 liteparse 解析原文（版式文本，保留空格）</b> {view_links}<br>'
                f'<pre style="white-space:pre;margin:4px 0;color:#1A5276;">{escaped}</pre>'
                '</div>'
            )
        self.liteparse_browser.setHtml(html)

    def _on_liteparse_view_toggle(self, url):
        """处理 liteparse 视图切换链接点击。"""
        href = url.toString()
        if href == "liteparse:fulltext":
            self._liteparse_view_mode = "fulltext"
        elif href == "liteparse:clustered":
            self._liteparse_view_mode = "clustered"
        else:
            return
        # 用缓存数据重渲染
        self._show_liteparse_rows(self._liteparse_text_items_cache, self._liteparse_full_text)

    def _apply_diff_highlight(self, table, data):
        """对当前表格数据应用差异标注高亮

        新格式（行/列层级对齐）：
        {
            "cell_diffs": {"r,c": {"status": ..., "cell_value": ..., "liteparse_value": ...}},
            "extra_rows": [...],        # pdf2docx 多余行
            "missing_row_texts": [...], # liteparse 中未被捕获的行
            "extra_cols": {r: [c, ...]}, # 多余列
            "missing_cols": {r: n},      # 缺失列
            "unmatched_items": [...],    # 未消费的 liteparse 文本
        }
        兼容旧格式（顶层直接是 cell diff dict）。
        """
        if not self._liteparse_cache:
            self._load_liteparse_cache()
        if not self._liteparse_cache:
            self.diff_stats_label.setText("⚠️ liteparse 缓存不可用，无法进行差异标注")
            return

        page_num = table.get("page", 0)
        if not page_num:
            self.diff_stats_label.setText("⚠️ 无法确定当前页面号")
            return

        lp_page = self._get_liteparse_page(page_num)
        if not lp_page:
            self.diff_stats_label.setText(f"⚠️ liteparse 未解析第 {page_num} 页")
            return

        # 同页多表时，优先使用 processor 预计算的 scoped text_items
        text_items = table.get("_liteparse_items")
        if not text_items:
            text_items = lp_page.get("text_items", [])
        if not text_items:
            self.diff_stats_label.setText(f"⚠️ 第 {page_num} 页 liteparse 无文本数据")
            return

        try:
            diff_results = diff_table_with_liteparse(data, text_items)
            row_classification = classify_rows_with_liteparse(data, text_items)
        except Exception as e:
            print(f"[Diff] 对比失败: {e}")
            return

        # 兼容旧格式：如果顶层包含 status 字段，说明是旧版扁平格式
        if diff_results and any(
            isinstance(v, dict) and "status" in v
            for v in diff_results.values()
        ):
            cell_diffs = diff_results
            extra_rows = []
            missing_row_texts = []
            extra_cols = {}
            missing_cols = {}
        else:
            cell_diffs = diff_results.get("cell_diffs", {})
            extra_rows = diff_results.get("extra_rows", [])
            missing_row_texts = diff_results.get("missing_row_texts", [])
            extra_cols = diff_results.get("extra_cols", {})
            missing_cols = diff_results.get("missing_cols", {})

        # ---- 行级分类结果 ----
        row_status = row_classification.get("row_status", {})
        missing_lp_rows = row_classification.get("missing_rows", [])

        # ---- 显示 liteparse 原文（按行聚类，左侧标签 + 右侧数据列） ----
        self._show_liteparse_rows(text_items, full_text=lp_page.get("full_text", ""))

        # ---- 行级标注：幽灵行 / 节标题行 / 多余行 ----
        phantom_count = 0
        section_count = 0
        for r, status in row_status.items():
            st = status.get("status", "")
            reason = status.get("reason", "")
            cols = self.table_widget.columnCount()

            if st == "phantom":
                # 幽灵行：灰色半透明 + 删除线 + 👻 图标
                for c in range(cols):
                    item = self.table_widget.item(r, c)
                    if item is None:
                        continue
                    item.setBackground(QColor("#E8E8E8"))
                    # 添加删除线字体
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                    item.setForeground(QColor("#999999"))
                    if c == 0:
                        item.setToolTip(f"👻 幽灵行\n{reason}\n💡 此行是相邻行的非空值子集，疑似 pdf2docx 多余行")
                phantom_count += 1
            elif st == "section":
                # 节标题行：淡蓝背景
                for c in range(cols):
                    item = self.table_widget.item(r, c)
                    if item is None:
                        continue
                    item.setBackground(QColor("#D6EAF8"))
                    if c == 0:
                        item.setToolTip(f"📑 节标题\n{reason}")
                section_count += 1
            elif st == "extra":
                # 多余行（liteparse 中未找到）：黄色
                for c in range(cols):
                    item = self.table_widget.item(r, c)
                    if item is None:
                        continue
                    item.setBackground(QColor("#FFF3CD"))
                    if c == 0:
                        item.setToolTip(f"⚠️ 多余行\n{reason}")

        # ---- 单元格差异标红 ----
        suspicious_count = 0
        for key, info in cell_diffs.items():
            try:
                r, c = map(int, key.split(","))
            except ValueError:
                continue

            # 跳过幽灵行的单元格差异标注
            if row_status.get(r, {}).get("status") == "phantom":
                continue

            item = self.table_widget.item(r, c)
            if item is None:
                continue

            status = info.get("status", "")
            hint = info.get("liteparse_hint", "")
            lp_value = info.get("liteparse_value", "")

            if status == "suspicious":
                item.setBackground(QColor("#FFE0E0"))
                tooltip = f"⚠️ 可疑值\n{hint}"
                if lp_value:
                    tooltip += f"\n💡 建议值: {lp_value}"
                item.setToolTip(tooltip)
                suspicious_count += 1

        # ---- 多余行标黄（保留旧逻辑，与 extra 互补） ----
        for r in extra_rows:
            # 跳过已经分类过的行
            if r in row_status and row_status[r].get("status") in ("phantom", "section"):
                continue
            cols = self.table_widget.columnCount()
            for c in range(cols):
                item = self.table_widget.item(r, c)
                if item is not None:
                    item.setBackground(QColor("#FFF3CD"))
                    if c == 0:
                        item.setToolTip("⚠️ 疑似多余行：此行标签在 liteparse 中未找到")

        # ---- 多余列标橙 ----
        for r, cols in extra_cols.items():
            # 跳过幽灵行
            if row_status.get(r, {}).get("status") == "phantom":
                continue
            for c in cols:
                item = self.table_widget.item(r, c)
                if item is not None:
                    item.setBackground(QColor("#FFE0B2"))
                    item.setToolTip(f"⚠️ 疑似多余列：liteparse 该行只有 {c} 列数据")

        # ---- 缺失列提示（在最后一列加提示） ----
        for r, n_missing in missing_cols.items():
            # 跳过幽灵行
            if row_status.get(r, {}).get("status") == "phantom":
                continue
            lp_row = extra_cols.get(r, [])
            cols = self.table_widget.columnCount()
            last_col = cols - 1
            if lp_row:
                last_col = min(lp_row) - 1
            item = self.table_widget.item(r, max(0, last_col))
            if item is not None:
                existing_tip = item.toolTip() or ""
                tip = f"⚠️ 疑似缺失列：liteparse 该行多 {n_missing} 列数据"
                if existing_tip:
                    tip = existing_tip + "\n" + tip
                item.setToolTip(tip)

        # ---- 统计信息 ----
        total_cells = sum(len(row) for row in data)
        non_empty = sum(
            1 for row in data for cell in row
            if cell and str(cell).strip()
        )

        parts = [f"🔍 差异标注"]
        if suspicious_count:
            parts.append(f"可疑: {suspicious_count}")
        if phantom_count:
            parts.append(f"👻幽灵行: {phantom_count}")
        if missing_lp_rows:
            parts.append(f"缺失行: {len(missing_lp_rows)}")
        if section_count:
            parts.append(f"节标题: {section_count}")
        if extra_rows:
            parts.append(f"多余行: {len(extra_rows)}")
        if extra_cols:
            parts.append(f"多余列: {sum(len(v) for v in extra_cols.values())}")
        if missing_cols:
            parts.append(f"缺失列: {sum(missing_cols.values())}")
        if not any([suspicious_count, phantom_count, missing_lp_rows, extra_rows, missing_row_texts, extra_cols, missing_cols]):
            parts.append("全部一致 ✅")

        parts.append(f"含值: {non_empty}")
        parts.append(f"总单元格: {total_cells}")

        self.diff_stats_label.setText(" | ".join(parts))
    
    # ==================== 原始/清洗切换 ====================

    def toggle_original_view(self):
        """三层数据切换：清洗后(0) → 清洗前(1) → 提取原样(2) → 清洗后(0)"""
        self.showing_data_layer = (self.showing_data_layer + 1) % 3
        if self.showing_data_layer == 0:
            self.toggle_original_btn.setText("📋 查看原始数据")
            self.toggle_original_btn.setToolTip("切换: 清洗数据 → 清洗前 → 提取原样 (当前: 清洗后)")
        elif self.showing_data_layer == 1:
            self.toggle_original_btn.setText("📋 查看清洗数据")
            self.toggle_original_btn.setToolTip("切换: 清洗前 → 提取原样 → 清洗后 (当前: 清洗前 original_data)")
        else:
            self.toggle_original_btn.setText("📄 查看提取原样")
            self.toggle_original_btn.setToolTip("切换: 提取原样 → 清洗后 → 清洗前 (当前: 提取原样 raw_data)")
        # 刷新当前表格显示
        self.update_preview_display()

    def _get_current_data_key(self):
        """获取当前数据层的键名"""
        if self.showing_data_layer == 2:
            return 'raw_data'
        elif self.showing_data_layer == 1:
            return 'original_data'
        return 'data'
    
    def _change_table_page(self, filtered_row):
        """手动修改表格的 PDF 页号"""
        if filtered_row < 0 or filtered_row >= len(self.filtered_indices):
            return
        
        table_idx = self.filtered_indices[filtered_row]
        tables = self.main_window.processed_results.get('tables', [])
        if table_idx >= len(tables):
            return
        
        current_page = tables[table_idx].get('page', 0)
        
        # 弹出输入对话框
        new_page_str, ok = QInputDialog.getText(
            self.main_window, "修改页号",
            f"当前页号: P{current_page}\n请输入新的页号:",
            text=str(current_page)
        )
        if not ok or not new_page_str:
            return
        
        try:
            new_page = int(new_page_str.strip())
        except ValueError:
            QMessageBox.warning(self.main_window, "修改页号", "请输入有效的数字页号。")
            return
        
        if new_page == current_page:
            return
        
        # 更新页号
        tables[table_idx]['page'] = new_page
        # 按页码重排
        tables.sort(key=lambda x: x.get('page', 0))
        self.main_window.processed_results['tables'] = tables
        
        # 同步到文件
        from codes.pdf_extractor import save_mid_data
        if self.main_window.current_file:
            save_mid_data(self.main_window.current_file, self.main_window.processed_results)
        
        # 刷新列表，找到修改后的表格并选中
        self.apply_table_filter(preserve_selection=table_idx if table_idx < len(tables) else None)
    def prev_preview_page(self):
        """上一页预览（切页前先保存当前编辑）"""
        self._sync_ui_to_processed_results()
        current = self.table_list_widget.currentRow()
        if current > 0:
            self.filtered_index = current - 1
            self.table_list_widget.setCurrentRow(self.filtered_index)
            self.update_filter_nav_buttons()
            self.update_preview_display()

    def next_preview_page(self):
        """下一页预览（切页前先保存当前编辑）"""
        self._sync_ui_to_processed_results()
        current = self.table_list_widget.currentRow()
        if current < self.table_list_widget.count() - 1:
            self.filtered_index = current + 1
            self.table_list_widget.setCurrentRow(self.filtered_index)
            self.update_filter_nav_buttons()
            self.update_preview_display()

    def first_preview_page(self):
        """第一页预览（切页前先保存当前编辑）"""
        self._sync_ui_to_processed_results()
        if self.table_list_widget.count() > 0:
            self.filtered_index = 0
            self.table_list_widget.setCurrentRow(0)
            self.update_filter_nav_buttons()
            self.update_preview_display()

    def last_preview_page(self):
        """最后一页预览（切页前先保存当前编辑）"""
        self._sync_ui_to_processed_results()
        if self.table_list_widget.count() > 0:
            self.filtered_index = self.table_list_widget.count() - 1
            self.table_list_widget.setCurrentRow(self.filtered_index)
            self.update_filter_nav_buttons()
            self.update_preview_display()

    def goto_preview_page(self, page_num):
        """跳转到指定页（切页前先保存当前编辑）"""
        self._sync_ui_to_processed_results()
        if self.table_list_widget.count() > 0 and page_num >= 1:
            self.filtered_index = min(page_num - 1, self.table_list_widget.count() - 1)
            self.table_list_widget.setCurrentRow(self.filtered_index)
            self.update_filter_nav_buttons()
            self.update_preview_display()
    
    # ==================== 表格编辑 ====================
    
    def show_table_context_menu(self, position):
        """显示表格右键菜单"""
        menu = QMenu()
        
        undo_action = menu.addAction("↩️ 撤销")
        undo_action.triggered.connect(self.undo_change)
        undo_action.setEnabled(len(self.undo_stack) > 0)
        
        redo_action = menu.addAction("↪️ 重做")
        redo_action.triggered.connect(self.redo_change)
        redo_action.setEnabled(len(self.redo_stack) > 0)
        
        menu.addSeparator()

        menu.addAction("⬆️ 上方插入行").triggered.connect(self.insert_row_above)
        menu.addAction("⬇️ 下方插入行").triggered.connect(self.insert_row_below)
        menu.addAction("⬅️ 左侧插入列").triggered.connect(self.insert_col_left)
        menu.addAction("➡️ 右侧插入列").triggered.connect(self.insert_col_right)
        menu.addSeparator()
        menu.addAction("📦 批量插入行/列...").triggered.connect(self.batch_insert)

        menu.addSeparator()

        # 删除操作 - 根据选中情况显示不同选项
        selected_items = self.table_widget.selectedItems()
        selected_rows = set(item.row() for item in selected_items)
        selected_cols = set(item.column() for item in selected_items)

        if len(selected_rows) > 1:
            delete_all_rows_action = menu.addAction(f"🗑️ 删除所有选中行 ({len(selected_rows)}行)")
            delete_all_rows_action.triggered.connect(self.delete_selected_rows)
        elif len(selected_rows) == 1:
            menu.addAction("🗑️ 删除行").triggered.connect(self.delete_row)

        if len(selected_cols) > 1:
            delete_all_cols_action = menu.addAction(f"🗑️ 删除所有选中列 ({len(selected_cols)}列)")
            delete_all_cols_action.triggered.connect(self.delete_selected_columns)
        elif len(selected_cols) == 1:
            menu.addAction("🗑️ 删除列").triggered.connect(self.delete_column)

        menu.addSeparator()
        menu.addAction("📋 复制").triggered.connect(self.copy_from_table)
        menu.addAction("📄 粘贴").triggered.connect(self.paste_to_table)
        menu.addAction("✂️ 剪切").triggered.connect(self.cut_from_table)
        menu.addAction("⬇️ 向下填充").triggered.connect(self.fill_down_from_table)

        menu.exec_(self.table_widget.mapToGlobal(position))

    def insert_row(self):
        """插入行（工具栏用，在选中位置上方插入）"""
        self.save_current_table_state()
        current_row = self.table_widget.currentRow()
        self.table_widget.insertRow(current_row if current_row >= 0 else 0)
        self.table_widget.resizeColumnsToContents()

    def delete_row(self):
        """删除选中行"""
        self.save_current_table_state()
        current_row = self.table_widget.currentRow()
        if current_row >= 0:
            self.table_widget.removeRow(current_row)

    def insert_column(self):
        """插入列（工具栏用，在选中位置左侧插入）"""
        self.save_current_table_state()
        current_col = self.table_widget.currentColumn()
        self.table_widget.insertColumn(current_col if current_col >= 0 else 0)
        self.table_widget.resizeColumnsToContents()

    def delete_column(self):
        """删除选中列"""
        self.save_current_table_state()
        current_col = self.table_widget.currentColumn()
        if current_col >= 0:
            self.table_widget.removeColumn(current_col)

    def insert_row_above(self):
        """上方插入行"""
        self.save_current_table_state()
        current_row = self.table_widget.currentRow()
        self.table_widget.insertRow(current_row if current_row >= 0 else 0)
        self.table_widget.resizeColumnsToContents()

    def insert_row_below(self):
        """下方插入行"""
        self.save_current_table_state()
        current_row = self.table_widget.currentRow()
        self.table_widget.insertRow(current_row + 1 if current_row >= 0 else 0)
        self.table_widget.resizeColumnsToContents()

    def insert_col_left(self):
        """左侧插入列"""
        self.save_current_table_state()
        current_col = self.table_widget.currentColumn()
        self.table_widget.insertColumn(current_col if current_col >= 0 else 0)
        self.table_widget.resizeColumnsToContents()

    def insert_col_right(self):
        """右侧插入列"""
        self.save_current_table_state()
        current_col = self.table_widget.currentColumn()
        self.table_widget.insertColumn(current_col + 1 if current_col >= 0 else 0)
        self.table_widget.resizeColumnsToContents()

    def delete_selected_rows(self):
        """删除所有选中的行"""
        selected_rows = set(item.row() for item in self.table_widget.selectedItems())
        if selected_rows:
            self.save_current_table_state()
            for row in sorted(selected_rows, reverse=True):
                self.table_widget.removeRow(row)

    def delete_selected_columns(self):
        """删除所有选中的列"""
        selected_cols = set(item.column() for item in self.table_widget.selectedItems())
        if selected_cols:
            self.save_current_table_state()
            for col in sorted(selected_cols, reverse=True):
                self.table_widget.removeColumn(col)

    def save_current_table_state(self):
        """保存当前表格状态到撤销栈和数据源"""
        row = self.table_list_widget.currentRow()
        if row < 0:
            return

        data = []
        for i in range(self.table_widget.rowCount()):
            row_data = []
            for j in range(self.table_widget.columnCount()):
                item = self.table_widget.item(i, j)
                row_data.append(item.text() if item else "")
            data.append(row_data)

        if len(self.undo_stack) >= self.max_history:
            self.undo_stack.pop(0)
        self.undo_stack.append((row, data))
        self.redo_stack.clear()

        # 同步到 processed_results，确保编辑不丢失
        self._sync_ui_to_processed_results()
    
    def undo_change(self):
        """撤销"""
        if not self.undo_stack:
            return
        
        row = self.table_list_widget.currentRow()
        current_data = []
        for i in range(self.table_widget.rowCount()):
            row_data = []
            for j in range(self.table_widget.columnCount()):
                item = self.table_widget.item(i, j)
                row_data.append(item.text() if item else "")
            current_data.append(row_data)
        
        self.redo_stack.append((row, current_data))
        
        undo_data = self.undo_stack.pop()
        self.restore_table_data(undo_data)
    
    def redo_change(self):
        """重做"""
        if not self.redo_stack:
            return
        
        row = self.table_list_widget.currentRow()
        current_data = []
        for i in range(self.table_widget.rowCount()):
            row_data = []
            for j in range(self.table_widget.columnCount()):
                item = self.table_widget.item(i, j)
                row_data.append(item.text() if item else "")
            current_data.append(row_data)
        
        self.undo_stack.append((row, current_data))
        
        redo_data = self.redo_stack.pop()
        self.restore_table_data(redo_data)
    
    def restore_table_data(self, data):
        """恢复表格数据（撤销/重做），同步到数据源"""
        row, table_data = data

        self.table_widget.blockSignals(True)
        self.table_widget.clear()

        rows = len(table_data)
        cols = max(len(r) for r in table_data) if table_data else 0

        self.table_widget.setRowCount(rows)
        self.table_widget.setColumnCount(cols)

        for i, row_data in enumerate(table_data):
            for j, cell in enumerate(row_data):
                item = QTableWidgetItem(str(cell) if cell else "")
                self.table_widget.setItem(i, j, item)

        self.table_widget.resizeColumnsToContents()
        self.table_widget.blockSignals(False)

        # 同步到 processed_results，确保切页/刷新时不丢失
        self._sync_ui_to_processed_results()

        # 只在不同页面时才切换，避免触发不必要的页面重载
        if row >= 0 and row != self.table_list_widget.currentRow() and row < self.table_list_widget.count():
            self.table_list_widget.setCurrentRow(row)
    
    def copy_from_table(self):
        """复制"""
        selection = self.table_widget.selectedRanges()
        if not selection:
            return
        
        text = ""
        for range_ in selection:
            for row in range(range_.topRow(), range_.bottomRow() + 1):
                row_data = []
                for col in range(range_.leftColumn(), range_.rightColumn() + 1):
                    item = self.table_widget.item(row, col)
                    row_data.append(item.text() if item else "")
                text += "\t".join(row_data) + "\n"
        
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
    
    def fill_down_from_table(self):
        """向下填充"""
        selection = self.table_widget.selectedRanges()
        if not selection:
            return
        r = selection[0]
        src_row = r.topRow()
        src_col = r.leftColumn()
        
        src_item = self.table_widget.item(src_row, src_col)
        if not src_item:
            return
        src_text = src_item.text()
        
        self.table_widget.data_about_to_change.emit()
        for row in range(src_row + 1, r.bottomRow() + 1):
            for col in range(r.leftColumn(), r.rightColumn() + 1):
                item = self.table_widget.item(row, col)
                if item:
                    item.setText(src_text)
                else:
                    self.table_widget.setItem(row, col, QTableWidgetItem(src_text))

    def cut_from_table(self):
        """剪切"""
        self.copy_from_table()
        for range_ in self.table_widget.selectedRanges():
            for row in range(range_.topRow(), range_.bottomRow() + 1):
                for col in range(range_.leftColumn(), range_.rightColumn() + 1):
                    item = self.table_widget.item(row, col)
                    if item:
                        item.setText("")
    
    def paste_to_table(self):
        """粘贴"""
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if not text:
            return
        
        self.save_current_table_state()
        
        rows = text.strip().split('\n')
        start_row = self.table_widget.currentRow()
        start_col = self.table_widget.currentColumn()
        
        for i, row_text in enumerate(rows):
            cols = row_text.split('\t')
            for j, cell_text in enumerate(cols):
                target_row = start_row + i
                target_col = start_col + j
                
                if target_row >= self.table_widget.rowCount():
                    self.table_widget.insertRow(target_row)
                if target_col >= self.table_widget.columnCount():
                    self.table_widget.insertColumn(target_col)
                
                item = self.table_widget.item(target_row, target_col)
                if not item:
                    item = QTableWidgetItem()
                    self.table_widget.setItem(target_row, target_col, item)
                item.setText(cell_text)
        
        self.table_widget.resizeColumnsToContents()
    
    def remove_spaces(self):
        """智能删除空单元格：仅在处理文本行时，删除每行左右两端的空单元格，保持内部空单元格不变"""
        self.save_current_table_state()
        table = self.table_widget
        removed_count = 0
        for row in range(table.rowCount()):
            # 找到该行第一个和最后一个非空单元格
            first_non_empty = -1
            last_non_empty = -1
            for col in range(table.columnCount()):
                item = table.item(row, col)
                text = item.text().strip() if item else ""
                if text:
                    if first_non_empty < 0:
                        first_non_empty = col
                    last_non_empty = col

            if first_non_empty < 0:
                # 全空行 → 跳过
                continue

            # 统计左侧被删除的空单元格数
            removed_count += first_non_empty  # 左侧空单元格数

            # 统计右侧被删除的空单元格数
            right_empty = table.columnCount() - 1 - last_non_empty
            removed_count += right_empty

            # 将 first_non_empty..last_non_empty 范围左移到 0..N-1
            new_col = 0
            for col in range(first_non_empty, last_non_empty + 1):
                src = table.item(row, col)
                dst = table.item(row, new_col)
                if dst is None:
                    dst = QTableWidgetItem()
                    table.setItem(row, new_col, dst)
                dst.setText(src.text() if src else "")
                dst.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                new_col += 1

            # 多出的列清空
            for col in range(new_col, table.columnCount()):
                item = table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(row, col, item)
                if item.text():
                    item.setText("")

        table.resizeColumnsToContents()
        QMessageBox.information(
            self.main_window, "删除空格",
            f"已处理完成，共清理 {removed_count} 个两端空单元格。\n"
            "注意：仅删除了每行左右两端的空单元格，内部的不会删除。"
        )

    def clean_data(self):
        """清洗选中区域数据：只保留数值、千分位、小数点、负号、括号、百分号"""
        table = self.table_widget

        # 优先使用 selectedItems，避免因焦点丢失导致 selectedRanges 为空
        selected = table.selectedItems()
        if not selected:
            QMessageBox.information(
                self.main_window, "清洗数据",
                "请先选中要清洗的单元格区域。"
            )
            return

        self.save_current_table_state()
        import re

        # 匹配要保留的字符：数字、逗号、小数点、负号、括号、百分号
        pattern = re.compile(r'[^0-9,\.\-\(\)\%\s]')

        cleaned_count = 0
        seen = set()
        for item in selected:
            row = item.row()
            col = item.column()
            key = (row, col)
            if key in seen:
                continue
            seen.add(key)

            text = item.text()
            if not text:
                continue
            # 去掉不允许的字符，然后去除前后空白
            cleaned = pattern.sub('', text)
            cleaned = cleaned.strip()
            if cleaned != text.strip():
                cleaned_count += 1
            item.setText(cleaned)
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        table.resizeColumnsToContents()
        QMessageBox.information(
            self.main_window, "清洗数据",
            f"已处理完成，共清洗 {cleaned_count} 个单元格。"
        )

    def merge_to_previous(self):
        """将当前表格数据追加到前一个同类别表格的左下方，然后删除当前表格"""
        current_row = self.table_list_widget.currentRow()
        if current_row < 0 or current_row >= len(self.filtered_indices):
            return

        # 获取当前表格的原始索引和类别
        current_table_idx = self.filtered_indices[current_row]
        tables = self.main_window.processed_results.get('tables', [])
        if current_table_idx >= len(tables):
            return

        current_table = tables[current_table_idx]
        current_is_success = current_table.get('parse_status') == 'success'
        current_is_manual = current_table.get('is_manual', False)

        # 在同类别表格中，找到当前表格的前一个表格（按原始顺序向前查找）
        prev_table_idx = None
        for i in range(current_table_idx - 1, -1, -1):
            t = tables[i]
            t_is_success = t.get('parse_status') == 'success'
            t_is_manual = t.get('is_manual', False)

            # 判断是否是同类别（parse_status 和 is_manual 都相同）
            if t_is_success == current_is_success and t_is_manual == current_is_manual:
                prev_table_idx = i
                break

        if prev_table_idx is None:
            QMessageBox.information(
                self.main_window, "向前合并",
                "当前已是同类别中的第一个表格，无法向前合并。"
            )
            return

        # 找到 prev_table_idx 在 filtered_indices 中的位置（用于显示序号）
        prev_row_in_filtered = None
        for i, idx in enumerate(self.filtered_indices):
            if idx == prev_table_idx:
                prev_row_in_filtered = i
                break

        prev_table = tables[prev_table_idx]
        current_data = current_table.get('data', [])
        prev_data = prev_table.get('data', [])

        if not current_data:
            QMessageBox.information(
                self.main_window, "向前合并",
                "当前表格没有数据，无需合并。"
            )
            return

        # 确认对话框（显示同类别中的序号）
        display_current = current_row + 1
        display_prev = prev_row_in_filtered + 1 if prev_row_in_filtered is not None else prev_table_idx + 1
        reply = QMessageBox.question(
            self.main_window, "确认合并",
            f"确定将当前表格（同类别第{display_current}个）合并到前一个同类别表格（同类别第{display_prev}个）吗？\n\n"
            f"当前表格 {len(current_data)} 行数据将追加到前一个表格下方。",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # 数据合并：将当前表格数据追加到前一个表格的左下方
        # 左对齐：以前一个表格的列数为准，不足的列补空
        prev_cols = max((len(row) for row in prev_data), default=0) if prev_data else 0
        if prev_cols == 0:
            # 前一个表格没有数据，直接用当前表格的数据
            prev_data = [list(row) for row in current_data]
        else:
            for row in current_data:
                new_row = list(row)
                while len(new_row) < prev_cols:
                    new_row.append("")
                prev_data.append(new_row[:prev_cols])  # 截断多余的列

        # 更新前一个表格的数据和行数
        prev_table['data'] = prev_data
        prev_table['rows'] = len(prev_data)
        prev_table['parse_status'] = 'success'

        # 删除当前表格（使用原始索引）
        # 因为 prev_table_idx < current_table_idx，删除 current_table_idx 不影响 prev_table_idx
        tables.pop(current_table_idx)
        self.main_window.processed_results['tables'] = tables
        self.main_window.processed_results['total_tables'] = len(tables)

        # 同步到文件
        if self.main_window.current_file:
            from codes.pdf_extractor import save_mid_data
            save_mid_data(self.main_window.current_file, self.main_window.processed_results)

        # 刷新列表并选中前一个（使用原始索引）
        self.apply_table_filter(preserve_selection=prev_table_idx)
        QMessageBox.information(
            self.main_window, "向前合并",
            f"合并完成！已追加 {len(current_data)} 行数据到前一个表格。"
        )

    def batch_insert(self):
        """批量插入多行或多列"""
        # 创建对话框
        dlg = QDialog(self.main_window)
        dlg.setWindowTitle("批量插入")
        dlg.setMinimumWidth(320)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        # 类型选择
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("类型:"))
        row_radio = QRadioButton("行")
        col_radio = QRadioButton("列")
        row_radio.setChecked(True)
        type_layout.addWidget(row_radio)
        type_layout.addWidget(col_radio)
        type_layout.addStretch()
        layout.addLayout(type_layout)

        # 数量选择
        count_layout = QHBoxLayout()
        count_layout.addWidget(QLabel("数量:"))
        count_spin = QSpinBox()
        count_spin.setRange(1, 999)
        count_spin.setValue(5)
        count_spin.setSuffix(" 个")
        count_spin.setMinimumWidth(100)
        count_layout.addWidget(count_spin)
        count_layout.addStretch()
        layout.addLayout(count_layout)

        # 位置选择
        pos_layout = QHBoxLayout()
        pos_layout.addWidget(QLabel("位置:"))
        pos_combo = QComboBox()
        pos_combo.addItems(["选中行下方", "选中行上方", "表格末尾"])
        pos_combo.setMinimumWidth(150)
        pos_layout.addWidget(pos_combo)
        pos_layout.addStretch()
        layout.addLayout(pos_layout)

        # 类型切换时更新位置选项
        def on_type_changed():
            pos_combo.clear()
            if row_radio.isChecked():
                pos_combo.addItems(["选中行下方", "选中行上方", "表格末尾"])
            else:
                pos_combo.addItems(["选中列右侧", "选中列左侧", "表格末尾"])
        row_radio.toggled.connect(on_type_changed)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        btn_box.button(QDialogButtonBox.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.Cancel).setText("取消")
        layout.addWidget(btn_box)

        if dlg.exec_() != QDialog.Accepted:
            return

        count = count_spin.value()
        is_row = row_radio.isChecked()
        position = pos_combo.currentText()

        self.save_current_table_state()

        if is_row:
            current_row = self.table_widget.currentRow()
            for _ in range(count):
                if position == "选中行上方":
                    self.table_widget.insertRow(current_row if current_row >= 0 else 0)
                    if current_row >= 0:
                        current_row += 1
                elif position == "选中行下方":
                    insert_at = current_row + 1 if current_row >= 0 else 0
                    self.table_widget.insertRow(insert_at)
                    if current_row >= 0:
                        current_row += 1
                else:  # 表格末尾
                    self.table_widget.insertRow(self.table_widget.rowCount())
        else:
            current_col = self.table_widget.currentColumn()
            for _ in range(count):
                if position == "选中列左侧":
                    self.table_widget.insertColumn(current_col if current_col >= 0 else 0)
                    if current_col >= 0:
                        current_col += 1
                elif position == "选中列右侧":
                    insert_at = current_col + 1 if current_col >= 0 else 0
                    self.table_widget.insertColumn(insert_at)
                    if current_col >= 0:
                        current_col += 1
                else:  # 表格末尾
                    self.table_widget.insertColumn(self.table_widget.columnCount())

        self.table_widget.resizeColumnsToContents()
    
    def calculate_selected(self):
        """计算选中区域"""
        selected = self.table_widget.selectedItems()
        if not selected:
            self.stats_label.setText("请先选择单元格区域")
            return
        
        total_sum = 0
        count = 0
        values = []
        
        for item in selected:
            if item:
                try:
                    val = float(item.text().replace(',', '').replace('%', ''))
                    total_sum += val
                    values.append(val)
                    count += 1
                except ValueError:
                    pass
        
        if count > 0:
            avg = total_sum / count
            self.stats_label.setText(f"总和: {total_sum:,.2f} | 平均: {avg:,.2f} | 数量: {count}")
        else:
            self.stats_label.setText("选中区域没有数值")
    
    def filter_table(self):
        """筛选表格内容"""
        if not hasattr(self, 'table_widget'):
            return
        
        search_text = self.filter_input.text().lower()
        
        # 高亮匹配的单元格
        for i in range(self.table_widget.rowCount()):
            row_hidden = True
            for j in range(self.table_widget.columnCount()):
                item = self.table_widget.item(i, j)
                if item and search_text in item.text().lower():
                    row_hidden = False
                    break
            self.table_widget.setRowHidden(i, row_hidden)
    
    def clear_filter(self):
        """清除筛选"""
        self.filter_input.clear()
        for i in range(self.table_widget.rowCount()):
            self.table_widget.setRowHidden(i, False)
    
    # ==================== 页面状态管理 ====================
    
    def _set_table_editable(self, editable):
        """设置表格是否可编辑"""
        if editable:
            self.table_widget.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        else:
            self.table_widget.setEditTriggers(QTableWidget.NoEditTriggers)

    def toggle_edit_mode(self):
        """切换编辑模式"""
        if self.edit_mode:
            # 退出编辑模式时始终询问是否保存
            reply = QMessageBox.question(
                self.main_window, "退出编辑模式",
                "是否保存当前修改？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if reply == QMessageBox.Cancel:
                return
            elif reply == QMessageBox.Yes:
                self._save_all_changes()
                if hasattr(self, 'save_status_btn') and self.save_status_btn:
                    self.save_status_btn.setEnabled(False)
            self.edit_mode = False
            self.has_unsaved_changes = False
            self.edit_mode_btn.setText("✏️ 开始编辑")
            self.edit_mode_btn.setStyleSheet("""
                QPushButton { background-color: #E67E22; color: white; padding: 2px 10px; border-radius: 4px;
                              font-weight: bold; }
                QPushButton:hover { background-color: #D35400; }
            """)
            self.toggle_table_type_btn.setEnabled(False)
            self.save_status_btn.setEnabled(False)
            # 退出编辑模式后表格解锁，可自由编辑数据
            self._set_table_editable(True)
            self.main_window.status_bar.showMessage("已退出编辑模式，表格可自由编辑", 2000)
        else:
            self.edit_mode = True
            self.has_unsaved_changes = False
            self.edit_mode_btn.setText("🔴 退出编辑")
            self.edit_mode_btn.setStyleSheet("""
                QPushButton { background-color: #E74C3C; color: white; padding: 2px 10px; border-radius: 4px;
                              font-weight: bold; }
                QPushButton:hover { background-color: #C0392B; }
            """)
            self.toggle_table_type_btn.setEnabled(True)
            self.save_status_btn.setEnabled(True)
            # 进入编辑模式时锁定表格编辑（需先保存后才能编辑）
            self._set_table_editable(False)
            self.main_window.status_bar.showMessage("已进入编辑模式，可修改页面类型（退出后表格自动解锁）", 2000)
    
    def _save_all_changes(self):
        """保存所有更改到缓存"""
        if not self.main_window.processed_results or not self.main_window.current_file:
            return
        save_mid_data(self.main_window.current_file, self.main_window.processed_results)
        self.main_window.status_bar.showMessage("所有修改已保存", 3000)
    
    def save_page_status(self):
        """保存页面状态"""
        if not self.edit_mode:
            QMessageBox.information(self.main_window, "提示", "请先点击「开始编辑」进入编辑模式")
            return
        if not self.main_window.processed_results or not self.main_window.current_file:
            return
        
        self._save_all_changes()
        self.has_unsaved_changes = False
        # 保存完成后：保存按钮灰化不可触发，下方表格解锁可编辑
        self.save_status_btn.setEnabled(False)
        self._set_table_editable(True)
        self.main_window.status_bar.showMessage("页面状态已保存，下方表格可手动编辑", 3000)
    
    def toggle_current_page_type(self):
        """切换页面类型"""
        if not self.edit_mode:
            QMessageBox.information(self.main_window, "提示", "请先点击「开始编辑」进入编辑模式")
            return
        
        if not self.main_window.processed_results:
            return
        
        row = self.table_list_widget.currentRow()
        if row < 0:
            return
        
        # 获取当前项的原始索引（用于筛选后恢复选中）
        current_item = self.table_list_widget.item(row)
        if not current_item:
            return
        origin_idx = current_item.data(Qt.UserRole)
        
        tables = self.main_window.processed_results.get('tables', [])
        if origin_idx is None or origin_idx >= len(tables):
            return
        
        table = tables[origin_idx]
        current_status = table.get('parse_status', 'failed')
        new_status = 'success' if current_status == 'failed' else 'failed'
        table['parse_status'] = new_status
        table['is_manual'] = True
        self.has_unsaved_changes = True
        
        # 更新列表文本（保留页号_序号格式）
        page = table.get('page', 0)
        # 计算本表在当前页的序号
        seq = sum(1 for t in tables[:origin_idx] if t.get('page') == page) + 1
        ext = table.get('extractor', '')
        if ext == "manual": ext_tag = "M"
        elif ext.startswith("docx"): ext_tag = "D"
        else: ext_tag = "V2"
        title = table.get('title', '') or ''
        title_str = f" {title[:8]}" if title else ""
        status_icon = "✅" if new_status == 'success' else "❌"
        self.table_list_widget.item(row).setText(f"{status_icon} P{page}_{seq} [{ext_tag}]{title_str}")
        
        # 标记保存按钮为可保存状态
        self.save_status_btn.setText("💾 保存更改")
        self.main_window.status_bar.showMessage(f"已反转第{table['page']}页类型，点击「保存」可持久化", 3000)
        
        # 重新应用筛选，保持当前位置或跳到下一个
        self.apply_table_filter(preserve_selection=origin_idx)
    
    def batch_export_tables(self):
        """批量导出表格"""
        self.main_window.batch_export_tables()

    # ==================== AI 命名功能 ====================

    def on_ai_name_clicked(self):
        """AI命名按钮点击 - 启动 DeepSeek 为表格生成名称"""
        tables = self.main_window.processed_results.get('tables', [])
        if not tables:
            QMessageBox.warning(self.main_window, "无数据", "请先处理PDF文件，提取表格后再使用AI命名功能。")
            return

        # 筛选有数据或上下文文本的表格
        candidate_tables = []
        for idx, t in enumerate(tables):
            data = t.get('data', [])
            ctx = t.get('context_text', '')
            if data or ctx:
                candidate_tables.append((idx, t))

        if not candidate_tables:
            QMessageBox.information(self.main_window, "无表格", "没有找到可命名的表格数据。")
            return

        # 确认
        reply = QMessageBox.question(
            self.main_window, "AI 命名确认",
            f"将为当前文档的 {len(candidate_tables)} 个表格调用 DeepSeek 生成名称。\n\n"
            f"每个表格约需 1-3 秒，总共约需 {len(candidate_tables) * 2} 秒。\n\n是否继续？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.No:
            self._start_ai_naming(candidate_tables)

    def _start_ai_naming(self, candidate_tables):
        """启动 AI 命名后台线程"""
        from codes.pdf_extractor import load_config

        if hasattr(self, 'ai_name_btn') and self.ai_name_btn:
            self.ai_name_btn.setEnabled(False)
            self.ai_name_btn.setText("🤖 命名中...")

        config = load_config()
        ds_api_key = config.get("deepseek_api_key", "")
        if not ds_api_key:
            QMessageBox.warning(
                self.main_window, "未配置",
                "请先在「配置」页面填写 DeepSeek API Key。"
            )
            if hasattr(self, 'ai_name_btn') and self.ai_name_btn:
                self.ai_name_btn.setEnabled(True)
                self.ai_name_btn.setText("🤖 AI命名")
            return

        # 准备传给 worker 的数据（只取必要字段）
        worker_tables = []
        for idx, t in candidate_tables:
            worker_tables.append({
                "index": idx,
                "context_text": t.get("context_text", ""),
                "data": t.get("data", []),
            })

        self._ai_worker = AINameWorker(worker_tables, config)
        self._ai_worker.progress.connect(self._on_ai_naming_progress)
        self._ai_worker.finished.connect(self._on_ai_name_finished)
        self._ai_worker.start()

        # 显示进度
        self.main_window.status_bar.showMessage(f"🤖 AI命名: 正在处理 0/{len(worker_tables)}...")

    def _on_ai_naming_progress(self, current, total, message):
        """AI 命名进度更新"""
        self.main_window.status_bar.showMessage(f"🤖 AI命名: {current}/{total} - {message}")

    def _on_ai_name_finished(self, results):
        """AI 命名完成 - 更新数据并刷新"""
        tables = self.main_window.processed_results.get('tables', [])

        updated_count = 0
        for result in results:
            idx = result["index"]
            if idx < len(tables):
                tbl = tables[idx]
                title = result.get("title", "")
                summary = result.get("summary", "")
                if title:
                    tbl["llm_title"] = title
                    tbl["llm_summary"] = summary
                    updated_count += 1

        if hasattr(self, 'ai_name_btn') and self.ai_name_btn:
            self.ai_name_btn.setEnabled(True)
            self.ai_name_btn.setText("🤖 AI命名")

        if updated_count > 0:
            self.main_window.status_bar.showMessage(
                f"✅ AI命名完成: 成功为 {updated_count} 个表格生成名称"
            )
            # 刷新列表和预览
            self.apply_table_filter()
            # 自动保存到缓存
            self.has_unsaved_changes = True
            self._schedule_auto_save()
        else:
            self.main_window.status_bar.showMessage(
                "⚠️ AI命名: 没有成功生成任何表格名称，请检查 API 配置"
            )

        # 清理 worker
        self._ai_worker = None

    # ==================== AI 纠错功能 ====================

    def _get_table_only_tables(self):
        """获取所有表格类型的表格（排除非表格页），预置原始索引"""
        all_tables = self.main_window.processed_results.get('tables', [])
        result = []
        for orig_idx, t in enumerate(all_tables):
            if t.get('parse_status') == 'success':
                t["__index__"] = orig_idx  # 预置原始索引，引擎不会覆盖
                result.append(t)
        return result

    def on_ai_correct_current_clicked(self):
        """AI纠错当前表 - 仅处理当前选中的表格 (Shift+Click = 强制重新分析)"""
        all_tables = self.main_window.processed_results.get('tables', [])
        if not all_tables:
            QMessageBox.warning(self.main_window, "无数据", "请先处理PDF文件，提取表格后再使用AI纠错功能。")
            return

        # 获取当前选中的表格
        current_idx = self._get_current_table_index()
        if current_idx is None:
            QMessageBox.warning(self.main_window, "未选中表格", "请先在表格列表中选中一个表格。")
            return

        current_table = all_tables[current_idx]

        # 检查是否为表格类型
        if current_table.get('parse_status') != 'success':
            QMessageBox.information(
                self.main_window, "非表格页",
                f"当前页面（P{current_table.get('page', '?')}页）不是表格类型，无需 AI 纠错。"
            )
            return

        force = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)

        cache_hint = ""
        if not force:
            pdf_path = getattr(self.main_window, 'current_file', None)
            if pdf_path:
                cached = load_ai_correction_cache(pdf_path)
                if cached and any(r.table_index == current_idx for r in cached):
                    cache_hint = "\n（已有缓存结果，将直接加载。按住 Shift 点击可强制重新分析）"

        page = current_table.get('page', '?')
        reply = QMessageBox.question(
            self.main_window,
            "AI 纠错（当前表）",
            f"将对当前表格（P{page}页，表格 #{current_idx}）进行 AI 分析。\n\n"
            f"这会调用 DeepSeek API，可能需要几秒钟处理时间。{cache_hint}\n继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        # 预置原始索引，引擎 guard 不会覆盖
        current_table["__index__"] = current_idx
        self._run_ai_correction([current_table], force=force)

    def on_ai_correct_all_clicked(self):
        """AI纠错全部 - 处理所有表格类型的表 (Shift+Click = 强制重新分析)"""
        all_tables = self.main_window.processed_results.get('tables', [])
        if not all_tables:
            QMessageBox.warning(self.main_window, "无数据", "请先处理PDF文件，提取表格后再使用AI纠错功能。")
            return

        # 只处理表格类型的表
        table_tables = self._get_table_only_tables()
        non_table_count = len(all_tables) - len(table_tables)

        if not table_tables:
            QMessageBox.information(
                self.main_window, "无表格",
                f"当前文档共 {len(all_tables)} 页，但没有表格类型的页面，无需 AI 纠错。"
            )
            return

        force = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)

        cache_hint = ""
        if not force:
            pdf_path = getattr(self.main_window, 'current_file', None)
            if pdf_path:
                cached = load_ai_correction_cache(pdf_path)
                if cached:
                    cached_count = len([r for r in cached
                                        if r.table_index in set(t.get("__index__", -1) for t in table_tables)])
                    if cached_count == len(table_tables):
                        cache_hint = "\n（全部表格已有缓存结果，将直接加载。按住 Shift 点击可强制重新分析）"

        # 确认
        extra = f"（已自动跳过 {non_table_count} 个非表格页）" if non_table_count > 0 else ""
        reply = QMessageBox.question(
            self.main_window,
            "AI 纠错（全部）",
            f"将对 {len(table_tables)} 张表格进行 AI 分析（命名 + 层级 + 区域判断）。{extra}{cache_hint}\n\n"
            "这会调用 DeepSeek API，可能需要较长时间处理。\n继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        self._run_ai_correction(table_tables, force=force)

    def on_segmenter_clicked(self):
        """liteparse 表格分割验证 — 纯规则驱动，零 API 成本"""
        # 加载 liteparse 缓存
        self._load_liteparse_cache()
        if not self._liteparse_cache:
            QMessageBox.warning(
                self.main_window, "无 liteparse 数据",
                "未找到 liteparse 解析缓存。\n\n"
                "请确保 PDF 提取时已运行 liteparse 旁路解析。"
            )
            return

        self.segmenter_btn.setEnabled(False)
        self.segmenter_btn.setText("📊 分割中...")

        try:
            from codes.table_validator.liteparse_table_segmenter import (
                segment_tables_from_liteparse,
                print_verification_report,
            )

            tables, report = segment_tables_from_liteparse(
                self._liteparse_cache,
                enable_cross_page=True,
            )

            if not tables and report.get("error"):
                QMessageBox.warning(self.main_window, "分割失败",
                                   f"表格分割未产生结果：\n{report['error']}")
                return

            # 生成可读报告
            report_text = print_verification_report(tables, report)

            # 用新的 LiteparseTableDialog 展示（含逐表浏览 + 导出）
            dialog = LiteparseTableDialog(
                tables, report, report_text, parent=self.main_window
            )
            dialog.exec_()

        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.main_window, "错误",
                               f"表格分割失败：\n{str(e)}")
        finally:
            self.segmenter_btn.setEnabled(True)
            self.segmenter_btn.setText("📊 表格分割验证")

    def on_llm_boundary_clicked(self):
        """LLM 边界优化 — 边界检测 + 碎片合并 + liteparse 文本填充"""
        all_tables = self.main_window.processed_results.get('tables', [])
        if not all_tables:
            QMessageBox.warning(self.main_window, "无数据", "请先处理PDF文件，提取表格后再使用 LLM 边界优化。")
            return

        # 仅处理表格类型的表
        table_tables = self._get_table_only_tables()
        if not table_tables:
            QMessageBox.information(self.main_window, "无表格", "当前文档没有表格类型的页面。")
            return

        # 加载 liteparse 缓存
        self._load_liteparse_cache()
        if not self._liteparse_cache:
            QMessageBox.warning(
                self.main_window, "无 liteparse 数据",
                "未找到 liteparse 解析缓存。\n\n"
                "请确保 PDF 提取时已运行 liteparse 旁路解析。"
            )
            return

        reply = QMessageBox.question(
            self.main_window, "确认 LLM 边界优化",
            f"将对 {len(table_tables)} 张表格进行 LLM 边界优化：\n\n"
            f"1️⃣ LLM 识别表格边界 → 合并 pdf2docx 碎片\n"
            f"2️⃣ liteparse 精确文本 → 填充每个单元格\n\n"
            f"⚠ 此操作会调用 DeepSeek API（约按页计费）。\n"
            f"⚠ 合并后表格数量和内容可能与原始有差异。\n\n"
            f"是否继续？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # 禁用按钮，显示处理中
        self.llm_boundary_btn.setEnabled(False)
        self.llm_boundary_btn.setText("🔮 处理中...")

        try:
            from codes.pdf_extractor.table_boundary_llm import run_llm_table_enhancement
            from codes.pdf_extractor.processor import _auto_merge_split_tables

            # 先对原始表做启发式合并（兜底）
            all_tables_copy = [t.copy() for t in all_tables]
            _auto_merge_split_tables(all_tables_copy, liteparse_data=self._liteparse_cache)

            # 定义进度回调
            def progress_cb(pct, msg):
                self.llm_boundary_btn.setText(f"🔮 {msg[:12]}...")

            result = run_llm_table_enhancement(
                all_tables_copy,
                liteparse_data=self._liteparse_cache,
                progress_callback=progress_cb,
                pdf_path=getattr(self.main_window, 'current_file', None),
            )

            if result.get("error"):
                QMessageBox.warning(self.main_window, "部分失败",
                                   f"LLM 边界优化部分步骤失败：\n{result['error']}\n\n"
                                   f"已完成的步骤已应用。")

            # 更新 processed_results
            stats = result["stats"]
            self.main_window.processed_results["tables"] = result["tables"]
            self.main_window.processed_results["total_tables"] = len(result["tables"])

            # 刷新 UI
            self.apply_table_filter()
            self.update_preview_display()

            msg = (f"LLM 边界优化完成 ✅\n\n"
                   f"合并前: {stats['tables_before']} 张表\n"
                   f"合并后: {stats['tables_after']} 张表\n"
                   f"单元格修正: {stats['cells_changed']} 个\n"
                   f"补充行: {stats['rows_added']} 行\n"
                   f"LLM token 消耗: {stats['tokens_used']}")
            QMessageBox.information(self.main_window, "完成", msg)

        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self.main_window, "错误",
                               f"LLM 边界优化失败：\n{str(e)}")
        finally:
            self.llm_boundary_btn.setEnabled(True)
            self.llm_boundary_btn.setText("🔮 LLM 边界优化")

    def _run_ai_correction(self, tables, force=False):
        """通用 AI 纠错启动方法
        Args:
            tables: 待处理的表格列表（已预置 __index__ 为原始索引）
            force: True 跳过缓存，强制重新分析
        """
        pdf_path = getattr(self.main_window, 'current_file', None)

        # ====== 缓存检查（非强制模式） ======
        if pdf_path and not force:
            cached_results = load_ai_correction_cache(pdf_path)
            if cached_results:
                cached_indices = set(r.table_index for r in cached_results)
                requested_indices = set(t.get("__index__", -1) for t in tables)
                if requested_indices.issubset(cached_indices):
                    # 所有请求的表都在缓存中 → 直接使用缓存
                    filtered = [r for r in cached_results if r.table_index in requested_indices]
                    print(f"[AI CACHE] 命中缓存 ({len(filtered)}/{len(requested_indices)} 张表)，跳过分析")
                    self._on_ai_correction_finished(filtered)
                    return
                else:
                    missing = requested_indices - cached_indices
                    print(f"[AI CACHE] 缓存未全覆盖，缺少表: {missing}，重新分析")

        # 获取 PDF context（从 ProcessingWorker 或缓存中）
        pdf_context = getattr(self.main_window, '_pdf_context', None)

        # 禁用两个纠错按钮
        self._disable_correction_buttons()

        self._ai_correction_worker = AICorrectionWorker(
            tables, pdf_context, pdf_path=pdf_path
        )
        self._ai_correction_worker.progress.connect(self._on_ai_correction_progress)
        self._ai_correction_worker.finished.connect(self._on_ai_correction_finished)
        self._ai_correction_worker.error.connect(self._on_ai_correction_error)
        self._ai_correction_worker.start()

        self.main_window.status_bar.showMessage("🔍 AI纠错: 规则预检中...")

    def on_preview_prompt_clicked(self):
        """预览 Prompt 按钮 — 构建 prompt 并弹出编辑弹窗"""
        all_tables = self.main_window.processed_results.get('tables', [])
        if not all_tables:
            QMessageBox.warning(self.main_window, "无数据",
                                "请先处理PDF文件，提取表格后再预览 Prompt。")
            return

        # 判断分析范围：有选中表时默认"当前表"，否则"全部"
        current_idx = self._get_current_table_index()
        table_tables = self._get_table_only_tables()

        # 弹窗让用户选择范围
        from PyQt5.QtWidgets import QDialog, QVBoxLayout as VBL, QHBoxLayout as HBL, \
            QDialogButtonBox as DBB, QRadioButton

        scope_dlg = QDialog(self.main_window)
        scope_dlg.setWindowTitle("选择 Prompt 范围")
        layout = VBL(scope_dlg)

        radio_current = QRadioButton("仅当前选中表格")
        radio_all = QRadioButton(f"全部表格（{len(table_tables)} 张）")
        radio_all.setChecked(True)

        if current_idx is not None:
            radio_current.setEnabled(True)
        else:
            radio_current.setEnabled(False)
            radio_current.setText("仅当前选中表格（无选中表）")

        layout.addWidget(QLabel("请选择要构建 Prompt 的表格范围："))
        layout.addWidget(radio_current)
        layout.addWidget(radio_all)

        buttons = DBB(DBB.Ok | DBB.Cancel)
        buttons.accepted.connect(scope_dlg.accept)
        buttons.rejected.connect(scope_dlg.reject)
        layout.addWidget(buttons)

        if scope_dlg.exec_() != QDialog.Accepted:
            return

        # 确定分析范围
        if radio_current.isChecked() and current_idx is not None:
            scope_tables = [all_tables[current_idx]]
            scope_label = "当前表"
        else:
            scope_tables = table_tables
            scope_label = f"全部（{len(scope_tables)} 张）"

        if not scope_tables:
            QMessageBox.information(self.main_window, "无表格",
                                    "选中的范围内没有可分析的表格。")
            return

        # 预置索引 + 规则预检
        check_results = {}
        for i, t in enumerate(scope_tables):
            if "__index__" not in t:
                idx = t.get("__index__", i)
                t["__index__"] = idx
            check_results[t.get("__index__", i)] = RuleChecker.check(t)

        # 构建 prompt
        pdf_context = getattr(self.main_window, '_pdf_context', None)
        corrector = LLMCorrector()
        try:
            sys_prompt, usr_prompt = corrector.build_prompt_for_preview(
                scope_tables, check_results, pdf_context
            )
        except Exception as e:
            QMessageBox.critical(self.main_window, "构建失败",
                                 f"构建 Prompt 时出错：\n{str(e)}")
            return

        # 获取可用模板列表
        templates = corrector.get_template_list()

        # 弹出编辑弹窗
        dlg = PromptEditDialog(sys_prompt, usr_prompt, analysis_scope=scope_label,
                               parent=self.main_window, templates=templates)

        # 模板切换信号
        def on_template_selected(template_id):
            tpl_sys, tpl_usr = corrector.apply_template(
                template_id, scope_tables, check_results, pdf_context
            )
            if tpl_sys and tpl_usr:
                dlg.apply_template_prompts(tpl_sys, tpl_usr)
            else:
                QMessageBox.warning(self.main_window, "模板加载失败",
                                    f"无法加载模板 '{template_id}'，请检查模板配置。")

        dlg.template_changed.connect(on_template_selected)

        if dlg.exec_() != QDialog.Accepted:
            return  # 用户取消

        # 获取编辑后的 prompt（新格式返回三元组）
        result = dlg.get_edited_prompts()
        if len(result) == 3:
            edited_sys, edited_usr, _template_id = result
        else:
            edited_sys, edited_usr = result

        # 确认发送
        reply = QMessageBox.question(
            self.main_window, "确认发送",
            f"将使用编辑后的 Prompt 对 {scope_label} 表格进行 AI 分析。\n\n"
            f"● System Prompt: {len(edited_sys)} 字符\n"
            f"● User Prompt: {len(edited_usr)} 字符\n\n"
            f"继续发送？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        self._run_ai_correction_with_prompt(scope_tables, edited_sys, edited_usr)

    def _run_ai_correction_with_prompt(self, tables, custom_system_prompt, custom_user_prompt):
        """使用自定义 Prompt 启动 AI 纠错"""
        pdf_path = getattr(self.main_window, 'current_file', None)
        pdf_context = getattr(self.main_window, '_pdf_context', None)

        # 禁用按钮
        self._disable_correction_buttons()

        self._ai_correction_worker = AICorrectionWorker(
            tables, pdf_context, pdf_path=pdf_path,
            custom_system_prompt=custom_system_prompt,
            custom_user_prompt=custom_user_prompt
        )
        self._ai_correction_worker.progress.connect(self._on_ai_correction_progress)
        self._ai_correction_worker.finished.connect(self._on_ai_correction_finished)
        self._ai_correction_worker.error.connect(self._on_ai_correction_error)
        self._ai_correction_worker.start()

        self.main_window.status_bar.showMessage("🔍 AI纠错: 使用自定义 Prompt 分析中...")

    def _disable_correction_buttons(self):
        """禁用所有 AI 纠错按钮"""
        if hasattr(self, 'ai_correct_current_btn') and self.ai_correct_current_btn:
            self.ai_correct_current_btn.setEnabled(False)
        if hasattr(self, 'ai_correct_all_btn') and self.ai_correct_all_btn:
            self.ai_correct_all_btn.setEnabled(False)
        if hasattr(self, 'preview_prompt_btn') and self.preview_prompt_btn:
            self.preview_prompt_btn.setEnabled(False)

    def _enable_correction_buttons(self):
        """恢复所有 AI 纠错按钮"""
        if hasattr(self, 'ai_correct_current_btn') and self.ai_correct_current_btn:
            self.ai_correct_current_btn.setEnabled(True)
        if hasattr(self, 'ai_correct_all_btn') and self.ai_correct_all_btn:
            self.ai_correct_all_btn.setEnabled(True)
        if hasattr(self, 'preview_prompt_btn') and self.preview_prompt_btn:
            self.preview_prompt_btn.setEnabled(True)

    def cancel_ai_correction_worker(self):
        """取消正在运行的 AI 纠错后台线程（切换PDF时调用）"""
        worker = getattr(self, '_ai_correction_worker', None)
        if worker and worker.isRunning():
            # 断开信号，防止旧线程的结果误写入新PDF的数据
            try:
                worker.progress.disconnect(self._on_ai_correction_progress)
            except TypeError:
                pass
            try:
                worker.finished.disconnect(self._on_ai_correction_finished)
            except TypeError:
                pass
            try:
                worker.error.disconnect(self._on_ai_correction_error)
            except TypeError:
                pass
            worker.quit()
            worker.wait(2000)  # 等待最多 2 秒
            print("[AI CORE] 已取消旧PDF的后台纠错任务")
        self._ai_correction_worker = None
        self._enable_correction_buttons()

    def _on_ai_correction_progress(self, percent, message):
        """AI 纠错进度更新"""
        self.main_window.status_bar.showMessage(f"🔍 AI纠错: [{percent}%] {message}")

    def _on_ai_correction_finished(self, correction_results):
        """AI 纠错完成 - 切换到 AI优化 Tab"""
        self._enable_correction_buttons()

        # 一致性守卫：worker 对应的 PDF 与当前 PDF 不一致则丢弃
        worker = self._ai_correction_worker
        if worker and hasattr(worker, 'pdf_path'):
            current = getattr(self.main_window, 'current_file', None)
            if current and worker.pdf_path and current != worker.pdf_path:
                print(f"[AI CORE] 丢弃过期纠错结果 (worker PDF={worker.pdf_path}, current={current})")
                self._ai_correction_worker = None
                return

        stats = self._count_correction_stats(correction_results)
        self.main_window.status_bar.showMessage(
            f"🔍 AI纠错完成: 高置信度{stats['high']} 中{stats['medium']} 需人工{stats['unresolvable']}"
        )

        # 将结果加载到 AI优化 Tab
        ai_tab = getattr(self.main_window, 'ai_correction_tab', None)
        if ai_tab:
            ai_tab.set_results(correction_results, self.main_window.processed_results)

            # 传递实际发送的 Prompt 和 Token 消耗（供事后查看）
            if worker and hasattr(worker, 'engine') and worker.engine:
                prompts = getattr(worker.engine, 'last_prompts', None)
                usage = getattr(worker.engine, 'last_total_usage', None)
                if prompts:
                    ai_tab.set_prompts(prompts[0], prompts[1], usage)

            # 切换到 AI优化 Tab
            tabs = self.main_window.tabs
            for i in range(tabs.count()):
                if tabs.tabText(i) == "🔍 AI优化":
                    tabs.setCurrentIndex(i)
                    break

        # 缓存结果（合并模式：不覆盖已有的其他表缓存）
        pdf_path = getattr(self.main_window, 'current_file', None)
        if pdf_path and correction_results:
            try:
                # 读取已有缓存
                existing = load_ai_correction_cache(pdf_path) or []
                # 合并：本次结果覆盖同 table_index 的旧结果
                existing_map = {r.table_index: r for r in existing}
                for r in correction_results:
                    existing_map[r.table_index] = r
                merged = list(existing_map.values())
                save_ai_correction_cache(pdf_path, merged)
            except Exception as e:
                print(f"[AI CACHE] 保存缓存失败: {e}")

        self._ai_correction_worker = None

    def _on_ai_correction_error(self, error_msg):
        """AI 纠错错误处理"""
        self._enable_correction_buttons()
        QMessageBox.critical(self.main_window, "AI纠错失败", f"纠错过程中发生错误：\n\n{error_msg}")
        self._ai_correction_worker = None

    # ==================== 交叉验证 (liteparse × pdf2docx) ====================

    def on_cross_validate_clicked(self):
        """交叉验证按钮点击 — 启动 liteparse × pdf2docx 交叉验证"""
        all_tables = self.main_window.processed_results.get('tables', [])
        if not all_tables:
            QMessageBox.warning(self.main_window, "无数据",
                                "请先处理PDF文件，提取表格后再使用交叉验证功能。")
            return

        pdf_path = getattr(self.main_window, 'current_file', None)
        if not pdf_path:
            QMessageBox.warning(self.main_window, "无文件", "请先选择一个 PDF 文件。")
            return

        # 检查 liteparse 缓存
        from codes.liteparse_extractor.cache_manager import is_cache_valid as liteparse_cache_valid
        if not liteparse_cache_valid(pdf_path):
            QMessageBox.warning(
                self.main_window, "liteparse 数据不可用",
                "liteparse 缓存数据不存在或已过期。\n\n"
                "请确保 PDF 已完成解析（处理过程中的 liteparse 侧通道会自动生成），"
                "然后重新尝试。"
            )
            return

        # 检查 DeepSeek API
        from codes.pdf_extractor.utils import load_config
        config = load_config()
        api_key = config.get("deepseek_api_key", "")
        if not api_key:
            reply = QMessageBox.question(
                self.main_window, "未配置 API Key",
                "交叉验证需要 DeepSeek API Key（在「配置」Tab 中设置）。\n\n"
                "是否先仅运行规则分类（不调用 LLM）？\n"
                "规则分类可判断真/假表格，但不做深度检查。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply != QMessageBox.Yes:
                return

            # 仅规则分类
            self._run_quick_classify()
            return

        # 正常流程：规则 + LLM
        reply = QMessageBox.question(
            self.main_window,
            "交叉验证",
            "将对所有表格页进行 liteparse × pdf2docx 交叉验证：\n\n"
            "① 规则分类：判断每页是否真表格（零成本）\n"
            "② LLM 深度验证：对真表格页做 5 维度检查（表头/重复/错位/混入文本/拼接）\n\n"
            "LLM 调用将消耗 DeepSeek API 额度。继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        self._run_cross_validation(pdf_path)

    def _run_quick_classify(self):
        """仅规则分类（无 API Key 时）"""
        from codes.table_validator.validator import quick_classify
        from codes.table_validator.models import CrossValidateReport

        processed = self.main_window.processed_results
        try:
            results = quick_classify(processed)
            real = sum(1 for r in results if r.is_real_table)
            fake = sum(1 for r in results if not r.is_real_table)

            report = CrossValidateReport(
                pdf_path=getattr(self.main_window, 'current_file', ''),
                classify_results=results,
                total_pages=processed.get('total_pages', 0),
            )

            dlg = CrossValidationDialog(report, self.main_window)
            dlg.exec_()

            self.main_window.status_bar.showMessage(
                f"🔬 规则分类完成: 真表格 {real} 页, 非表格 {fake} 页"
            )
        except Exception as e:
            QMessageBox.critical(self.main_window, "分类失败", str(e))

    def _run_cross_validation(self, pdf_path):
        """启动完整交叉验证（规则 + LLM）"""
        # 禁用按钮
        if hasattr(self, 'cross_validate_btn') and self.cross_validate_btn:
            self.cross_validate_btn.setEnabled(False)
        if hasattr(self, 'ai_correct_current_btn') and self.ai_correct_current_btn:
            self.ai_correct_current_btn.setEnabled(False)
        if hasattr(self, 'ai_correct_all_btn') and self.ai_correct_all_btn:
            self.ai_correct_all_btn.setEnabled(False)

        processed = self.main_window.processed_results
        self._cv_worker = CrossValidationWorker(pdf_path, processed)
        self._cv_worker.progress.connect(self._on_cv_progress)
        self._cv_worker.finished.connect(self._on_cv_finished)
        self._cv_worker.error.connect(self._on_cv_error)
        self._cv_worker.start()

        self.main_window.status_bar.showMessage("🔬 交叉验证: 规则分类中...")

    def _on_cv_progress(self, step: str, current: int, total: int):
        """交叉验证进度更新"""
        step_names = {
            "classify": "规则分类",
            "classify_done": "规则分类完成",
            "load_liteparse": "加载 liteparse 数据",
            "verify": "LLM 验证",
            "done": "完成",
        }
        name = step_names.get(step, step)
        if step == "verify":
            self.main_window.status_bar.showMessage(
                f"🔬 交叉验证: {name} ({current}/{total})"
            )
        else:
            self.main_window.status_bar.showMessage(f"🔬 交叉验证: {name}...")

    def _on_cv_finished(self, report):
        """交叉验证完成"""
        # 恢复按钮
        if hasattr(self, 'cross_validate_btn') and self.cross_validate_btn:
            self.cross_validate_btn.setEnabled(True)
        if hasattr(self, 'ai_correct_current_btn') and self.ai_correct_current_btn:
            self.ai_correct_current_btn.setEnabled(True)
        if hasattr(self, 'ai_correct_all_btn') and self.ai_correct_all_btn:
            self.ai_correct_all_btn.setEnabled(True)

        self._cv_worker = None

        self.main_window.status_bar.showMessage(
            f"🔬 交叉验证完成: 真表格 {report.real_table_count}, "
            f"假表格 {report.fake_table_count}, LLM验证 {report.verified_count} 页"
        )

        # 显示结果对话框
        dlg = CrossValidationDialog(report, self.main_window)
        dlg.exec_()

    def _on_cv_error(self, error_msg):
        """交叉验证错误处理"""
        if hasattr(self, 'cross_validate_btn') and self.cross_validate_btn:
            self.cross_validate_btn.setEnabled(True)
        if hasattr(self, 'ai_correct_current_btn') and self.ai_correct_current_btn:
            self.ai_correct_current_btn.setEnabled(True)
        if hasattr(self, 'ai_correct_all_btn') and self.ai_correct_all_btn:
            self.ai_correct_all_btn.setEnabled(True)
        self._cv_worker = None
        QMessageBox.critical(self.main_window, "交叉验证失败",
                             f"验证过程中发生错误：\n\n{error_msg}")

    # ==================== AI 纠错应用 ====================

    def _apply_corrections(self, accepted_results, confirm_status):
        """将确认的修正应用到 processed_results

        Args:
            accepted_results: [CorrectionResult] 被接受的修正
            confirm_status: {table_index: "accepted"/"rejected"/"pending"}
        """
        tables = self.main_window.processed_results.get('tables', [])
        if not tables:
            return

        updated_count = 0
        merge_pairs = []  # [(target_idx, source_indices), ...]

        for r in accepted_results:
            idx = r.table_index
            if idx >= len(tables):
                continue

            tbl = tables[idx]

            # == 核心：应用 LLM 重构数据（reconstructed_data） ==
            if r.reconstructed_data:
                if "original_data" not in tbl:
                    tbl["original_data"] = _deep_copy_list(tbl.get("data", []))
                tbl["data"] = r.reconstructed_data
                tbl["ai_reconstructed"] = True
                tbl["ai_changes_log"] = r.changes_log
                updated_count += 1

            # == 应用 P4 确定性修正（corrected_data from auto_fixed_code） ==
            elif r.corrected_data and r.diff_source == "deterministic":
                if "original_data" not in tbl:
                    tbl["original_data"] = _deep_copy_list(tbl.get("data", []))
                tbl["data"] = r.corrected_data
                tbl["ai_code_fixed"] = True
                tbl["ai_changes_log"] = r.changes_log
                updated_count += 1

            # == 应用 LLM 合并建议 ==
            if r.merge_source_indices:
                merge_pairs.append((idx, r.merge_source_indices))

            # == 应用修正数据（reconstructed_data 已作为 corrected_data 时跳过重复） ==
            if r.corrected_data and not r.reconstructed_data and r.diff_source != "deterministic":
                if "original_data" not in tbl:
                    tbl["original_data"] = _deep_copy_list(tbl.get("data", []))
                tbl["data"] = r.corrected_data
                updated_count += 1

            # 应用命名
            if r.name_title:
                tbl["llm_title"] = r.name_title
                tbl["llm_summary"] = r.name_summary
                updated_count += 1

            # 应用层级（保存到表格的扩展字段）
            if r.hierarchy:
                tbl["ai_hierarchy"] = r.hierarchy
                tbl["ai_hierarchy_verified"] = r.hierarchy_verified

            # 应用区域判断
            if r.region_merge_prev is not None or r.region_merge_next is not None:
                tbl["ai_region"] = {
                    "is_complete": r.region_is_complete,
                    "merge_prev": r.region_merge_prev,
                    "merge_next": r.region_merge_next,
                    "split_rows": r.region_split_rows
                }

            # 应用修正数据（兼容旧字段）
            if r.corrected_data and r.reconstructed_data:
                # 已有 reconstructed_data，corrected_data 作为补充记录
                pass

            # 保存纠错元数据（含新状态标记）
            tbl["ai_correction_status"] = r.status
            tbl["ai_correction_confidence"] = r.confidence
            tbl["ai_correction_applied_rules"] = r.applied_rules
            tbl["ai_correction_summary"] = r.changes_summary
            tbl["ai_diff_source"] = getattr(r, "diff_source", "unknown")

        # == 执行实际合并操作 ==
        merge_count = 0
        if merge_pairs:
            try:
                merge_count = self._execute_llm_merges(tables, merge_pairs)
            except Exception as e:
                print(f"[AI CORRECTION] LLM合并执行失败: {e}")

        if updated_count > 0 or merge_count > 0:
            # LLM 纠错完成后：自动尝试跨页合并（补充未在 LLM 中直接合并的）
            try:
                tables_before = len(tables)
                _auto_merge_split_tables(tables, cross_page=True)
                auto_merge_count = tables_before - len(tables)
                if auto_merge_count > 0:
                    merge_count += auto_merge_count
            except Exception as e:
                print(f"[AI CORRECTION] 跨页合并失败: {e}")

            self.main_window.processed_results['tables'] = tables
            self.main_window.processed_results['total_tables'] = len(tables)

            # 刷新列表和预览
            self.apply_table_filter()
            self.has_unsaved_changes = True
            self._schedule_auto_save()

            msg = f"✅ 已应用 AI 纠错：{updated_count} 张表格已更新"
            if merge_count > 0:
                msg += f" | 🔗 合并 {merge_count} 对表格"
            self.main_window.status_bar.showMessage(msg, 8000)
        else:
            self.main_window.status_bar.showMessage(
                "⚠️ AI 纠错：没有应用任何修改", 3000
            )

    def _execute_llm_merges(self, tables, merge_pairs):
        """执行 LLM 建议的表格合并。

        Args:
            tables: 表格列表（原地修改）
            merge_pairs: [(target_idx, [source_idx1, source_idx2, ...]), ...]
                         target_idx 是主表索引，source_indices 是待合并的表索引

        Returns:
            int: 实际合并的对数
        """
        # 安全处理：排除循环引用和重复
        merged_sources = set()  # 已经被合并走的 source 索引
        valid_pairs = []

        for target_idx, source_indices in merge_pairs:
            if target_idx >= len(tables):
                continue
            valid_sources = []
            for si in source_indices:
                if si >= len(tables) or si == target_idx:
                    continue
                if si in merged_sources:
                    continue
                # 不能合并已经被合并的表
                if si == target_idx:
                    continue
                valid_sources.append(si)
            if valid_sources:
                valid_pairs.append((target_idx, valid_sources))
                merged_sources.update(valid_sources)

        if not valid_pairs:
            return 0

        # 从大到小删除 source 表（避免索引错乱）
        to_remove = set()
        for _, sources in valid_pairs:
            to_remove.update(sources)

        # 执行合并：将 source 表的数据追加到 target 表
        for target_idx, sources in valid_pairs:
            target = tables[target_idx]
            target_data = target.get("data", [])
            # 确保是列表
            if not isinstance(target_data, list):
                target_data = list(target_data)
            target_cols = max((len(r) for r in target_data), default=0)

            for si in sorted(sources):  # 按索引顺序合并
                if si >= len(tables):
                    continue
                source = tables[si]
                source_data = source.get("data", [])
                if not source_data:
                    continue

                # 对齐列数到目标表
                for row in source_data:
                    while len(row) < target_cols:
                        row.append("")
                    # 裁剪到目标列数
                    if len(row) > target_cols:
                        row = row[:target_cols]

                # 追加到目标表（插入空行分隔）
                if target_data:
                    target_data.append([""] * target_cols)  # 分隔行
                target_data.extend(source_data)

                # 记录合并标记
                target["ai_merged_from"] = target.get("ai_merged_from", []) + [si]
                # 也合并 original_data 如果存在
                if "original_data" in target and "original_data" in source:
                    target["original_data"].extend(source.get("original_data", []))

            target["data"] = target_data

        # 删除被合并的源表
        for idx in sorted(to_remove, reverse=True):
            if idx < len(tables):
                tables.pop(idx)

        return len(valid_pairs)

    def _count_correction_stats(self, results):
        """统计纠错结果"""
        stats = {"high": 0, "medium": 0, "low": 0, "unresolvable": 0}
        for r in results:
            stats[r.confidence] = stats.get(r.confidence, 0) + 1
        return stats


class AINameWorker(QThread):
    """AI 命名后台工作线程"""
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(list)

    def __init__(self, tables, config):
        super().__init__()
        self.tables = tables
        self.config = config

    def run(self):
        from codes.pdf_extractor import TableContextLLM

        llm = TableContextLLM(
            api_key=self.config.get("deepseek_api_key", ""),
            endpoint=self.config.get("deepseek_endpoint", "api.deepseek.com"),
            model=self.config.get("deepseek_model", "deepseek-chat")
        )

        llm_results = llm.batch_generate(
            self.tables,
            progress_callback=lambda c, t, m: self.progress.emit(c, t, m)
        )

        final_results = []
        for i, result in enumerate(llm_results):
            final_results.append({
                "index": self.tables[i]["index"],
                "title": result.get("title", ""),
                "summary": result.get("summary", ""),
                "error": result.get("error", ""),
            })

        self.finished.emit(final_results)


class AICorrectionWorker(QThread):
    """AI 纠错后台工作线程"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, tables, pdf_context=None, pdf_path=None,
                 custom_system_prompt=None, custom_user_prompt=None):
        super().__init__()
        self.tables = tables
        self.pdf_context = pdf_context
        self.pdf_path = pdf_path
        self.custom_system_prompt = custom_system_prompt
        self.custom_user_prompt = custom_user_prompt

    def run(self):
        from codes.pdf_extractor.ai_correction import CorrectionEngine

        try:
            self.engine = CorrectionEngine(self.tables, self.pdf_context)
            self.engine.progress.connect(self.progress.emit)

            results = self.engine.run(
                custom_system_prompt=self.custom_system_prompt,
                custom_user_prompt=self.custom_user_prompt
            )
            self.finished.emit(results)

        except Exception as e:
            import traceback
            self.error.emit(f"{str(e)}\n{traceback.format_exc()}")


# 辅助函数：深拷贝列表
def _deep_copy_list(data):
    """深拷贝二维列表"""
    return [[cell for cell in row] for row in data]
