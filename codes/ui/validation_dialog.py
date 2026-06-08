# -*- coding: utf-8 -*-
"""
交叉验证结果对话框

展示 liteparse × pdf2docx 交叉验证的完整报告：
- 真假表格分类结果
- LLM 5 维度深度验证结果
"""

import json
import os
from pathlib import Path

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QTextBrowser,
    QSplitter, QMessageBox, QProgressBar, QWidget, QHeaderView,
    QGroupBox, QScrollArea, QFileDialog, QApplication,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QBrush

from codes.table_validator.models import (
    ClassifyResult, LLMVerifyResult, CrossValidateReport,
)

# 颜色常量
COLOR_GREEN = QColor("#27AE60")
COLOR_RED = QColor("#E74C3C")
COLOR_ORANGE = QColor("#E67E22")
COLOR_BLUE = QColor("#2980B9")
COLOR_GRAY = QColor("#95A5A6")
COLOR_DARK = QColor("#2C3E50")


class CrossValidationDialog(QDialog):
    """交叉验证结果对话框"""

    def __init__(self, report: CrossValidateReport, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("🔬 liteparse × pdf2docx 交叉验证报告")
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 标题栏
        title_layout = QHBoxLayout()

        title = QLabel("🔬 交叉验证报告")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {COLOR_DARK.name()};"
        )
        title_layout.addWidget(title)
        title_layout.addStretch()

        # 统计摘要
        real = self.report.real_table_count
        fake = self.report.fake_table_count
        verified = self.report.verified_count

        stats = QLabel(
            f"真表格: {real} | 假表格: {fake} | LLM 验证: {verified}"
        )
        stats.setStyleSheet(f"font-size: 13px; color: {COLOR_BLUE.name()}; font-weight: bold;")
        title_layout.addWidget(stats)

        # 紧急程度
        if self.report.has_issues:
            alert = QLabel("⚠️ 发现问题")
            alert.setStyleSheet(
                f"color: {COLOR_RED.name()}; font-weight: bold; font-size: 13px;"
                f"padding: 2px 10px; border: 2px solid {COLOR_RED.name()}; border-radius: 4px;"
            )
        else:
            alert = QLabel("✅ 无异常")
            alert.setStyleSheet(
                f"color: {COLOR_GREEN.name()}; font-weight: bold; font-size: 13px;"
                f"padding: 2px 10px; border: 2px solid {COLOR_GREEN.name()}; border-radius: 4px;"
            )
        title_layout.addWidget(alert)
        layout.addLayout(title_layout)

        if self.report.error:
            error_label = QLabel(f"❌ {self.report.error}")
            error_label.setStyleSheet(
                f"color: {COLOR_RED.name()}; background: #FDEDEC; padding: 8px;"
                f"border-radius: 4px; font-weight: bold;"
            )
            error_label.setWordWrap(True)
            layout.addWidget(error_label)

        # 双栏: 左侧树形导航，右侧详情
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：导航树
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_label = QLabel("页面导航")
        left_label.setStyleSheet(f"font-weight: bold; color: {COLOR_DARK.name()}; padding: 4px;")
        left_layout.addWidget(left_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["页码", "判定", "问题"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setStretchLastSection(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        left_layout.addWidget(self.tree)

        splitter.addWidget(left_panel)

        # 右侧：详情区域
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_label = QLabel("详情")
        right_label.setStyleSheet(f"font-weight: bold; color: {COLOR_DARK.name()}; padding: 4px;")
        right_layout.addWidget(right_label)

        self.detail_browser = QTextBrowser()
        self.detail_browser.setOpenExternalLinks(False)
        right_layout.addWidget(self.detail_browser)

        splitter.addWidget(right_panel)
        splitter.setSizes([350, 600])

        layout.addWidget(splitter, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet("""
            QPushButton { padding: 6px 20px; background: #5D6D7E; color: white;
                         border-radius: 4px; }
            QPushButton:hover { background: #4A5568; }
        """)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # 填充数据
        self._populate_tree()

    def _populate_tree(self):
        """填充导航树"""
        self.tree.clear()

        # 1. 真表格组
        real_results = [r for r in self.report.classify_results if r.is_real_table]
        if real_results:
            real_group = QTreeWidgetItem(self.tree)
            real_group.setText(0, f"✅ 真表格 ({len(real_results)}页)")
            real_group.setExpanded(True)
            font = QFont()
            font.setBold(True)
            real_group.setFont(0, font)
            real_group.setBackground(0, QColor("#E8F8F5"))

            for cr in real_results:
                item = QTreeWidgetItem(real_group)
                item.setText(0, f"第 {cr.page} 页")
                item.setText(1, "真表格")
                item.setData(0, Qt.UserRole, {"type": "real_table", "page": cr.page})

                # 查找对应的 LLM 结果
                llm_r = next(
                    (r for r in self.report.llm_results if r.page == cr.page),
                    None
                )
                if llm_r:
                    issue_count = self._count_issues(llm_r)
                    if issue_count > 0:
                        item.setText(2, f"⚠️ {issue_count} 个问题")
                        item.setForeground(2, COLOR_RED)
                    else:
                        item.setText(2, "✅ 无异常")
                        item.setForeground(2, COLOR_GREEN)
                else:
                    item.setText(2, "—")
                    item.setForeground(2, COLOR_GRAY)

        # 2. 假表格组
        fake_results = [r for r in self.report.classify_results if not r.is_real_table]
        if fake_results:
            fake_group = QTreeWidgetItem(self.tree)
            fake_group.setText(0, f"❌ 非表格 ({len(fake_results)}页)")
            fake_group.setExpanded(True)
            font = QFont()
            font.setBold(True)
            fake_group.setFont(0, font)
            fake_group.setBackground(0, QColor("#FDEDEC"))

            for cr in fake_results:
                item = QTreeWidgetItem(fake_group)
                item.setText(0, f"第 {cr.page} 页")
                item.setText(1, "非表格")
                item.setText(2, cr.reason[:40])
                item.setToolTip(2, cr.reason)
                item.setData(0, Qt.UserRole, {"type": "fake_table", "page": cr.page})
                item.setForeground(1, COLOR_RED)

    def _count_issues(self, llm_r: LLMVerifyResult) -> int:
        """统计问题数量"""
        count = 0
        if not llm_r.header_correct:
            count += 1
        if llm_r.has_duplicate_header or llm_r.has_duplicate_data:
            count += 1
        if llm_r.has_misalignment:
            count += 1
        if llm_r.has_footer_text:
            count += 1
        if llm_r.needs_merge_prev or llm_r.needs_merge_next:
            count += 1
        return count

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, col: int):
        """点击树节点时显示详情"""
        data = item.data(0, Qt.UserRole)
        if not data:
            return

        page = data.get("page", 0)
        item_type = data.get("type", "")

        if item_type == "real_table":
            self._show_real_table_detail(page)
        elif item_type == "fake_table":
            self._show_fake_table_detail(page)

    def _show_real_table_detail(self, page: int):
        """显示真表格 LLM 验证详情"""
        html = f'<h3 style="color: {COLOR_DARK.name()};">第 {page} 页 — 真表格</h3>'

        # 分类信息
        cr = next((r for r in self.report.classify_results if r.page == page), None)
        if cr:
            html += "<h4>📊 规则分类</h4>"
            html += f"<p><b>置信度:</b> {cr.confidence:.0%} | <b>理由:</b> {cr.reason}</p>"
            checks = cr.checks
            if checks:
                html += "<ul>"
                if "has_numeric_col" in checks:
                    html += f"<li>数值列: {'✅' if checks['has_numeric_col'] else '❌'} "
                    if checks.get("numeric_cols"):
                        for nc in checks["numeric_cols"]:
                            relaxed = "(宽松)" if nc.get("relaxed") else ""
                            html += f"第{nc['col']+1}列 数值率={nc['ratio']:.0%} {relaxed} "
                    html += "</li>"
                if "is_toc" in checks:
                    html += f"<li>目录页: {'⚠️ 是' if checks['is_toc'] else '✅ 否'}</li>"
                html += "</ul>"

        # LLM 验证结果
        llm_r = next((r for r in self.report.llm_results if r.page == page), None)
        if llm_r is None:
            html += '<p style="color: gray;">无 LLM 验证结果</p>'
            self.detail_browser.setHtml(html)
            return

        if llm_r.llm_error:
            html += f'<p style="color: {COLOR_RED.name()};"><b>LLM 错误:</b> {llm_r.llm_error}</p>'
            self.detail_browser.setHtml(html)
            return

        html += "<h4>🤖 LLM 深度验证</h4>"

        # 1. 表头
        icon = "✅" if llm_r.header_correct else "❌"
        html += f'<p><b>{icon} 表头:</b> {"正确" if llm_r.header_correct else "有问题"}'
        if llm_r.header_issues:
            html += "<ul>"
            for issue in llm_r.header_issues:
                html += f'<li style="color: {COLOR_RED.name()};">{issue}</li>'
            html += "</ul>"

        # 2. 重复
        if llm_r.has_duplicate_header or llm_r.has_duplicate_data:
            html += '<p><b>⚠️ 重复:</b>'
            html += "<ul>"
            for d in llm_r.duplicate_details:
                html += f'<li style="color: {COLOR_ORANGE.name()};">{d}</li>'
            html += "</ul>"
        else:
            html += "<p><b>✅ 重复:</b> 无异常</p>"

        # 3. 错位
        if llm_r.has_misalignment:
            html += '<p><b>⚠️ 错位:</b>'
            html += "<ul>"
            for d in llm_r.misalignment_details:
                html += f'<li style="color: {COLOR_RED.name()};">{d}</li>'
            html += "</ul>"
        else:
            html += "<p><b>✅ 错位:</b> 无异常</p>"

        # 4. 底部混入
        if llm_r.has_footer_text:
            html += '<p><b>⚠️ 底部混入文本:</b>'
            html += f'<span style="color: {COLOR_RED.name()};">从第 {llm_r.footer_from_row + 1} 行开始</span>'
            html += "<ul>"
            for d in llm_r.footer_details:
                html += f'<li style="color: {COLOR_RED.name()};">{d}</li>'
            html += "</ul>"
        else:
            html += "<p><b>✅ 底部混入:</b> 无异常</p>"

        # 5. 拼接
        if llm_r.needs_merge_prev or llm_r.needs_merge_next:
            html += '<p><b>🔄 拼接建议:</b>'
            html += "<ul>"
            for d in llm_r.merge_details:
                html += f'<li style="color: {COLOR_BLUE.name()};">{d}</li>'
            html += "</ul>"
        else:
            html += "<p><b>✅ 拼接:</b> 表格完整，无需拼接</p>"

        # Token 消耗
        if llm_r.usage:
            usage = llm_r.usage
            html += "<hr><p style='font-size: 11px; color: gray;'>"
            html += f"Token: 输入 {usage.get('prompt_tokens', 0)}, "
            html += f"输出 {usage.get('completion_tokens', 0)}, "
            html += f"共计 {usage.get('total_tokens', 0)}"
            html += "</p>"

        self.detail_browser.setHtml(html)

    def _show_fake_table_detail(self, page: int):
        """显示假表格详情"""
        cr = next((r for r in self.report.classify_results if r.page == page), None)
        if cr is None:
            self.detail_browser.setHtml("<p>无数据</p>")
            return

        html = f'<h3 style="color: {COLOR_RED.name()};">第 {page} 页 — 非表格</h3>'
        html += f'<p><b>判定理由:</b> {cr.reason}</p>'
        html += f'<p><b>置信度:</b> {cr.confidence:.0%}</p>'

        html += "<h4>📋 检查明细</h4><ul>"
        checks = cr.checks

        if "col_count" in checks:
            ok = checks["col_count"] >= 2
            icon = "✅" if ok else "❌"
            html += f"<li>{icon} 列数: {checks['col_count']}</li>"

        if "data_rows" in checks:
            ok = checks["data_rows"] >= 3
            icon = "✅" if ok else "❌"
            html += f"<li>{icon} 数据行: {checks['data_rows']}</li>"

        if "is_toc" in checks:
            html += f"<li>{'⚠️' if checks['is_toc'] else '✅'} 目录页检测: "
            html += f"{'是' if checks['is_toc'] else '否'}"
            if checks.get("toc_col") is not None:
                html += f" （第{checks['toc_col']+1}列 重复率 {checks.get('toc_dup_ratio', 0):.0%}）"
            html += "</li>"

        if "has_numeric_col" in checks:
            icon = "✅" if checks["has_numeric_col"] else "❌"
            html += f"<li>{icon} 数值列: {'有' if checks['has_numeric_col'] else '无'}</li>"

        if "parse_status" in checks:
            html += f"<li>解析状态: {checks['parse_status']}</li>"

        if "error" in checks:
            html += f"<li>错误: {checks['error']}</li>"

        html += "</ul>"
        self.detail_browser.setHtml(html)


# ============================================================
# 后台验证线程
# ============================================================

class CrossValidationWorker(QThread):
    """交叉验证后台线程"""
    progress = pyqtSignal(str, int, int)    # (step, current, total)
    finished = pyqtSignal(object)            # CrossValidateReport
    error = pyqtSignal(str)

    def __init__(self, pdf_path: str, processed_results: dict):
        super().__init__()
        self.pdf_path = pdf_path
        self.processed_results = processed_results

    def run(self):
        from codes.table_validator.validator import run_cross_validation

        try:
            report = run_cross_validation(
                pdf_path=self.pdf_path,
                processed_results=self.processed_results,
                progress_callback=lambda step, cur, tot: self.progress.emit(step, cur, tot),
            )
            self.finished.emit(report)
        except Exception as e:
            import traceback
            self.error.emit(f"{str(e)}\n{traceback.format_exc()}")


# ============================================================
# liteparse 表格分割验证对话框
# ============================================================

class LiteparseTableDialog(QDialog):
    """liteparse 表格分割验证对话框 — 支持逐表浏览 + 导出

    提供选项卡切换查看每张表的完整内容，
    以及一键导出全部表格为 JSON/TXT 文件。
    """

    def __init__(self, tables: list, report: dict, report_text: str, parent=None):
        super().__init__(parent)
        self.tables = tables
        self.report = report
        self.report_text = report_text
        self.setWindowTitle("📊 liteparse 表格分割验证报告")
        self.setMinimumSize(880, 620)
        self.resize(950, 700)
        self._init_ui()

    # --------------------------------------------------------
    # UI 构建
    # --------------------------------------------------------
    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ---- 标题栏 ----
        title_layout = QHBoxLayout()

        # 质量决策统计
        accepted = sum(1 for t in self.tables if t.get("quality_decision") == "accepted")
        review_n = sum(1 for t in self.tables if t.get("quality_decision") == "review")
        rejected = sum(1 for t in self.tables if t.get("quality_decision") == "rejected")
        title = QLabel(
            f"📊 分割完成：{self.report.get('total_tables', 0)} 张表 "
            f"（✅{accepted} 可信 · 🔍{review_n} 待复核 · ❌{rejected} 已拒绝），"
            f"覆盖 {self.report.get('table_pages', 0)} 页表格页"
        )
        title.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #2C3E50; padding: 4px 0;"
        )
        title_layout.addWidget(title)
        title_layout.addStretch()

        # 统计胶囊
        cross = self.report.get("cross_page_merges", 0)
        orphans_total = sum(
            len(items) for items in self.report.get("orphan_page_items", {}).values()
        )
        capsule = QLabel(
            f"  {orphans_total} 孤儿 items  |  {cross} 跨页拼接  |  纯规则驱动 · 零 API 成本  "
        )
        capsule.setStyleSheet(
            "background: #EBF5FB; color: #2980B9; font-size: 11px;"
            "padding: 3px 10px; border-radius: 8px;"
        )
        title_layout.addWidget(capsule)
        layout.addLayout(title_layout)

        # ---- 选项卡 ----
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #D5D8DC; border-top: none; }
            QTabBar::tab { padding: 6px 16px; font-size: 12px; }
            QTabBar::tab:selected { font-weight: bold; color: #2C3E50; }
        """)

        # 摘要选项卡
        summary_tab = QWidget()
        summary_layout = QVBoxLayout(summary_tab)
        report_browser = QTextBrowser()
        report_browser.setPlainText(self.report_text)
        report_browser.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 12px;"
        )
        summary_layout.addWidget(report_browser)
        self.tab_widget.addTab(summary_tab, "📋 验证摘要")

        # 每张表一个选项卡
        for t in self.tables:
            tab = self._build_table_tab(t)
            self.tab_widget.addTab(tab, self._tab_label(t))

        layout.addWidget(self.tab_widget, 1)

        # ---- 底部按钮栏 ----
        btn_layout = QHBoxLayout()

        export_json_btn = QPushButton("💾 导出 JSON（全部）")
        export_json_btn.setToolTip("导出全部表格为结构化 JSON，含坐标与行数据")
        export_json_btn.clicked.connect(lambda: self._export_all("json"))
        export_json_btn.setStyleSheet(self._btn_style("#27AE60"))
        btn_layout.addWidget(export_json_btn)

        export_txt_btn = QPushButton("📄 导出 TXT（全部）")
        export_txt_btn.setToolTip("导出全部表格为人类可读文本，方便人工逐表核查")
        export_txt_btn.clicked.connect(lambda: self._export_all("txt"))
        export_txt_btn.setStyleSheet(self._btn_style("#2980B9"))
        btn_layout.addWidget(export_txt_btn)

        export_csv_btn = QPushButton("📊 导出 CSV（全部）")
        export_csv_btn.setToolTip("导出全部表格为 CSV 列式文件")
        export_csv_btn.clicked.connect(lambda: self._export_all("csv"))
        export_csv_btn.setStyleSheet(self._btn_style("#8E44AD"))
        btn_layout.addWidget(export_csv_btn)

        btn_layout.addStretch()

        copy_btn = QPushButton("📋 复制当前表")
        copy_btn.clicked.connect(self._copy_current_table)
        copy_btn.setStyleSheet(self._btn_style("#7F8C8D"))
        btn_layout.addWidget(copy_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet(
            "QPushButton { padding: 6px 20px; background: #5D6D7E; color: white;"
            "border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background: #4A5568; }"
        )
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    # --------------------------------------------------------
    # 单表选项卡
    # --------------------------------------------------------
    def _build_table_tab(self, table: dict) -> QWidget:
        """为一张表构建选项卡内容（HTML 表格渲染）。"""
        w = QWidget()
        lt = QVBoxLayout(w)

        # 表头信息行
        info = self._table_info_html(table)
        info_label = QLabel(info)
        info_label.setWordWrap(True)
        info_label.setStyleSheet(
            "font-size: 12px; color: #2C3E50; padding: 4px 0;"
        )
        lt.addWidget(info_label)

        # 表格内容（HTML 渲染）
        browser = QTextBrowser()
        browser.setHtml(self._render_table_html(table))
        browser.setStyleSheet("font-family: 'Microsoft YaHei', sans-serif; font-size: 12px;")
        lt.addWidget(browser, 1)
        return w

    def _tab_label(self, table: dict) -> str:
        """生成选项卡标签。"""
        tid = table.get("table_id", "?")
        pages = table.get("pages", [table.get("page", "?")])
        if len(pages) <= 1:
            p_str = f"P{pages[0]}"
        else:
            p_str = f"P{pages[0]}-{pages[-1]}"
        cross = "↔" if table.get("is_cross_page") else ""
        decision = table.get("quality_decision", "")
        dec_icon = {"accepted": "✅", "review": "🔍", "rejected": "❌"}.get(decision, "")
        return f"{dec_icon} 表#{tid} {p_str}{cross}"

    def _table_info_html(self, table: dict) -> str:
        """表头上方的元信息行。"""
        tid = table.get("table_id", "?")
        pages = table.get("pages", [table.get("page", "?")])
        cap = table.get("caption", "") or "（无标题）"
        conf = table.get("confidence", 0)
        rows_n = table.get("row_count", 0)
        items_n = len(table.get("text_items", []))
        cross = "是" if table.get("is_cross_page") else "否"
        region_idx = table.get("region_index", -1)
        col_n = len(table.get("column_x_ranges", []))
        decision = table.get("quality_decision", "")
        dec_reason = table.get("quality_decision_reason", "")
        fin_conf = table.get("financial_confidence", 0.0)
        category = table.get("table_category", "")

        conf_color = "#27AE60" if conf >= 0.7 else ("#E67E22" if conf >= 0.4 else "#E74C3C")
        dec_color = {"accepted": "#27AE60", "review": "#E67E22", "rejected": "#E74C3C"}.get(decision, "#95A5A6")
        dec_label = {"accepted": "可信", "review": "待复核", "rejected": "已拒绝"}.get(decision, decision)

        return (
            f"<b>表#{tid}</b>  &nbsp; 页码: {pages}  &nbsp; "
            f"标题: <i>{cap}</i>  &nbsp; 行数: {rows_n}  &nbsp; "
            f"列数: {col_n}  &nbsp; Items: {items_n}  &nbsp; "
            f"跨页: {cross}  &nbsp; "
            f"分类: {category}  &nbsp; "
            f"财务置信: <span style='color:{conf_color};font-weight:bold;'>{fin_conf:.0%}</span>  &nbsp; "
            f"判定: <span style='color:{dec_color};font-weight:bold;'>{dec_label}</span>"
            f"{' (' + dec_reason + ')' if dec_reason else ''}"
        )

    def _render_table_html(self, table: dict) -> str:
        """将一张表渲染为 HTML 表格。"""
        rows = table.get("rows", [])
        if not rows:
            return "<p style='color:gray;'>表格无行数据</p>"

        # 估算列数
        max_cols = max((len(r.get("texts", [])) for r in rows), default=1)

        # 列 X 范围（用于显示列间距）
        col_ranges = table.get("column_x_ranges", [])
        col_ranges_str = ""
        if col_ranges:
            parts = [f"{x0:.0f}-{x1:.0f}" for x0, x1 in col_ranges]
            col_ranges_str = (
                "<p style='font-size:10px; color:#7F8C8D; margin:2px 0;'>"
                f"列 X 范围 (pt): {',  '.join(parts)}</p>"
            )

        # HTML 表头 + 行
        html = col_ranges_str
        html += '<table border="1" cellpadding="4" cellspacing="0" '
        html += 'style="border-collapse:collapse; width:100%; font-size:12px;">'

        for ri, row in enumerate(rows):
            texts = row.get("texts", [])
            is_first = (ri == 0)

            html += '<tr>'
            # 行号列
            html += (
                f'<td style="background:#F8F9FA; color:#7F8C8D; '
                f'text-align:right; width:30px; font-size:11px;'
                f'border:1px solid #D5D8DC;">{ri + 1}</td>'
            )

            for ci in range(max_cols):
                txt = texts[ci] if ci < len(texts) else ""
                # 截断过长文本
                display = txt if len(txt) <= 80 else txt[:77] + "..."

                if is_first:
                    # 首行（可能是表头）：深色背景
                    style = (
                        "background:#2C3E50; color:white; font-weight:bold;"
                        "border:1px solid #1A252F;"
                    )
                elif ci == 0:
                    # 第一列（行标签）：浅蓝背景
                    style = (
                        "background:#EBF5FB; color:#2C3E50; font-weight:bold;"
                        "border:1px solid #D5D8DC;"
                    )
                else:
                    # 数据列
                    is_numeric = any(c.isdigit() for c in txt)
                    if is_numeric:
                        style = (
                            "text-align:right; border:1px solid #D5D8DC;"
                            "font-family: Consolas, monospace;"
                        )
                    else:
                        style = "border:1px solid #D5D8DC;"

                html += f'<td style="{style}">{display if display else "&nbsp;"}</td>'

            html += '</tr>'

        html += '</table>'
        return html

    # --------------------------------------------------------
    # 导出
    # --------------------------------------------------------
    def _export_all(self, fmt: str):
        """导出全部表格到统一文件夹（不弹窗选路径，直接落盘）。"""
        from datetime import datetime

        base_dir = Path(__file__).parent.parent.parent / "data" / "mid_cache" / "liteparse_tables"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_path = str(base_dir / timestamp)
        os.makedirs(dir_path, exist_ok=True)

        try:
            if fmt == "json":
                self._export_json(dir_path)
            elif fmt == "txt":
                self._export_txt(dir_path)
            elif fmt == "csv":
                self._export_csv(dir_path)

            # 打开文件夹方便人工核查
            os.startfile(dir_path)
            QMessageBox.information(
                self, "导出完成",
                f"已导出 {len(self.tables)} 张表到：\n{dir_path}"
            )
        except Exception as e:
            import traceback
            QMessageBox.critical(self, "导出失败", f"{str(e)}\n\n{traceback.format_exc()}")

    def _export_json(self, dir_path: str):
        """导出 JSON 文件。"""
        # 全部表格汇总
        all_path = os.path.join(dir_path, "_all_tables.json")
        with open(all_path, "w", encoding="utf-8") as f:
            json.dump(self.tables, f, ensure_ascii=False, indent=2, default=str)

        # 逐表单独文件
        for t in self.tables:
            fname = self._table_filename(t, "json")
            with open(os.path.join(dir_path, fname), "w", encoding="utf-8") as f:
                json.dump(t, f, ensure_ascii=False, indent=2, default=str)

        # 报告
        report_path = os.path.join(dir_path, "_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(self.report, f, ensure_ascii=False, indent=2, default=str)

    def _export_txt(self, dir_path: str):
        """导出人类可读 TXT 文件。"""
        # 全部表格汇总
        all_lines = []
        all_lines.append("═" * 80)
        all_lines.append("  liteparse 表格分割 — 全部表格汇总")
        all_lines.append("═" * 80)
        all_lines.append("")

        for t in self.tables:
            all_lines.extend(self._table_to_txt_lines(t))
            all_lines.append("")

        all_path = os.path.join(dir_path, "_all_tables.txt")
        with open(all_path, "w", encoding="utf-8") as f:
            f.write("\n".join(all_lines))

        # 逐表单独文件（仅 accepted）
        for t in self.tables:
            if t.get("quality_decision") != "accepted":
                continue
            lines = self._table_to_txt_lines(t)
            fname = self._table_filename(t, "txt")
            with open(os.path.join(dir_path, fname), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

        # 验证报告
        report_path = os.path.join(dir_path, "_report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(self.report_text)

    def _table_to_txt_lines(self, table: dict) -> list:
        """将一张表转为格式化的文本行列表。"""
        lines = []
        tid = table.get("table_id", "?")
        pages = table.get("pages", [table.get("page", "?")])
        cap = table.get("caption", "") or "（无标题）"
        rows_n = table.get("row_count", 0)
        conf = table.get("confidence", 0)
        is_cross = table.get("is_cross_page", False)
        col_ranges = table.get("column_x_ranges", [])

        pages_str = (
            f"P{pages[0]}" if len(pages) <= 1
            else f"P{pages[0]}-{pages[-1]}"
        )
        cross_str = " [跨页]" if is_cross else ""

        lines.append("─" * 60)
        lines.append(f"  表#{tid}  {pages_str}{cross_str}  「{cap}」")
        lines.append(f"  行数: {rows_n}  置信度: {conf:.2f}")

        # 质量决策标注
        decision = table.get("quality_decision", "")
        dec_reason = table.get("quality_decision_reason", "")
        category = table.get("table_category", "")
        fin_conf = table.get("financial_confidence", 0.0)
        if decision:
            dec_icon = {"accepted": "✅", "review": "🔍", "rejected": "❌"}.get(decision, "")
            lines.append(f"  判定: {dec_icon} {decision} [{category}] 财务置信: {fin_conf:.0%}")
            if dec_reason:
                lines.append(f"  理由: {dec_reason}")

        if col_ranges:
            cr_str = ",  ".join(f"[{x0:.0f}, {x1:.0f}]" for x0, x1 in col_ranges)
            lines.append(f"  列 X 范围: {cr_str}")
        lines.append("─" * 60)

        rows = table.get("rows", [])
        if not rows:
            lines.append("  （无行数据）")
            return lines

        # 计算列宽
        max_cols = max((len(r.get("texts", [])) for r in rows), default=1)
        col_widths = [0] * max_cols
        for r in rows:
            for ci, txt in enumerate(r.get("texts", [])):
                # 中文字符算2宽度
                w = sum(2 if ord(c) > 127 else 1 for c in txt)
                if ci < max_cols:
                    col_widths[ci] = max(col_widths[ci], min(w, 40))

        for ri, row in enumerate(rows):
            texts = row.get("texts", [])
            parts = [f"{ri + 1:3d}│"]
            for ci in range(max_cols):
                txt = texts[ci] if ci < len(texts) else ""
                w = col_widths[ci] if ci < len(col_widths) else 10
                # 使用 format 对齐
                parts.append(f"{txt:<{w}}")
            lines.append(" ".join(parts))

        return lines

    def _export_csv(self, dir_path: str):
        """导出 CSV 文件。

        默认逐表导出：仅 accepted 表。
        汇总文件 _all_tables.csv：全部表格，标注质量决策。
        调试导出：_review_tables.csv、_rejected_tables.csv。
        """
        import csv

        # 分类表格
        accepted = [t for t in self.tables if t.get("quality_decision") == "accepted"]
        review = [t for t in self.tables if t.get("quality_decision") == "review"]
        rejected = [t for t in self.tables if t.get("quality_decision") == "rejected"]

        # 逐表单独 CSV：仅 accepted（可信表）
        for t in accepted:
            rows = self._get_table_csv_rows(t)
            if not rows:
                continue
            max_cols = max(len(r) for r in rows)
            fname = self._table_filename(t, "csv")
            fpath = os.path.join(dir_path, fname)
            with open(fpath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                for row in rows:
                    writer.writerow(row + [""] * (max_cols - len(row)))

        # 全部汇总 CSV（用空行分隔，标注质量决策）
        all_csv_path = os.path.join(dir_path, "_all_tables.csv")
        with open(all_csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            for ti, t in enumerate(self.tables):
                if ti > 0:
                    writer.writerow([])  # 空行分隔
                # 表标识行（含质量决策标注）
                tid = t.get("table_id", "?")
                pages = t.get("pages", [t.get("page", "?")])
                cap = t.get("caption", "") or "（无标题）"
                category = t.get("table_category", "")
                conf = t.get("financial_confidence", 0.0)
                decision = t.get("quality_decision", "?")
                reason = t.get("quality_decision_reason", "")
                conf_str = f" | 置信度:{conf:.0%}" if conf > 0 else ""
                cat_str = f" [{category}]" if category else ""
                dec_str = f" [{decision.upper()}: {reason}]" if decision else ""
                writer.writerow([f"# 表#{tid}  P{pages}{cat_str}{conf_str}{dec_str}  {cap}"])
                rows = self._get_table_csv_rows(t)
                if rows:
                    max_cols = max(len(r) for r in rows)
                    for row in rows:
                        writer.writerow(row + [""] * (max_cols - len(row)))

        # 调试导出：仅 review 表
        if review:
            self._write_tables_csv(review, os.path.join(dir_path, "_review_tables.csv"))
        # 调试导出：仅 rejected 表
        if rejected:
            self._write_tables_csv(rejected, os.path.join(dir_path, "_rejected_tables.csv"))

        # 质量报告 JSON
        quality_path = os.path.join(dir_path, "_quality_report.json")
        quality_data = {
            "accepted": len(accepted),
            "review": len(review),
            "rejected": len(rejected),
            "total": len(self.tables),
            "by_category": {},
            "details": [],
        }
        for t in self.tables:
            cat = t.get("table_category", "未知")
            quality_data["by_category"][cat] = quality_data["by_category"].get(cat, 0) + 1
            quality_data["details"].append({
                "table_id": t.get("table_id", -1),
                "page": t.get("page", 0),
                "caption": t.get("caption", ""),
                "decision": t.get("quality_decision", "?"),
                "reason": t.get("quality_decision_reason", ""),
                "category": t.get("table_category", ""),
                "confidence": t.get("financial_confidence", 0.0),
            })
        with open(quality_path, "w", encoding="utf-8") as f:
            json.dump(quality_data, f, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _get_table_csv_rows(table: dict) -> list:
        """获取表格的 CSV 数据（二维列表），优先清洗后 data，降级到原始 rows.texts。"""
        data = table.get("data", [])
        if data:
            return [list(row) for row in data]
        rows = table.get("rows", [])
        if rows:
            return [list(row.get("texts", [])) for row in rows]
        return []

    @staticmethod
    def _write_tables_csv(tables: list, fpath: str):
        """将表格列表写入单个 CSV 汇总文件（空行分隔）。"""
        import csv
        with open(fpath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            for ti, t in enumerate(tables):
                if ti > 0:
                    writer.writerow([])
                tid = t.get("table_id", "?")
                pages = t.get("pages", [t.get("page", "?")])
                cap = t.get("caption", "") or "（无标题）"
                category = t.get("table_category", "")
                decision = t.get("quality_decision", "?")
                reason = t.get("quality_decision_reason", "")
                writer.writerow([f"# 表#{tid}  P{pages} [{category}] [{decision.upper()}: {reason}]  {cap}"])
                rows = ValidationDialog._get_table_csv_rows(t)
                if rows:
                    max_cols = max(len(r) for r in rows)
                    for row in rows:
                        writer.writerow(row + [""] * (max_cols - len(row)))

    def _table_filename(self, table: dict, ext: str) -> str:
        """生成表格文件名。"""
        tid = table.get("table_id", 0)
        pages = table.get("pages", [table.get("page", "?")])
        p_str = f"P{'-'.join(str(p) for p in pages)}"
        cap = table.get("caption", "")
        # 文件名安全处理
        cap_safe = ""
        if cap:
            import re
            cap_safe = "_" + re.sub(r'[\\/:*?"<>|]', '', cap)[:30].strip()
        return f"table_{tid:03d}_{p_str}{cap_safe}.{ext}"

    # --------------------------------------------------------
    # 复制
    # --------------------------------------------------------
    def _copy_current_table(self):
        """复制当前选项卡表格为 TSV 文本。"""
        idx = self.tab_widget.currentIndex()
        if idx < 1:  # 摘要选项卡
            QApplication.clipboard().setText(self.report_text)
            QMessageBox.information(self, "已复制", "验证摘要文本已复制到剪贴板")
            return

        table_idx = idx - 1
        if table_idx >= len(self.tables):
            return

        table = self.tables[table_idx]
        rows = table.get("rows", [])
        if not rows:
            return

        max_cols = max((len(r.get("texts", [])) for r in rows), default=1)
        tsv_lines = []
        for row in rows:
            texts = row.get("texts", [])
            tsv_lines.append("\t".join(texts + [""] * (max_cols - len(texts))))

        QApplication.clipboard().setText("\n".join(tsv_lines))
        QMessageBox.information(self, "已复制", "当前表格已按 TSV 格式复制到剪贴板")

    @staticmethod
    def _btn_style(color: str) -> str:
        """按钮样式模板。"""
        return (
            f"QPushButton {{ padding: 6px 14px; background: {color}; color: white;"
            f"border-radius: 4px; font-size: 12px; border: none; }}"
            f"QPushButton:hover {{ background: {color}; opacity: 0.85; }}"
        )
