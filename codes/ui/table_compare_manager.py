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
    QTextBrowser, QCheckBox
)
from PyQt5.QtGui import QKeySequence
from PyQt5.QtCore import Qt, QEvent, QObject, QTimer, QThread, pyqtSignal

from codes.pdf_extractor import (
    ZoomableTableWidget, save_mid_data
)


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
        # 锁定表格状态
        self.table_locked = False
        self.locked_table_data = None  # 锁定时保持的表格数据
        self.has_unsaved_changes = False
        
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
        
        # 原始数据勾选框
        self.show_original_checkbox = QCheckBox("📋 原始数据")
        self.show_original_checkbox.setToolTip("勾选查看清洗前的原始数据，不勾选查看清洗后的数据")
        self.show_original_checkbox.stateChanged.connect(self.on_show_original_changed)
        filter_layout.addWidget(self.show_original_checkbox)
        
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
        
        # 上下文文本展示区（表格上方描述文字）
        self.context_text_browser = QTextBrowser()
        self.context_text_browser.setReadOnly(True)
        self.context_text_browser.setFixedHeight(50)
        self.context_text_browser.setPlaceholderText("选中表格后显示标题和上下文...")
        self.context_text_browser.setStyleSheet("QTextBrowser { border:1px solid #BDC3C7; border-radius:4px; background-color:#FEF9E7; color:#7D6608; font-size:12px; padding:4px; }")
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

        # 删除空格按钮
        self.remove_spaces_btn = QPushButton("🧹 删除空格")
        self.remove_spaces_btn.setToolTip("清除当前表格中所有单元格的前后及中间空格，并左对齐")
        self.remove_spaces_btn.clicked.connect(self.remove_spaces)
        btn_layout.addWidget(self.remove_spaces_btn)

        btn_layout.addSpacing(10)

        # 批量插入按钮
        self.batch_insert_btn = QPushButton("📦 批量插入")
        self.batch_insert_btn.setToolTip("一次性插入多行或多列")
        self.batch_insert_btn.clicked.connect(self.batch_insert)
        btn_layout.addWidget(self.batch_insert_btn)
        
        # 统计按钮
        self.calc_sum_btn = QPushButton("📊 计算选中区域")
        self.calc_sum_btn.setToolTip("计算选中单元格的总和、平均值、数量")
        self.calc_sum_btn.clicked.connect(self.calculate_selected)
        btn_layout.addWidget(self.calc_sum_btn)
        
        btn_layout.addSpacing(10)
        
        # AI命名按钮
        self.ai_name_btn = QPushButton("🤖 AI命名")
        self.ai_name_btn.setToolTip("使用 DeepSeek 为表格生成规范的标题和摘要")
        self.ai_name_btn.setFocusPolicy(Qt.NoFocus)
        self.ai_name_btn.clicked.connect(self.on_ai_name_clicked)
        self.ai_name_btn.setStyleSheet("QPushButton { background-color:#8E44AD; color:white; padding:2px 12px; border-radius:4px; font-weight:bold; } QPushButton:hover { background-color:#7D3C98; } QPushButton:disabled { background-color:#BDC3C7; color:#ecf0f1; }")
        btn_layout.addWidget(self.ai_name_btn)
        
        btn_layout.addSpacing(10)
        
        # 撤销/重做按钮
        self.undo_btn = QPushButton("↩️ 撤销")
        self.undo_btn.setToolTip("撤销上一步操作 (Ctrl+Z)")
        self.undo_btn.clicked.connect(self.undo_change)
        self.undo_btn.setMaximumWidth(60)
        btn_layout.addWidget(self.undo_btn)
        
        self.redo_btn = QPushButton("↪️ 重做")
        self.redo_btn.setToolTip("重做操作 (Ctrl+Y)")
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
        self.table_splitter.setChildrenCollapsible(False)
        
        # 设置splitter分割线宽度和初始大小
        self.table_splitter.setHandleWidth(8)
        self.table_splitter.setSizes([200, 500])
        
        # 创建表格控件
        self.table_widget = ZoomableTableWidget()
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
        
        tables[table_idx]['data'] = data
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
        tables[self._last_displayed_table_idx]['data'] = data
    
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

    def on_show_original_changed(self):
        """原始数据/清洗数据切换"""
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
        insert_before = menu.addAction("在上方插入新表")
        insert_after = menu.addAction("在下方插入新表")
        menu.addSeparator()
        delete_action = menu.addAction("🗑️ 删除此表")
        action = menu.exec_(self.table_list_widget.mapToGlobal(pos))
        if action == insert_before:
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
            status_icon = "✅" if table.get('parse_status') == 'success' else "❌"
            ext = table.get('extractor', '')
            if ext == "manual":
                ext_tag = "M"
            elif ext.startswith("docx"):
                ext_tag = "D"
            else:
                ext_tag = "V2"
            # 标题优先级：llm_title > title > data第一格 > context首行
            title = table.get('llm_title', '')
            if not title:
                title = table.get('title', '')
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
            llm_mark = "✨" if table.get('llm_title') else ""
            item_text = f"{status_icon} P{page}_{page_seq[page]} [{ext_tag}]{llm_mark}{title_str}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, idx)  # 保存原始索引
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
        print(f"[DEBUG] 当前页: {table['page']}, 类型: {table.get('type')}, 状态: {table.get('parse_status')}")
        print(f"[DEBUG] table data 前3行: {table.get('data', [])[:3] if table.get('data') else '无数据'}")
        
        # 更新表格类型标签
        # 更新上下文文本展示区
        context_text = table.get('context_text', '')
        llm_title = table.get('llm_title', '')
        llm_summary = table.get('llm_summary', '')
        if hasattr(self, 'context_text_browser') and self.context_text_browser:
            if llm_title or context_text:
                text = ""
                if llm_title: text = f"📌 {llm_title}"
                if llm_summary: text += f"\n{llm_summary}" if text else llm_summary
                if context_text: text += f"\n{context_text}" if text else context_text
                self.context_text_browser.setPlainText(text)
            else:
                self.context_text_browser.setPlainText("（无上下文描述文字）")

        is_success = table.get('parse_status') == 'success'
        status_text = "✅ 表格" if is_success else "❌ 非表格"
        self.table_type_label.setText(f"状态: {status_text}")
        
        # 根据状态设置标签颜色
        if is_success:
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
                        print(f"[WARN] page {table['page']} 超出 preview_images 范围")
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
    
    def display_table_data(self, table):
        """显示表格数据"""
        if not hasattr(self, 'table_widget'):
            return
        
        self.table_widget.blockSignals(True)
        self.table_widget.clear()
        
        # 勾选"原始数据"时显示 original_data，否则显示清洗后的 data
        show_original = self.show_original_checkbox.isChecked()
        data = table.get('original_data') if show_original and table.get('original_data') else table.get('data', [])
        parse_type = table.get('type', '')
        parse_message = table.get('parse_message', '')
        
        if not data:
            # 图片类页面虽然没有数据，但也正常显示
            # 根据类型设置更友好的提示信息
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
            self.stats_label.setText(hint_text)
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
        
        # 更新统计标签（注明数据来源和锁定状态）
        tip = "选中单元格查看统计信息"
        if self.table_locked:
            tip = "🔒 已锁定 | " + tip
        if show_original:
            tip = "📋 原始数据 | " + tip
        self.stats_label.setText(tip)
    
    def _update_pdf_preview(self, page_num):
        """仅更新 PDF 预览到指定页码（不改变表格内容）"""
        try:
            if hasattr(self.main_window, 'pdf_preview_widget') and self.main_window.pdf_preview_widget:
                if hasattr(self.main_window, 'preview_images') and self.main_window.preview_images:
                    if page_num <= len(self.main_window.preview_images):
                        img_path = self.main_window.preview_images[page_num - 1]
                        if img_path and os.path.exists(img_path):
                            self.main_window.pdf_preview_widget.set_preview(
                                img_path, page_num - 1,
                                pdf_path=self.main_window.current_file
                            )
        except Exception as e:
            pass

    def prev_preview_page(self):
        """上一页预览"""
        self._sync_ui_to_processed_results()
        current = self.table_list_widget.currentRow()
        if current > 0:
            self.table_list_widget.setCurrentRow(current - 1)
            self.update_preview_display()

    def next_preview_page(self):
        """下一页预览"""
        self._sync_ui_to_processed_results()
        current = self.table_list_widget.currentRow()
        if current < self.table_list_widget.count() - 1:
            self.table_list_widget.setCurrentRow(current + 1)
            self.update_preview_display()

    def first_preview_page(self):
        """第一页预览"""
        self._sync_ui_to_processed_results()
        if self.table_list_widget.count() > 0:
            self.table_list_widget.setCurrentRow(0)
            self.update_preview_display()

    def last_preview_page(self):
        """最后一页预览"""
        self._sync_ui_to_processed_results()
        if self.table_list_widget.count() > 0:
            self.table_list_widget.setCurrentRow(self.table_list_widget.count() - 1)
            self.update_preview_display()

    def goto_preview_page(self, page_num):
        """跳转到指定页（切页前先保存当前编辑）"""
        self._sync_ui_to_processed_results()
        if self.table_list_widget.count() > 0 and page_num >= 1:
            target_row = min(page_num - 1, self.table_list_widget.count() - 1)
            self.table_list_widget.setCurrentRow(target_row)
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

        menu.addAction("📋 复制").triggered.connect(self.copy_from_table)
        menu.addAction("📄 粘贴").triggered.connect(self.paste_to_table)
        menu.addAction("✂️ 剪切").triggered.connect(self.cut_from_table)
        menu.addAction("⬇️ 向下填充").triggered.connect(self.fill_down_from_table)
        
        menu.exec_(self.table_widget.mapToGlobal(position))
    
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
        """删除当前表格中所有单元格的前后及中间空格，并左对齐"""
        self.save_current_table_state()
        table = self.table_widget
        removed_count = 0
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item is None:
                    continue
                text = item.text()
                if not text:
                    continue
                # 去除前后空格，并将中间连续空白字符合并为单个空格
                import re
                cleaned = re.sub(r'\s+', ' ', text.strip())
                if cleaned != text:
                    removed_count += 1
                item.setText(cleaned)
                # 设置左对齐
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.resizeColumnsToContents()
        QMessageBox.information(
            self.main_window, "删除空格",
            f"已处理完成，共修改 {removed_count} 个单元格。"
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
        """删除所有选中的行"""
        selected_rows = set(item.row() for item in self.table_widget.selectedItems())
        if selected_rows:
            self.save_current_table_state()
            # 从大到小排序，避免删除后索引变化
            for row in sorted(selected_rows, reverse=True):
                self.table_widget.removeRow(row)
    
    def delete_selected_rows(self):
        """删除所有选中的行"""
        selected_rows = set(item.row() for item in self.table_widget.selectedItems())
        if selected_rows:
            self.save_current_table_state()
            # 从大到小排序，避免删除后索引变化
            for row in sorted(selected_rows, reverse=True):
                self.table_widget.removeRow(row)

    def delete_selected_columns(self):
        """删除所有选中的列"""
        selected_cols = set(item.column() for item in self.table_widget.selectedItems())
        if selected_cols:
            self.save_current_table_state()
            # 从大到小排序，避免删除后索引变化
            for col in sorted(selected_cols, reverse=True):
                self.table_widget.removeColumn(col)
    
    def calculate_selected(self):
        """计算选中区域"""
        selection = self.table_widget.selectedRanges()
        if not selection:
            self.stats_label.setText("请先选择单元格区域")
            return
        
        total_sum = 0
        count = 0
        values = []
        
        for range_ in selection:
            for row in range(range_.topRow(), range_.bottomRow() + 1):
                for col in range(range_.leftColumn(), range_.rightColumn() + 1):
                    item = self.table_widget.item(row, col)
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
        tables = self.main_window.processed_results.get('tables', [])
        if not tables:
            QMessageBox.warning(self.main_window, "无数据", "请先处理PDF文件，提取表格后再使用AI命名功能。")
            return
        candidate = [(i, t) for i, t in enumerate(tables) if t.get('data') or t.get('context_text')]
        if not candidate:
            QMessageBox.information(self.main_window, "无表格", "没有找到可命名的表格数据。")
            return
        reply = QMessageBox.question(self.main_window, "AI 命名确认",
            f"将为 {len(candidate)} 个表格调用 DeepSeek 生成名称。\n约需 {len(candidate)*2} 秒。是否继续？",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.No:
            self._start_ai_naming(candidate)

    def _start_ai_naming(self, candidate):
        from codes.pdf_extractor import load_config
        self.ai_name_btn.setEnabled(False)
        self.ai_name_btn.setText("命名中...")
        config = load_config()
        if not config.get("deepseek_api_key"):
            QMessageBox.warning(self.main_window, "未配置", "请先在「配置」页面填写 DeepSeek API Key。")
            self.ai_name_btn.setEnabled(True)
            self.ai_name_btn.setText("🤖 AI命名")
            return
        worker_tables = [{"index": i, "context_text": t.get("context_text",""), "data": t.get("data",[])} for i, t in candidate]
        self._ai_worker = AINameWorker(worker_tables, config)
        self._ai_worker.progress.connect(lambda c, t, m: self.main_window.status_bar.showMessage(f"🤖 AI命名: {c}/{t} - {m}"))
        self._ai_worker.finished.connect(self._on_ai_name_finished)
        self._ai_worker.start()
        self.main_window.status_bar.showMessage(f"🤖 AI命名: 0/{len(worker_tables)}...")

    def _on_ai_name_finished(self, results):
        tables = self.main_window.processed_results.get('tables', [])
        updated = 0
        for r in results:
            idx = r["index"]
            if idx < len(tables) and r.get("title"):
                tables[idx]["llm_title"] = r["title"]
                tables[idx]["llm_summary"] = r.get("summary", "")
                updated += 1
        self.ai_name_btn.setEnabled(True)
        self.ai_name_btn.setText("🤖 AI命名")
        self.main_window.status_bar.showMessage(f"✅ AI命名: 成功{updated}个" if updated else "⚠️ AI命名: 无结果")
        if updated:
            self.apply_table_filter()
            self.has_unsaved_changes = True
            self._schedule_auto_save()
        self._ai_worker = None


class AINameWorker(QThread):
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
        final = []
        for i, r in enumerate(llm_results):
            final.append({
                "index": self.tables[i]["index"],
                "title": r.get("title", ""),
                "summary": r.get("summary", ""),
                "error": r.get("error", ""),
            })
        self.finished.emit(final)
