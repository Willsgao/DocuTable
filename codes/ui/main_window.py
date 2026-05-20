# -*- coding: utf-8 -*-
"""
主窗口模块 - MainWindow 类
管理整体UI布局、Tab页初始化、委托方法
"""

import os
import time
import atexit
from datetime import datetime

from codes.pdf_extractor import (
    cleanup_temp_files, load_config, save_config,
    load_pdf_history, save_pdf_history,
    get_all_cached_files, delete_cache_file,
    get_cached_pdf_info,
    load_mid_data, save_mid_data,
    PDFPreviewWidget, ZoomableScrollArea
)

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QMessageBox,
    QStatusBar, QGroupBox, QSpinBox, QTabWidget, QListWidget,
    QComboBox, QLineEdit, QTextBrowser, QSplitter,
    QTableWidget, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_file = None
        self.processed_results = None
        self.config = load_config()

        # 初始化控件
        self.table_widget = None
        self.excel_widget = None
        self._using_excel_mode = False
        self._excel_opened = False
        self._last_excel_path = None

        # 预览图片列表
        self.preview_images = []

        # 历史记录
        self.pdf_history = load_pdf_history()

        # 管理器（初始化UI时创建）
        self.table_compare_manager = None
        self.processing_manager = None
        self.file_manager = None
        self.preview_manager = None
        self.history_manager = None
        self.export_manager = None
        self.settings_manager = None

        self.init_ui()
        self.apply_styles()

    def closeEvent(self, event):
        """窗口关闭时清理"""
        # 保存表格编辑（如果有待保存的更改）
        if self.table_compare_manager and self.table_compare_manager.has_unsaved_changes:
            self.table_compare_manager._do_auto_save()
        save_pdf_history(self.pdf_history)
        cleanup_temp_files()
        event.accept()

    # ==================== UI初始化 ====================

    def init_ui(self):
        """初始化主UI"""
        self.setWindowTitle("银行年报PDF解析工具 v1.0")
        self.setGeometry(100, 100, 1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 标题
        title_label = QLabel("📊 银行年报PDF解析工具")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #2C3E50; padding: 10px;")
        main_layout.addWidget(title_label)

        # 初始化所有管理器（必须在tab创建之前）
        from codes.ui.processing_manager import ProcessingManager
        from codes.ui.file_manager import FileManager
        from codes.ui.table_compare_manager import TableCompareManager
        from codes.ui.preview_manager import PreviewManager
        from codes.ui.history_manager import HistoryManager
        from codes.ui.export_manager import ExportManager
        from codes.ui.settings_manager import SettingsManager

        self.processing_manager = ProcessingManager(self)
        self.file_manager = FileManager(self)
        self.table_compare_manager = TableCompareManager(self)
        self.preview_manager = PreviewManager(self)
        self.history_manager = HistoryManager(self)
        self.export_manager = ExportManager(self)
        self.settings_manager = SettingsManager(self)

        # Tab页
        self.tabs = QTabWidget()

        # Tab1: 主处理界面
        self.main_tab = QWidget()
        self._init_main_tab()
        self.tabs.addTab(self.main_tab, "🔄 处理")

        # Tab2: 对比预览
        self.preview_tab = QWidget()
        self._init_preview_tab()
        self.tabs.addTab(self.preview_tab, "👁 对比预览")

        # Tab3: 历史记录
        self.history_tab = QWidget()
        self._init_history_tab()
        self.tabs.addTab(self.history_tab, "📜 历史记录")

        # Tab4: 配置
        self.config_tab = QWidget()
        self._init_config_tab()
        self.tabs.addTab(self.config_tab, "⚙️ 配置")

        main_layout.addWidget(self.tabs)

        # 进度条 + 耗时计时器
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        progress_layout.addWidget(self.progress_bar)
        self.progress_timer_label = QLabel("")
        self.progress_timer_label.setVisible(False)
        self.progress_timer_label.setFixedWidth(80)
        self.progress_timer_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_layout.addWidget(self.progress_timer_label)
        main_layout.addLayout(progress_layout)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 | 银行年报PDF解析工具 v1.0")

        # 提取器版本标签（永久显示在右下角）
        extraction_version = self.config.get("extraction_version", "v2")
        self.extractor_version_label = QLabel(f"提取器: <b>{extraction_version.upper()}</b>")
        self.extractor_version_label.setStyleSheet(
            "QLabel { padding: 0 8px; color: #2980B9; font-size: 12px; }"
        )
        self.status_bar.addPermanentWidget(self.extractor_version_label)

    def _init_main_tab(self):
        """初始化主处理Tab"""
        main_tab_layout = QVBoxLayout(self.main_tab)

        # 文件选择
        file_group = QGroupBox("📁 文件选择")
        file_layout = QVBoxLayout(file_group)

        file_btn_layout = QHBoxLayout()
        self.select_file_btn = QPushButton("📂 选择PDF文件")
        self.select_file_btn.clicked.connect(self.select_file)
        file_btn_layout.addWidget(self.select_file_btn)

        self.file_label = QLabel("未选择文件")
        self.file_label.setStyleSheet("color: #7F8C8D; font-style: italic;")
        file_btn_layout.addWidget(self.file_label)
        file_btn_layout.addStretch()
        file_layout.addLayout(file_btn_layout)

        # 处理选项
        options_layout = QHBoxLayout()
        options_layout.addWidget(QLabel("处理模式:"))

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["自动检测（推荐）", "仅文本提取", "仅AI识别"])
        self.mode_combo.setCurrentIndex(0)
        options_layout.addWidget(self.mode_combo)

        options_layout.addWidget(QLabel("  最大页数:"))
        self.max_pages_spin = QSpinBox()
        self.max_pages_spin.setRange(1, 500)
        self.max_pages_spin.setValue(self.config.get("max_pages", 500))
        options_layout.addWidget(self.max_pages_spin)
        options_layout.addStretch()

        self.process_btn = QPushButton("🚀 开始处理")
        self.process_btn.clicked.connect(self.start_processing)
        self.process_btn.setEnabled(False)
        self.process_btn.setMinimumHeight(45)
        options_layout.addWidget(self.process_btn)

        file_layout.addLayout(options_layout)
        main_tab_layout.addWidget(file_group)

        # 预览区域
        preview_group = QGroupBox("📋 处理结果预览")
        preview_layout = QVBoxLayout(preview_group)

        self.preview_text = QTextBrowser()
        self.preview_text.setPlaceholderText("处理完成后，结果将显示在这里...\n\n可以点击'导出Excel'按钮保存结果。")
        preview_layout.addWidget(self.preview_text)

        export_btn_layout = QHBoxLayout()
        self.export_btn = QPushButton("💾 导出为Excel")
        self.export_btn.clicked.connect(self.export_to_excel)
        self.export_btn.setEnabled(False)
        export_btn_layout.addWidget(self.export_btn)
        export_btn_layout.addStretch()
        preview_layout.addLayout(export_btn_layout)
        main_tab_layout.addWidget(preview_group)

    def _init_preview_tab(self):
        """初始化对比预览Tab"""
        preview_layout = QVBoxLayout(self.preview_tab)

        # 解析结果统计区域 - 紧凑单行显示
        parse_stats_group = QGroupBox()
        parse_stats_group.setFixedHeight(30)
        parse_stats_group.setStyleSheet("QGroupBox { border: none; margin-top: 0px; padding-top: 0px; }")
        parse_stats_layout = QHBoxLayout(parse_stats_group)
        parse_stats_layout.setContentsMargins(5, 0, 5, 0)

        self.parse_stats_label = QLabel("📊 尚未处理PDF文件")
        self.parse_stats_label.setStyleSheet("font-size: 12px; font-weight: bold; color: #2C3E50;")
        parse_stats_layout.addWidget(self.parse_stats_label)
        parse_stats_layout.addStretch()
        preview_layout.addWidget(parse_stats_group)

        # 创建水平分割器
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：PDF预览（通过PreviewManager创建）
        pdf_group = QGroupBox("📄 PDF页面预览")
        pdf_layout = QVBoxLayout(pdf_group)

        zoom_hint = QLabel("💡 Ctrl+滚轮缩放 | 拖拽移动")
        zoom_hint.setStyleSheet("color: #888; font-size: 11px; padding: 2px;")
        zoom_hint.setMaximumHeight(18)
        pdf_layout.addWidget(zoom_hint)

        # 通过 PreviewManager 创建加载标签和PDF预览区域
        pdf_layout.addWidget(self.preview_manager.setup_pdf_loading_label())
        pdf_layout.addWidget(self.preview_manager.setup_pdf_preview(self))

        # PDF下方导航按钮 - 重新设计的翻页布局
        pdf_nav_layout = QHBoxLayout()
        pdf_nav_layout.addStretch()
        
        # 左侧：首页/上一页
        self.first_page_btn = QPushButton("◀◀ 首页")
        self.first_page_btn.clicked.connect(self.first_preview_page)
        self.first_page_btn.setEnabled(False)
        self.first_page_btn.setMaximumWidth(80)
        pdf_nav_layout.addWidget(self.first_page_btn)

        self.prev_page_btn = QPushButton("◀ 上一页")
        self.prev_page_btn.clicked.connect(self.prev_preview_page)
        self.prev_page_btn.setEnabled(False)
        self.prev_page_btn.setMaximumWidth(80)
        pdf_nav_layout.addWidget(self.prev_page_btn)

        # 中间：页码信息 + 跳转功能
        center_layout = QHBoxLayout()
        center_layout.setSpacing(5)
        
        self.page_info_label = QLabel("第 0/0 页")
        self.page_info_label.setAlignment(Qt.AlignCenter)
        self.page_info_label.setStyleSheet("font-weight: bold; color: #2C3E50; padding: 0 8px;")
        center_layout.addWidget(self.page_info_label)
        
        # 跳转功能区域
        center_layout.addSpacing(15)
        center_layout.addWidget(QLabel("跳至:"))
        
        self.goto_page_input = QSpinBox()
        self.goto_page_input.setMinimum(1)
        self.goto_page_input.setMaximum(9999)
        self.goto_page_input.setValue(1)
        self.goto_page_input.setMaximumWidth(60)
        self.goto_page_input.setEnabled(False)
        self.goto_page_input.setKeyboardTracking(False)
        self.goto_page_input.valueChanged.connect(self.on_goto_page_changed)
        center_layout.addWidget(self.goto_page_input)
        
        self.goto_page_btn = QPushButton("跳转")
        self.goto_page_btn.clicked.connect(self.goto_preview_page)
        self.goto_page_btn.setEnabled(False)
        self.goto_page_btn.setMaximumWidth(50)
        center_layout.addWidget(self.goto_page_btn)
        
        pdf_nav_layout.addLayout(center_layout)

        # 右侧：下一页/末页
        self.next_page_btn = QPushButton("下一页 ▶")
        self.next_page_btn.clicked.connect(self.next_preview_page)
        self.next_page_btn.setEnabled(False)
        self.next_page_btn.setMaximumWidth(80)
        pdf_nav_layout.addWidget(self.next_page_btn)

        self.last_page_btn = QPushButton("末页 ▶▶")
        self.last_page_btn.clicked.connect(self.last_preview_page)
        self.last_page_btn.setEnabled(False)
        self.last_page_btn.setMaximumWidth(80)
        pdf_nav_layout.addWidget(self.last_page_btn)
        pdf_nav_layout.addStretch()

        pdf_layout.addLayout(pdf_nav_layout)
        splitter.addWidget(pdf_group)

        # 右侧：表格预览 - 使用管理器初始化
        table_group = QGroupBox("📊 表格数据（双击编辑）")
        table_layout = QVBoxLayout(table_group)

        # 创建表格管理器
        self.table_compare_manager.init_ui(table_group)

        # 添加到splitter
        splitter.addWidget(table_group)

        # 设置Splitter比例 - PDF区域占更大比例
        splitter.setSizes([600, 400])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setMinimumHeight(300)

        preview_layout.addWidget(splitter)

        # 内部变量
        self.current_preview_index = 0

        # 将管理器中的控件引用同步到主窗口
        self._sync_manager_controls()

    def _sync_manager_controls(self):
        """同步管理器中的控件引用到主窗口"""
        mgr = self.table_compare_manager
        self.table_widget = mgr.table_widget
        self.table_list_widget = mgr.table_list_widget
        self.table_type_filter = mgr.table_type_filter
        self.table_type_label = mgr.table_type_label
        self.filter_nav_label = mgr.filter_nav_label
        self.filter_count_label = mgr.filter_count_label
        self.filter_input = mgr.filter_input
        self.stats_label = mgr.stats_label
        self.prev_filtered_btn = mgr.prev_filtered_btn
        self.next_filtered_btn = mgr.next_filtered_btn
        self.toggle_table_type_btn = mgr.toggle_table_type_btn
        self.save_status_btn = mgr.save_status_btn
        self.goto_export_btn = mgr.goto_export_btn

        # 同步PDF预览控件（PreviewManager创建）
        pm = self.preview_manager
        self.pdf_preview_widget = pm.pdf_preview_widget
        self.pdf_scroll_area = pm.pdf_scroll_area
        self.pdf_loading_label = pm.pdf_loading_label

    def _init_history_tab(self):
        """初始化历史记录Tab"""
        history_layout = QVBoxLayout(self.history_tab)
        history_layout.setContentsMargins(10, 10, 10, 10)

        # 标题栏
        header_layout = QHBoxLayout()
        title_label = QLabel("📜 PDF解析历史记录")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #2C3E50;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # 清空历史按钮
        self.clear_history_btn = QPushButton("🗑️ 清空历史")
        self.clear_history_btn.clicked.connect(self.clear_pdf_history)
        self.clear_history_btn.setStyleSheet("""
            QPushButton { background-color: #E74C3C; color: white; padding: 5px 15px; border-radius: 4px; }
            QPushButton:hover { background-color: #C0392B; }
        """)
        header_layout.addWidget(self.clear_history_btn)

        history_layout.addLayout(header_layout)

        # 统计信息
        self.history_stats_label = QLabel("共 0 条记录")
        self.history_stats_label.setStyleSheet("color: #7F8C8D; font-size: 12px; padding: 5px 0;")
        history_layout.addWidget(self.history_stats_label)

        # 分类筛选
        filter_layout = QHBoxLayout()
        filter_label = QLabel("筛选状态:")
        filter_label.setStyleSheet("font-weight: bold;")
        filter_layout.addWidget(filter_label)

        self.history_filter_combo = QComboBox()
        self.history_filter_combo.addItems(["全部", "✅ 成功", "❌ 图片类PDF", "⚠️ 部分失败", "❌ 解析失败"])
        self.history_filter_combo.currentTextChanged.connect(self.filter_history_list)
        filter_layout.addWidget(self.history_filter_combo)

        filter_layout.addSpacing(20)

        # 显示模式切换
        mode_label = QLabel("显示模式:")
        mode_label.setStyleSheet("font-weight: bold;")
        filter_layout.addWidget(mode_label)

        self.history_mode_combo = QComboBox()
        self.history_mode_combo.addItems(["📌 最新一次", "📋 全部记录"])
        self.history_mode_combo.setCurrentIndex(0)
        self.history_mode_combo.currentTextChanged.connect(self.filter_history_list)
        filter_layout.addWidget(self.history_mode_combo)

        filter_layout.addStretch()
        history_layout.addLayout(filter_layout)

        # 历史记录列表（表格形式）
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(8)
        self.history_table.setHorizontalHeaderLabels(["删除", "文件名", "状态", "总页数", "成功", "加载中", "预览", "处理时间"])
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setColumnWidth(0, 60)   # 删除列
        self.history_table.setColumnWidth(1, 180)   # 文件名
        self.history_table.setColumnWidth(2, 90)    # 状态
        self.history_table.setColumnWidth(3, 55)    # 总页数
        self.history_table.setColumnWidth(4, 45)    # 成功
        self.history_table.setColumnWidth(5, 80)    # 加载中
        self.history_table.setColumnWidth(6, 50)   # 预览
        self.history_table.setColumnWidth(7, 120)  # 处理时间

        # 加载状态定时器
        self.loading_timer = QTimer()
        self.loading_timer.timeout.connect(self._update_loading_time)
        self.loading_start_time = None
        self.loading_row = -1
        self.history_table.cellClicked.connect(self.on_history_cell_clicked)
        self.history_table.setStyleSheet("""
            QTableWidget { border: 1px solid #ddd; border-radius: 4px; }
            QTableWidget::item { padding: 5px; }
            QPushButton { background-color: #5D6D7E; color: white; border: none;
                          padding: 4px 12px; border-radius: 4px; font-size: 12px; }
            QPushButton:hover { background-color: #4A5568; }
        """)
        history_layout.addWidget(self.history_table)

        # 底部说明
        hint_label = QLabel("💡 点击「预览」按钮可查看PDF与表格对比")
        hint_label.setStyleSheet("color: #7F8C8D; font-size: 11px; padding: 5px 0;")
        history_layout.addWidget(hint_label)

        # 注入控件引用到 history_manager
        self.history_manager.setup_history_tab(
            self.history_table, self.history_filter_combo,
            self.history_mode_combo, self.history_stats_label, None
        )

        # 加载历史记录
        self.refresh_history_list()

    def _init_config_tab(self):
        """初始化配置Tab"""
        config_tab_layout = QVBoxLayout(self.config_tab)

        # API配置
        config_group = QGroupBox("⚙️ API配置")
        config_layout = QVBoxLayout(config_group)

        # 提取器版本
        version_layout = QHBoxLayout()
        version_layout.addWidget(QLabel("表格提取版本:"))
        self.extraction_version_combo = QComboBox()
        self.extraction_version_combo.addItems(["v1（位置分析+pdfplumber混合）", "v2（表格线+对齐聚簇）"])
        current_version = self.config.get("extraction_version", "v2")
        self.extraction_version_combo.setCurrentIndex(0 if current_version == "v1" else 1)
        version_layout.addWidget(self.extraction_version_combo)
        version_layout.addStretch()
        config_layout.addLayout(version_layout)

        # API Key
        api_key_layout = QHBoxLayout()
        api_key_layout.addWidget(QLabel("豆包API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setText(self.config.get("doubao_api_key", ""))
        self.api_key_input.setPlaceholderText("输入豆包API Key（用于图片型PDF识别）")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        api_key_layout.addWidget(self.api_key_input)

        self.show_key_btn = QPushButton("👁")
        self.show_key_btn.setMaximumWidth(40)
        self.show_key_btn.clicked.connect(self.toggle_key_visibility)
        api_key_layout.addWidget(self.show_key_btn)
        config_layout.addLayout(api_key_layout)

        # Endpoint
        endpoint_layout = QHBoxLayout()
        endpoint_layout.addWidget(QLabel("推理接入点:"))
        self.endpoint_input = QLineEdit()
        self.endpoint_input.setText(self.config.get("doubao_endpoint", "ark.cn-beijing.volces.com"))
        self.endpoint_input.setPlaceholderText("如: ark.cn-beijing.volces.com")
        endpoint_layout.addWidget(self.endpoint_input)
        config_layout.addLayout(endpoint_layout)

        # Model
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("模型名称:"))
        self.model_input = QLineEdit()
        self.model_input.setText(self.config.get("doubao_model", "doubao-pro-32k"))
        self.model_input.setPlaceholderText("如: doubao-pro-32k")
        model_layout.addWidget(self.model_input)

        self.test_api_btn = QPushButton("🧪 测试连接")
        self.test_api_btn.clicked.connect(self.test_api)
        model_layout.addWidget(self.test_api_btn)
        config_layout.addLayout(model_layout)

        # 保存按钮
        save_layout = QHBoxLayout()
        self.save_config_btn = QPushButton("💾 保存配置")
        self.save_config_btn.clicked.connect(self.save_settings)
        save_layout.addWidget(self.save_config_btn)
        save_layout.addStretch()
        config_layout.addLayout(save_layout)

        # 缓存管理
        cache_group = QGroupBox("💾 缓存管理")
        cache_layout = QVBoxLayout(cache_group)

        self.cache_stats_label = QLabel("正在加载缓存信息...")
        cache_layout.addWidget(self.cache_stats_label)

        self.cache_list_widget = QListWidget()
        self.cache_list_widget.setMaximumHeight(150)
        cache_layout.addWidget(self.cache_list_widget)

        # 注入控件引用到 file_manager
        self.file_manager.setup_cache_tab(self.cache_list_widget, self.cache_stats_label)

        cache_btn_layout = QHBoxLayout()
        self.refresh_cache_btn = QPushButton("🔄 刷新列表")
        self.refresh_cache_btn.clicked.connect(self.refresh_cache_list)
        cache_btn_layout.addWidget(self.refresh_cache_btn)

        self.delete_cache_btn = QPushButton("🗑️ 删除选中")
        self.delete_cache_btn.clicked.connect(self.delete_selected_cache)
        cache_btn_layout.addWidget(self.delete_cache_btn)

        self.delete_all_cache_btn = QPushButton("⚠️ 清空全部缓存")
        self.delete_all_cache_btn.clicked.connect(self.delete_all_cache)
        cache_btn_layout.addWidget(self.delete_all_cache_btn)

        cache_layout.addLayout(cache_btn_layout)
        config_layout.addWidget(cache_group)

        QTimer.singleShot(100, self.refresh_cache_list)

        config_tab_layout.addWidget(config_group)

        # 说明
        info_group = QGroupBox("📖 使用说明")
        info_layout = QVBoxLayout(info_group)

        info_text = QLabel(
            "<b>使用流程：</b><br>"
            "1. 如果处理图片型PDF（扫描件），需要先在'配置'页面填写API Key<br>"
            "2. 点击'选择PDF文件'选择要处理的年报PDF<br>"
            "3. 选择处理模式（自动检测会根据PDF类型选择最优方式）<br>"
            "4. 点击'开始处理'等待完成<br>"
            "5. 预览结果后点击'导出为Excel'保存<br><br>"
            "<b>处理模式说明：</b><br>"
            "• <b>自动检测</b>：自动识别PDF类型并选择最优处理方式<br>"
            "• <b>仅文本提取</b>：只从可复制的PDF中提取表格（快速，无需API）<br>"
            "• <b>仅AI识别</b>：将所有页面转为图片并调用AI识别（慢，但准确率高）"
        )
        info_text.setWordWrap(True)
        info_layout.addWidget(info_text)

        config_tab_layout.addWidget(info_group)
        config_tab_layout.addStretch()

    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QGroupBox {
                font-size: 14px; font-weight: bold;
                border: 1px solid #BDC3C7; border-radius: 6px;
                margin-top: 8px; padding-top: 8px;
            }
            QGroupBox::title { color: #5D6D7E; padding: 0 8px; }
            QPushButton {
                background-color: #5D6D7E; color: white;
                border: none; border-radius: 4px;
                padding: 6px 14px; font-size: 13px;
            }
            QPushButton:hover { background-color: #4A5568; }
            QPushButton:pressed { background-color: #4A5568; }
            QPushButton:disabled { background-color: #BDC3C7; }
            QLineEdit, QTextEdit, QTextBrowser {
                border: 1px solid #CCC; border-radius: 4px;
                padding: 6px; font-size: 13px;
            }
            QProgressBar {
                border: 1px solid #CCC; border-radius: 4px;
                text-align: center; height: 20px;
            }
            QProgressBar::chunk { background-color: #5D6D7E; border-radius: 4px; }
            QTabWidget::pane { border: 1px solid #CCC; border-radius: 4px; }
            QTabBar::tab { padding: 8px 16px; margin-right: 2px; }
            QTabBar::tab:selected { background: #5D6D7E; color: white; border-radius: 4px; }
            QComboBox, QSpinBox { border: 1px solid #CCC; border-radius: 4px; padding: 5px; }
        """)

    # ==================== 文件选择和处理 ====================

    def select_file(self):
        """选择文件，智能加载缓存（委托给processing_manager）"""
        if self.processing_manager:
            self.processing_manager.select_file()

    def start_processing(self):
        """开始处理（委托给processing_manager）"""
        if self.processing_manager:
            self.processing_manager.start_processing()

    def update_progress(self, value, msg):
        """更新进度（委托给processing_manager）"""
        if self.processing_manager:
            self.processing_manager.update_progress(value, msg)

    def on_processing_finished(self, result):
        """处理完成（委托给processing_manager）"""
        if self.processing_manager:
            self.processing_manager.on_processing_finished(result)

    def on_processing_error(self, error_msg):
        """处理错误（委托给processing_manager）"""
        if self.processing_manager:
            self.processing_manager.on_processing_error(error_msg)

    # ==================== PDF预览 ====================

    def generate_pdf_preview_images(self):
        """生成PDF页面截图（委托给preview_manager）"""
        if self.preview_manager:
            self.preview_manager.generate_pdf_preview_images()

    def update_preview_tab(self):
        """更新预览Tab（委托给preview_manager）"""
        if self.preview_manager:
            self.preview_manager.update_preview_tab()
        # 切换到预览Tab
        self.tabs.setCurrentIndex(1)

    def on_zoom_changed(self, zoom_factor):
        """缩放改变（委托给preview_manager）"""
        if self.preview_manager:
            self.preview_manager.on_zoom_changed(zoom_factor)


    def on_table_selected(self, item):
        """表格选中"""
        if item:
            self.table_compare_manager.on_table_selected(item)

    def on_table_type_filter_changed(self):
        """筛选类型改变"""
        self.table_compare_manager.on_table_type_filter_changed()

    def _apply_table_filter(self):
        """应用筛选"""
        self.table_compare_manager.apply_table_filter()

    def _update_filter_nav_buttons(self):
        """更新导航按钮"""
        self.table_compare_manager.update_filter_nav_buttons()

    def prev_filtered_page(self):
        """上一页"""
        self.table_compare_manager.prev_filtered_page()

    def next_filtered_page(self):
        """下一页"""
        self.table_compare_manager.next_filtered_page()

    def update_preview_display(self):
        """更新预览显示"""
        self.table_compare_manager.update_preview_display()

    def _display_table_data(self, table):
        """显示表格数据"""
        self.table_compare_manager.display_table_data(table)

    def prev_preview_page(self):
        """上一页预览（委托给table_compare_manager）"""
        self.table_compare_manager.prev_preview_page()

    def next_preview_page(self):
        """下一页预览（委托给table_compare_manager）"""
        self.table_compare_manager.next_preview_page()

    def first_preview_page(self):
        """第一页预览（委托给table_compare_manager）"""
        self.table_compare_manager.first_preview_page()

    def last_preview_page(self):
        """最后一页预览（委托给table_compare_manager）"""
        self.table_compare_manager.last_preview_page()

    def goto_preview_page(self):
        """跳转到指定页"""
        if hasattr(self, 'goto_page_input') and self.goto_page_input.isEnabled():
            page = self.goto_page_input.value()
            self.table_compare_manager.goto_preview_page(page)

    def on_goto_page_changed(self, value):
        """跳转页码输入变化时的处理"""
        pass  # 可以添加实时预览功能

    def show_loading(self, message="加载中"):
        """显示加载状态（委托给preview_manager）"""
        if self.preview_manager:
            self.preview_manager.show_loading(message)


    def hide_loading(self):
        """隐藏加载状态（委托给preview_manager）"""
        if self.preview_manager:
            self.preview_manager.hide_loading()


    def _update_loading_animation(self):
        """更新加载动画（委托给preview_manager）"""
        if self.preview_manager:
            self.preview_manager._update_loading_animation()


    def show_table_context_menu(self, position):
        """显示表格右键菜单"""
        self.table_compare_manager.show_table_context_menu(position)

    def insert_row_above(self):
        """上方插入行"""
        self.table_compare_manager.insert_row_above()

    def insert_row_below(self):
        """下方插入行"""
        self.table_compare_manager.insert_row_below()

    def insert_col_left(self):
        """左侧插入列"""
        self.table_compare_manager.insert_col_left()

    def insert_col_right(self):
        """右侧插入列"""
        self.table_compare_manager.insert_col_right()

    def _delete_row(self):
        """删除行"""
        self.table_compare_manager._delete_row()

    def _delete_column(self):
        """删除列"""
        self.table_compare_manager._delete_column()

    def eventFilter(self, obj, event):
        """事件过滤器"""
        return self.table_compare_manager.eventFilter(obj, event)

    def save_current_table_state(self):
        """保存当前表格状态"""
        self.table_compare_manager.save_current_table_state()

    def undo_change(self):
        """撤销"""
        self.table_compare_manager.undo_change()

    def redo_change(self):
        """重做"""
        self.table_compare_manager.redo_change()

    def _restore_table_data(self, data):
        """恢复表格数据"""
        self.table_compare_manager._restore_table_data(data)

    def on_cell_changed(self, item):
        """单元格改变"""
        self.table_compare_manager.on_cell_changed(item)

    def copy_from_table(self):
        """复制"""
        self.table_compare_manager.copy_from_table()

    def cut_from_table(self):
        """剪切"""
        self.table_compare_manager.cut_from_table()

    def paste_to_table(self):
        """粘贴"""
        self.table_compare_manager.paste_to_table()

    def insert_row(self):
        """插入行"""
        self.table_compare_manager.insert_row()

    def delete_row(self):
        """删除行"""
        self.table_compare_manager.delete_row()

    def insert_column(self):
        """插入列"""
        self.table_compare_manager.insert_column()

    def delete_column(self):
        """删除列"""
        self.table_compare_manager.delete_column()

    def calculate_selected(self):
        """计算选中区域"""
        self.table_compare_manager.calculate_selected()

    def filter_table(self):
        """筛选表格内容"""
        self.table_compare_manager.filter_table()

    def clear_filter(self):
        """清除筛选"""
        self.table_compare_manager.clear_filter()

    def on_selection_changed(self):
        """选择改变"""
        self.table_compare_manager.on_selection_changed()

    # ==================== 页面状态管理 ====================

    def save_page_status(self):
        """保存页面状态"""
        self.table_compare_manager.save_page_status()

    def toggle_current_page_type(self):
        """切换页面类型"""
        self.table_compare_manager.toggle_current_page_type()

    def reparse_current_page(self):
        """重新解析当前页面"""
        self.status_bar.showMessage("AI重识别功能开发中...")

    def export_failed_pages_list(self):
        """导出失败页面列表（委托给export_manager）"""
        if self.export_manager:
            self.export_manager.export_failed_pages_list()

    # ==================== 历史记录 ====================

    def refresh_history_list(self):
        """刷新历史记录列表（委托给history_manager）"""
        if self.history_manager:
            self.history_manager.refresh_history_list()


    def filter_history_list(self):
        """根据筛选条件过滤历史记录"""
        if self.history_manager:
            self.history_manager.filter_history_list()


    def clear_pdf_history(self):
        """清空历史记录"""
        if self.history_manager:
            self.history_manager.clear_pdf_history()


    def add_to_history(self, filename, status, total_pages, success_count, file_path):
        """添加历史记录（委托给history_manager）"""
        if self.history_manager:
            self.history_manager.add_to_history(filename, status, total_pages, success_count, file_path)

    def on_history_cell_clicked(self, row, column):
        """单击单元格"""
        pass

    def on_preview_button_clicked(self, row):
        """预览按钮点击（委托给history_manager）"""
        if self.history_manager:
            self.history_manager.on_preview_button_clicked(row)

    def _update_loading_time(self):
        """更新加载时间（委托给history_manager）"""
        if self.history_manager:
            self.history_manager._update_loading_time()

    def _on_history_double_click(self, item):
        """历史项双击"""
        pass  # 已通过表格按钮操作，不再使用此逻辑

    def _on_history_click(self, item):
        """历史项单击"""
        pass

    # ==================== 缓存管理 ====================

    def refresh_cache_list(self):
        """刷新缓存列表（委托给file_manager）"""
        if self.file_manager:
            self.file_manager.refresh_cache_list()

    def _format_size(self, size):
        """格式化文件大小（委托给file_manager）"""
        if self.file_manager:
            return self.file_manager._format_size(size)
        return f"{size} B"

    def delete_selected_cache(self):
        """删除选中缓存（委托给file_manager）"""
        if self.file_manager:
            self.file_manager.delete_selected_cache()

    def delete_all_cache(self):
        """清空所有缓存（委托给file_manager）"""
        if self.file_manager:
            self.file_manager.delete_all_cache()

    # ==================== 导出 ====================

    def export_to_excel(self):
        """导出到Excel（委托给export_manager）"""
        if self.export_manager:
            self.export_manager.export_to_excel()

    def batch_export_tables(self):
        """批量导出（委托给export_manager）"""
        if self.export_manager:
            self.export_manager.batch_export_tables()

    def _do_export(self, table_pages, tables, output_path):
        """执行导出（委托给export_manager）"""
        if self.export_manager:
            self.export_manager._do_export(table_pages, tables, output_path)

    # ==================== 设置 ====================

    def toggle_key_visibility(self):
        """切换Key可见性（委托给settings_manager）"""
        if self.settings_manager:
            self.settings_manager.toggle_key_visibility()

    def test_api(self):
        """测试API连接（委托给settings_manager）"""
        if self.settings_manager:
            self.settings_manager.test_api()

    def save_settings(self):
        """保存设置（委托给settings_manager）"""
        if self.settings_manager:
            self.settings_manager.save_settings()
