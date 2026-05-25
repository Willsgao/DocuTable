# -*- coding: utf-8 -*-
"""
AI 纠错审核 Tab — Layer 4 人工审核界面（独立 Tab 页）

布局：
┌───────────────────────────────────────────────────────────┐
│  🔍 AI 纠错审核    [全部接受] [全部拒绝] [仅高置信度] [✅应用]│
├────────────┬────────────────┬─────────────────────────────┤
│ 📋 变更列表  │ ▲ 原始提取数据    │ ✅ AI 修正后               │
│            │                │                            │
│ ✅ 表 #1   │ ┌────┬────┬───┐│ ┌────┬────┬───┐            │
│  P2[D]     │ │资产│2024│...││ │资产│2024│... │            │
│  已自动修复  │ ├────┼────┼───┤│ ├────┼────┼───┤            │
│            │ │... │... │...││ │... │... │... │            │
│ ⭐ 表 #3   │ └────┴────┴───┘│ └────┴────┴───┘            │
│  P5[D]     │                │                            │
│  LLM已分析  │ 📊 差异行黄色高亮  │ 📊 差异行黄色高亮          │
│            │                │                            │
│ ⚠️ 表 #7   │ ┌──────────────────────────────────────────┐│
│  P8[V2]    │ │ 变更摘要: 合并表头, 对齐列偏移             ││
│  待确认     │ │ 置信度: ⬤ 高 | 层级: 2层(✅验证)          ││
│            │ │ 子Tab: 📊数据对比 🌳层级结构 ⚠️问题详情    ││
│            │ │ [接受此表] [拒绝此表]                     ││
│            │ └──────────────────────────────────────────┘│
├────────────┴────────────────┬─────────────────────────────┤
│ ⬤高(8) 🟡中(3) 🔴需人工(2)  │ 进度: 8/13 已确认           │
└──────────────────────────────────────────────────────────┘
"""

import os

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QSplitter, QMessageBox, QTabWidget, QTextBrowser,
    QHeaderView, QAbstractItemView, QSpacerItem, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QBrush, QFont

from codes.pdf_extractor.widgets import PDFPreviewWidget, ZoomableScrollArea


# ============================================================
# 常量
# ============================================================

STATUS_ICONS = {
    "clean":         "✅",
    "auto_fixed":    "✅",
    "llm_analyzed":  "⭐",
    "needs_review":  "⚠️",
    "unresolvable":  "🔴",
}

CONFIDENCE_LABELS = {
    "high":           "⬤ 高",
    "medium":         "🟡 中",
    "low":            "🟠 低",
    "unresolvable":   "🔴 无法判断",
}


# ============================================================
# 标签颜色方案
# ============================================================

TAG_COLORS = {
    "auto_fixed":    ("#D5F5E3", "#1E8449"),   # 绿色底 + 绿色字
    "needs_review":  ("#FCF3CF", "#B7950B"),   # 黄色底 + 深黄字
    "llm_analyzed":  ("#D6EAF8", "#2471A3"),   # 蓝色底 + 蓝色字
    "verified":      ("#D5F5E3", "#17A589"),   # 浅绿底 + 青色字
    "meta":          ("#F2F3F4", "#7F8C8D"),   # 灰色底 + 灰色字
}


# ============================================================
# AI 纠错 Tab 组件
# ============================================================

class AICorrectionTab(QWidget):
    """AI 纠错审核 Tab — 替代弹窗，支持非阻塞对比"""

    # 信号：用户点击"应用修改"时发射
    apply_requested = pyqtSignal(list, dict)
    # apply_requested.emit(accepted_results, confirm_status)

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window

        self.correction_results = []
        self.tables = []
        self._confirm_status = {}
        self._current_table_index = -1

        self._init_ui()
        self._connect_signals()

    # ==================== UI 初始化 ====================

    def _init_ui(self):
        """初始化 UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ===== 顶部工具栏 =====
        top_bar = QHBoxLayout()

        title_label = QLabel("🔍 AI 纠错审核")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #2C3E50;")
        top_bar.addWidget(title_label)
        top_bar.addStretch(1)

        self.accept_all_btn = QPushButton("✅ 全部接受")
        self.accept_all_btn.setToolTip("接受所有修正")
        self.accept_all_btn.setStyleSheet(self._green_btn_style())
        top_bar.addWidget(self.accept_all_btn)

        self.reject_all_btn = QPushButton("❌ 全部拒绝")
        self.reject_all_btn.setToolTip("拒绝所有修正，保留原始数据")
        self.reject_all_btn.setStyleSheet(self._red_btn_style())
        top_bar.addWidget(self.reject_all_btn)

        self.accept_high_confidence_btn = QPushButton("⭐ 仅接受高置信度")
        self.accept_high_confidence_btn.setToolTip("只接受 high 置信度的修正")
        self.accept_high_confidence_btn.setStyleSheet(self._blue_btn_style())
        top_bar.addWidget(self.accept_high_confidence_btn)

        top_bar.addSpacing(15)

        self.apply_btn = QPushButton("✅ 应用修改")
        self.apply_btn.setToolTip("将所有已接受的修改应用到表格数据")
        self.apply_btn.setStyleSheet("""
            QPushButton { background-color: #8E44AD; color: white; padding: 6px 20px;
                          border-radius: 4px; font-weight: bold; font-size: 13px; }
            QPushButton:hover { background-color: #7D3C98; }
        """)
        top_bar.addWidget(self.apply_btn)

        main_layout.addLayout(top_bar)

        # ===== 主分割器：PDF预览 + 列表 + 原始 + 修正 =====
        self.main_splitter = QSplitter(Qt.Horizontal)

        # ---- 最左侧：PDF 预览 ----
        self.pdf_scroll_area = ZoomableScrollArea()
        self.pdf_scroll_area.setMinimumWidth(220)
        self.pdf_scroll_area.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")
        self.pdf_preview_widget = PDFPreviewWidget()
        self.pdf_scroll_area.setWidget(self.pdf_preview_widget)
        self.pdf_scroll_area.setWidgetResizable(False)
        self.pdf_scroll_area.zoomChanged.connect(
            lambda f: self.pdf_preview_widget.set_zoom(f)
        )
        self.main_splitter.addWidget(self.pdf_scroll_area)

        # ---- 左侧：变更列表 ----
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_header = QLabel("📋 变更列表")
        left_header.setStyleSheet("font-weight: bold; padding: 4px 8px; color: #2C3E50;")
        left_layout.addWidget(left_header)

        self.table_list = QListWidget()
        self.table_list.setMinimumWidth(220)
        self.table_list.setStyleSheet("""
            QListWidget { border: 1px solid #ddd; border-radius: 4px; }
            QListWidget::item { padding: 6px 8px; border-bottom: 1px solid #eee; }
            QListWidget::item:selected { background-color: #D4E6F1; }
        """)
        left_layout.addWidget(self.table_list)

        self.main_splitter.addWidget(left_panel)

        # ---- 中间：原始数据 ----
        orig_panel = QWidget()
        orig_layout = QVBoxLayout(orig_panel)
        orig_layout.setContentsMargins(0, 0, 4, 0)

        orig_header = QLabel("▲ 原始提取数据")
        orig_header.setStyleSheet("font-weight: bold; padding: 4px 8px; color: #E74C3C;")
        orig_layout.addWidget(orig_header)

        self.original_table = QTableWidget()
        self.original_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.original_table.setAlternatingRowColors(True)
        self.original_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        orig_layout.addWidget(self.original_table)

        self.main_splitter.addWidget(orig_panel)

        # ---- 右侧：AI 修正后 + 详情 ----
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)

        corrected_header = QLabel("✅ AI 修正后")
        corrected_header.setStyleSheet("font-weight: bold; padding: 4px 8px; color: #27AE60;")
        right_layout.addWidget(corrected_header)

        self.corrected_table = QTableWidget()
        self.corrected_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.corrected_table.setAlternatingRowColors(True)
        self.corrected_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        right_layout.addWidget(self.corrected_table)

        # 同步滚动
        self.original_table.verticalScrollBar().valueChanged.connect(
            self.corrected_table.verticalScrollBar().setValue
        )
        self.corrected_table.verticalScrollBar().valueChanged.connect(
            self.original_table.verticalScrollBar().setValue
        )

        self.main_splitter.addWidget(right_panel)

        self.main_splitter.setSizes([250, 220, 360, 360])
        self.main_splitter.setChildrenCollapsible(False)

        main_layout.addWidget(self.main_splitter, 1)

        # ===== 底部：详情区 =====
        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 4, 0, 0)

        # 信息栏 + 操作按钮
        info_bar = QHBoxLayout()

        self.info_label = QLabel("选择左侧表格查看详情")
        self.info_label.setStyleSheet("""
            font-size: 13px; font-weight: bold; padding: 6px 10px;
            background-color: #EBF5FB; border-radius: 4px; color: #2C3E50;
        """)
        self.info_label.setWordWrap(True)
        info_bar.addWidget(self.info_label, 1)

        # 层级 / 问题切换
        self.show_hierarchy_btn = QPushButton("🌳 层级")
        self.show_hierarchy_btn.setCheckable(True)
        self.show_hierarchy_btn.setStyleSheet(self._toggle_btn_style())
        info_bar.addWidget(self.show_hierarchy_btn)

        self.show_issues_btn = QPushButton("⚠️ 问题")
        self.show_issues_btn.setCheckable(True)
        self.show_issues_btn.setStyleSheet(self._toggle_btn_style())
        info_bar.addWidget(self.show_issues_btn)

        info_bar.addSpacing(20)

        self.accept_table_btn = QPushButton("✅ 接受此表")
        self.accept_table_btn.setStyleSheet(self._green_btn_style())
        info_bar.addWidget(self.accept_table_btn)

        self.reject_table_btn = QPushButton("❌ 拒绝此表")
        self.reject_table_btn.setStyleSheet(self._red_btn_style())
        info_bar.addWidget(self.reject_table_btn)

        bottom_layout.addLayout(info_bar)

        # 摘要标签
        self.change_summary_label = QLabel("")
        self.change_summary_label.setWordWrap(True)
        self.change_summary_label.setStyleSheet("color: #555; padding: 2px 10px; font-size: 12px;")
        bottom_layout.addWidget(self.change_summary_label)

        # 标签栏（紧接摘要下方）
        self.tag_area = QWidget()
        self.tag_layout = QHBoxLayout(self.tag_area)
        self.tag_layout.setContentsMargins(4, 2, 4, 2)
        self.tag_layout.setSpacing(4)
        self.tag_layout.addStretch(1)  # 右对齐占位
        bottom_layout.addWidget(self.tag_area)

        # 详情浏览器
        self.detail_browser = QTextBrowser()
        self.detail_browser.setMaximumHeight(120)
        self.detail_browser.setStyleSheet("""
            QTextBrowser { border: 1px solid #ddd; border-radius: 4px;
                          padding: 6px; font-size: 12px; }
        """)
        bottom_layout.addWidget(self.detail_browser)

        main_layout.addWidget(bottom_panel)

        # ===== 底部状态栏 =====
        status_bar = QHBoxLayout()

        self.stats_label = QLabel("未加载数据")
        self.stats_label.setStyleSheet("""
            color: #555; padding: 4px 12px; background-color: #F8F9FA;
            border-top: 1px solid #ddd; border-radius: 2px;
        """)
        status_bar.addWidget(self.stats_label, 1)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #2980B9; font-weight: bold; padding: 4px 12px;")
        status_bar.addWidget(self.progress_label)

        main_layout.addLayout(status_bar)

    def _green_btn_style(self):
        return """
            QPushButton { background-color: #27AE60; color: white; padding: 5px 14px;
                          border-radius: 4px; font-weight: bold; font-size: 12px; }
            QPushButton:hover { background-color: #229954; }
            QPushButton:disabled { background-color: #BDC3C7; color: #ecf0f1; }
        """

    def _red_btn_style(self):
        return """
            QPushButton { background-color: #E74C3C; color: white; padding: 5px 14px;
                          border-radius: 4px; font-weight: bold; font-size: 12px; }
            QPushButton:hover { background-color: #CB4335; }
            QPushButton:disabled { background-color: #BDC3C7; color: #ecf0f1; }
        """

    def _blue_btn_style(self):
        return """
            QPushButton { background-color: #2980B9; color: white; padding: 5px 14px;
                          border-radius: 4px; font-weight: bold; font-size: 12px; }
            QPushButton:hover { background-color: #2471A3; }
            QPushButton:disabled { background-color: #BDC3C7; color: #ecf0f1; }
        """

    def _toggle_btn_style(self):
        return """
            QPushButton { padding: 4px 10px; border-radius: 4px; border: 1px solid #ccc;
                          font-size: 12px; }
            QPushButton:checked { background-color: #D4E6F1; border-color: #2980B9;
                                 font-weight: bold; }
        """

    # ==================== 信号连接 ====================

    def _connect_signals(self):
        self.table_list.currentRowChanged.connect(self._on_table_selected)
        self.accept_all_btn.clicked.connect(self._accept_all)
        self.reject_all_btn.clicked.connect(self._reject_all)
        self.accept_high_confidence_btn.clicked.connect(self._accept_high_confidence)
        self.accept_table_btn.clicked.connect(self._accept_current)
        self.reject_table_btn.clicked.connect(self._reject_current)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        self.show_hierarchy_btn.toggled.connect(lambda c: self._update_detail_view())
        self.show_issues_btn.toggled.connect(lambda c: self._update_detail_view())

    # ==================== 数据设置 ====================

    def set_results(self, correction_results, processed_results):
        """加载纠错结果（由 table_compare_manager 调用）"""
        self.correction_results = correction_results
        self.tables = processed_results.get("tables", []) if processed_results else []

        # 初始化确认状态
        self._confirm_status = {}
        for r in correction_results:
            if r.confidence == "high" and not r.unresolved_issues:
                self._confirm_status[r.table_index] = "accepted"
            else:
                self._confirm_status[r.table_index] = "pending"

        self._populate_list()
        self._update_status_bar()

        if self.table_list.count() > 0:
            self.table_list.setCurrentRow(0)

    def clear_results(self):
        """清空结果"""
        self.correction_results = []
        self.tables = []
        self._confirm_status = {}
        self._current_table_index = -1

        self.table_list.clear()
        self.original_table.clear()
        self.corrected_table.clear()
        self.detail_browser.clear()
        self.info_label.setText("未加载纠错数据")
        self.change_summary_label.setText("")

        # 清除标签
        if hasattr(self, 'tag_layout'):
            while self.tag_layout.count() > 0:
                item = self.tag_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            self.tag_layout.addStretch(1)

        self.stats_label.setText("未加载数据")
        self.progress_label.setText("")

        # 清除 PDF 预览
        if hasattr(self, 'pdf_preview_widget') and self.pdf_preview_widget:
            self.pdf_preview_widget.clear()

    # ==================== 列表填充 ====================

    def _populate_list(self):
        """填充左侧表格列表"""
        self.table_list.blockSignals(True)
        self.table_list.clear()

        # 计算每页的序号映射
        page_seq = {}

        for r in self.correction_results:
            status = self._confirm_status.get(r.table_index, "pending")
            icon = STATUS_ICONS.get(r.status, "❓")

            table = self.tables[r.table_index] if r.table_index < len(self.tables) else {}
            page = table.get("page", "?")
            ext = table.get("extractor", "?")
            ext_tag = {
                "docx_text": "T", "docx_table": "D", "v2_position_based": "V2",
                "manual": "M"
            }.get(ext, ext[:2] if ext else "?")
            page_seq[page] = page_seq.get(page, 0) + 1
            seq = page_seq[page]

            # 标题
            title = r.name_title or table.get("llm_title", "") or table.get("title", "")
            if not title:
                ctx = table.get("context_text", "")
                if ctx:
                    title = ctx.split('\n')[0].strip()[:15]
            if not title:
                data = table.get("data", [])
                for row in data:
                    for cell in row:
                        if cell and str(cell).strip():
                            title = str(cell).strip()[:10]
                            break
                    if title:
                        break

            # 构建显示文本
            conf_icon = CONFIDENCE_LABELS.get(r.confidence, r.confidence)
            lines = [f"{icon} P{page}_{seq} [{ext_tag}]"]
            if title:
                lines.append(f"  {title[:20]}")

            if r.status == "clean":
                lines.append(f"  无需处理")
            elif r.status == "auto_fixed":
                lines.append(f"  已自动修复  {conf_icon}")
            elif r.status == "llm_analyzed":
                lines.append(f"  LLM已分析  {conf_icon}")
            elif r.status == "needs_review":
                lines.append(f"  待确认  {conf_icon}")
            elif r.status == "unresolvable":
                lines.append(f"  需人工介入  {conf_icon}")

            # 精简标签行
            if r.error_tags:
                tag_texts = []
                for tag in r.error_tags[:6]:  # 最多显示 6 个标签
                    tag_texts.append(f"▫{tag['label']}")
                if len(r.error_tags) > 6:
                    tag_texts.append(f"…+{len(r.error_tags) - 6}")
                lines.append(f"  {' '.join(tag_texts)}")

            if status == "accepted":
                lines.append("  [已接受]")
            elif status == "rejected":
                lines.append("  [已拒绝]")

            item = QListWidgetItem("\n".join(lines))

            if status == "accepted":
                item.setForeground(QBrush(QColor("#27AE60")))
            elif status == "rejected":
                item.setForeground(QBrush(QColor("#E74C3C")))
            elif r.status in ("needs_review", "unresolvable"):
                item.setForeground(QBrush(QColor("#E67E22")))

            item.setData(Qt.UserRole, r.table_index)
            self.table_list.addItem(item)

        self.table_list.blockSignals(False)

    def _update_list_item_status(self, table_index):
        """更新列表中某项的状态"""
        for i in range(self.table_list.count()):
            item = self.table_list.item(i)
            if item.data(Qt.UserRole) == table_index:
                row = self.table_list.row(item)
                self._populate_list()
                if row < self.table_list.count():
                    self.table_list.setCurrentRow(row)
                break

    # ==================== 详情显示 ====================

    def _on_table_selected(self, row):
        if row < 0 or row >= self.table_list.count():
            return

        item = self.table_list.item(row)
        table_index = item.data(Qt.UserRole)
        if table_index is None or table_index >= len(self.correction_results):
            return

        self._current_table_index = table_index
        r = self.correction_results[table_index]

        # 计算 P{page}_{seq} 显示名
        table = self.tables[table_index] if table_index < len(self.tables) else {}
        page = table.get('page', '?')

        # 在修正结果中计算本页序号
        seq = 1
        for other_r in self.correction_results:
            if other_r.table_index == table_index:
                break
            ot = self.tables[other_r.table_index] if other_r.table_index < len(self.tables) else {}
            if ot.get('page') == page:
                seq += 1

        self.info_label.setText(
            f"P{page}_{seq} — "
            f"状态: {r.status} | 置信度: {CONFIDENCE_LABELS.get(r.confidence, r.confidence)}"
        )
        self.change_summary_label.setText(f"变更摘要: {r.changes_summary}")

        # 按钮状态
        btn_status = self._confirm_status.get(table_index, "pending")
        self.accept_table_btn.setEnabled(btn_status != "accepted")
        self.reject_table_btn.setEnabled(btn_status != "rejected")

        # 填充三列表格
        self._fill_tables(r, table_index)

        # 更新详情
        self._update_detail_view()

        # 更新标签
        self._refresh_tags(r)

        # 导航PDF预览
        self._navigate_pdf_to_table(table_index)

    def _navigate_pdf_to_table(self, table_index):
        """导航 PDF 预览到对应表格的页码"""
        mw = self.main_window
        if not mw:
            return
        if not hasattr(self, 'pdf_preview_widget') or not self.pdf_preview_widget:
            return

        table = self.tables[table_index] if table_index < len(self.tables) else {}
        page = table.get("page", None)
        if page is None:
            return

        preview_images = getattr(mw, 'preview_images', None)
        current_file = getattr(mw, 'current_file', None)
        if not preview_images or page > len(preview_images):
            return

        img_path = preview_images[page - 1]
        if img_path and os.path.exists(img_path):
            self.pdf_preview_widget.set_preview(
                img_path, page - 1,
                pdf_path=current_file
            )

    def _fill_tables(self, result, table_index):
        """填充原始/修正对比表格"""
        table = self.tables[table_index] if table_index < len(self.tables) else {}
        original_data = table.get("data", [])
        corrected_data = result.corrected_data or original_data

        # 填充原始表
        self._populate_table_widget(self.original_table, original_data)

        # 填充修正表
        self._populate_table_widget(self.corrected_table, corrected_data)

        # 单元格级差异高亮
        if result.corrected_data and result.corrected_data != original_data:
            self._highlight_diff_cells(self.original_table, original_data,
                                       result.corrected_data)
            self._highlight_diff_cells(self.corrected_table, result.corrected_data,
                                       original_data)

    def _populate_table_widget(self, tw, data):
        tw.blockSignals(True)
        tw.clear()
        if not data:
            tw.setRowCount(1)
            tw.setColumnCount(1)
            item = QTableWidgetItem("（无数据）")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            tw.setItem(0, 0, item)
            tw.blockSignals(False)
            return

        rows, cols = len(data), max((len(r) for r in data), default=0)
        tw.setRowCount(rows)
        tw.setColumnCount(cols)
        for i, row in enumerate(data):
            for j in range(cols):
                cell_val = str(row[j]) if j < len(row) and row[j] else ""
                item = QTableWidgetItem(cell_val)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                tw.setItem(i, j, item)
        tw.resizeColumnsToContents()
        tw.blockSignals(False)

    def _highlight_diff_cells(self, tw, my_data, other_data):
        """单元格级差异高亮：只标黄色真正不同的格，行内有异时行首轻标"""
        yellow = QBrush(QColor("#FCF3CF"))
        for i in range(max(len(my_data), len(other_data))):
            my_row = my_data[i] if i < len(my_data) else []
            other_row = other_data[i] if i < len(other_data) else []
            cols = max(len(my_row), len(other_row))

            if my_row != other_row:
                # 行首格轻橙标记，表示本行有差异
                for j in range(cols):
                    my_val = str(my_row[j]) if j < len(my_row) and my_row[j] is not None else ""
                    other_val = str(other_row[j]) if j < len(other_row) and other_row[j] is not None else ""
                    if my_val.strip() != other_val.strip():
                        item = tw.item(i, j)
                        if item:
                            item.setBackground(yellow)

    def _refresh_tags(self, r):
        """根据 CorrectionResult.error_tags 渲染底部标签栏"""
        # 清除旧标签
        while self.tag_layout.count() > 0:
            item = self.tag_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for tag in r.error_tags:
            cat = tag.get("category", "needs_review")
            bg, fg = TAG_COLORS.get(cat, ("#FCF3CF", "#B7950B"))
            label = QLabel(f" {tag['label']} ")
            label.setToolTip(tag.get("detail", ""))
            label.setStyleSheet(f"""
                QLabel {{
                    background-color: {bg}; color: {fg};
                    border-radius: 3px; padding: 1px 6px; font-size: 11px;
                    font-weight: bold;
                }}
            """)
            self.tag_layout.addWidget(label)

        # 末尾伸缩
        self.tag_layout.addStretch(1)

    def _update_detail_view(self):
        """更新底部详情视图"""
        idx = self._current_table_index
        if idx < 0 or idx >= len(self.correction_results):
            self.detail_browser.clear()
            return

        r = self.correction_results[idx]
        parts = []

        # 层级视图
        if self.show_hierarchy_btn.isChecked() and r.hierarchy:
            parts.append(self._build_hierarchy_html(r))
        elif not self.show_issues_btn.isChecked():
            # 默认显示层级（如果有）
            if r.hierarchy:
                parts.append(self._build_hierarchy_html(r))

        # 问题视图
        if self.show_issues_btn.isChecked():
            parts.append(self._build_issues_html(r))
        elif not self.show_hierarchy_btn.isChecked():
            if r.unresolved_issues:
                parts.append(self._build_issues_html(r))

        if not parts:
            self.detail_browser.setHtml(
                "<p style='color: #27AE60; padding: 10px;'>✅ 无异常或层级信息</p>"
            )
        else:
            self.detail_browser.setHtml("\n".join(parts))

    def _build_hierarchy_html(self, result):
        """构建层级 HTML"""
        verified = "✅ 通过" if result.hierarchy_verified else ("⚠️ 存疑" if result.hierarchy else "")
        rows = [f"<h4>🌳 层级结构 ({len(result.hierarchy)}行标记, 验证: {verified})</h4>"]
        rows.append("<table border='1' cellpadding='4' cellspacing='0' "
                     "style='border-collapse:collapse; width:100%; font-size:12px;'>")
        rows.append("<tr style='background:#2980B9; color:white;'>"
                     "<th>行</th><th>层级</th><th>类型</th><th>标签</th><th>合计行</th></tr>")

        type_labels = {"header": "表头", "category": "分类", "subtotal": "小计",
                        "total": "总计", "data": "数据"}

        for h in result.hierarchy:
            level = h.get("level", 0)
            indent = "&nbsp;&nbsp;" * level
            t = h.get("type", "data")
            row_style = ""
            if t in ("subtotal", "total"):
                row_style = "background:#D5F5E3; font-weight:bold;"
            total_of = ", ".join(str(x) for x in h.get("total_of_rows", [])) if h.get("total_of_rows") else "—"
            rows.append(
                f"<tr style='{row_style}'>"
                f"<td>{h.get('row', '?')}</td>"
                f"<td>{indent}L{level}</td>"
                f"<td>{type_labels.get(t, t)}</td>"
                f"<td>{h.get('label', '')}</td>"
                f"<td>{total_of}</td>"
                f"</tr>"
            )
        rows.append("</table>")
        return "\n".join(rows)

    def _build_issues_html(self, result):
        """构建问题详情 HTML"""
        parts = []

        # 区域
        region_items = []
        if not result.region_is_complete:
            region_items.append("⚠️ 表格区域不完整")
        if result.region_merge_prev is not None:
            region_items.append(f"↔️ 应向前合并到表 #{result.region_merge_prev}")
        if result.region_merge_next is not None:
            region_items.append(f"↔️ 应与后表 #{result.region_merge_next} 合并")
        if result.region_split_rows:
            region_items.append(f"✂️ 建议在行 {result.region_split_rows} 处拆分")
        if region_items:
            parts.append("<h4>📐 区域判断</h4><ul>" +
                         "".join(f"<li>{x}</li>" for x in region_items) + "</ul>")

        # 修正
        if result.applied_corrections:
            items = []
            for c in result.applied_corrections:
                items.append(
                    f"<li>行{c.get('row','?')} 列{c.get('col','?')}: "
                    f"{c.get('action','修改')} → {c.get('new_value','')}</li>"
                )
            parts.append("<h4>🔧 已修正</h4><ul>" + "".join(items) + "</ul>")

        # 规则
        if result.applied_rules:
            parts.append("<h4>🛠️ 规则修复</h4><ul>" +
                         "".join(f"<li>{r}</li>" for r in result.applied_rules) + "</ul>")

        # 待处理
        if result.unresolved_issues:
            items = []
            for issue in result.unresolved_issues:
                items.append(
                    f"<li><b>{issue.get('type','')}</b> "
                    f"({issue.get('confidence','?')})<br>"
                    f"{issue.get('description','')}<br>"
                    f"<i>建议: {issue.get('suggested_action','')}</i></li>"
                )
            parts.append("<h4>⚠️ 待处理问题</h4><ul>" + "".join(items) + "</ul>")

        if not parts:
            parts.append("<p style='color:#27AE60;'>✅ 无待处理问题</p>")

        return "\n".join(parts)

    # ==================== 确认操作 ====================

    def _accept_all(self):
        for r in self.correction_results:
            self._confirm_status[r.table_index] = "accepted"
        self._refresh_ui()

    def _reject_all(self):
        for r in self.correction_results:
            self._confirm_status[r.table_index] = "rejected"
        self._refresh_ui()

    def _accept_high_confidence(self):
        for r in self.correction_results:
            if r.confidence == "high" and not r.unresolved_issues:
                self._confirm_status[r.table_index] = "accepted"
            elif self._confirm_status.get(r.table_index) == "accepted":
                self._confirm_status[r.table_index] = "pending"
        self._refresh_ui()

    def _accept_current(self):
        idx = self._current_table_index
        if idx >= 0:
            self._confirm_status[idx] = "accepted"
            self._update_list_item_status(idx)
            self._update_status_bar()

    def _reject_current(self):
        idx = self._current_table_index
        if idx >= 0:
            self._confirm_status[idx] = "rejected"
            self._update_list_item_status(idx)
            self._update_status_bar()

    def _refresh_ui(self):
        self._populate_list()
        if self.table_list.currentRow() >= 0:
            self._on_table_selected(self.table_list.currentRow())
        self._update_status_bar()

    def _update_status_bar(self):
        total = len(self.correction_results)
        accepted = sum(1 for v in self._confirm_status.values() if v == "accepted")
        rejected = sum(1 for v in self._confirm_status.values() if v == "rejected")

        high_conf = sum(1 for r in self.correction_results
                        if r.confidence == "high" and not r.unresolved_issues)
        medium_conf = sum(1 for r in self.correction_results
                          if r.confidence in ("medium", "low") and not r.unresolved_issues)
        unresolvable = sum(1 for r in self.correction_results
                           if r.status == "unresolvable" or r.confidence == "unresolvable")

        # 标签分布统计
        auto_fixed_count = sum(1 for r in self.correction_results
                               for t in r.error_tags if t.get("category") == "auto_fixed")
        needs_review_count = sum(1 for r in self.correction_results
                                 for t in r.error_tags if t.get("category") == "needs_review")
        llm_count = sum(1 for r in self.correction_results
                        for t in r.error_tags if t.get("category") == "llm_analyzed")

        parts = [
            f"⬤ 高置信度({high_conf})",
            f"🟡 中/低置信度({medium_conf})",
            f"🔴 需人工({unresolvable})",
            f"总表数: {total}",
        ]
        if auto_fixed_count:
            parts.append(f"🟢已修复({auto_fixed_count})")
        if needs_review_count:
            parts.append(f"🟡待确认({needs_review_count})")
        if llm_count:
            parts.append(f"🔵AI分析({llm_count})")

        self.stats_label.setText("  |  ".join(parts))

        self.progress_label.setText(f"已确认: {accepted}/{total}  已拒绝: {rejected}")

        self.apply_btn.setEnabled(accepted > 0)

    def _on_apply_clicked(self):
        """用户点击应用修改"""
        accepted = self.get_accepted_results()
        if not accepted:
            QMessageBox.information(self, "提示", "没有已接受的修正需要应用。")
            return

        count = len(accepted)
        reply = QMessageBox.question(
            self,
            "确认应用",
            f"将对 {count} 张表格应用 AI 修正。\n\n"
            "应用的修改包括：命名、层级标记、区域判断、数据修正。\n"
            "原始数据会被保存为 original_data 供撤销。\n\n确认应用？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            self.apply_requested.emit(accepted, dict(self._confirm_status))

    # ==================== 对外接口 ====================

    def get_accepted_results(self):
        """获取用户确认接受的修正结果"""
        return [
            r for r in self.correction_results
            if self._confirm_status.get(r.table_index, "pending") == "accepted"
        ]

    def get_confirm_status(self):
        return dict(self._confirm_status)

    def has_results(self):
        """是否有已加载的纠错结果"""
        return len(self.correction_results) > 0
