# -*- coding: utf-8 -*-
"""格式纠错独立 Tab — 预览后再应用，避免盲改。"""

from __future__ import annotations

import os
from copy import deepcopy
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTextBrowser, QCheckBox,
    QMessageBox, QSplitter, QAbstractItemView, QTableWidget,
    QTableWidgetItem, QTabWidget, QGroupBox, QComboBox,
)

from codes.format_corrector import FormatCorrectorEngine, FormatCorrectionReport
from codes.format_corrector.conservation import merge_tables_preserve
from codes.format_corrector.cross_page_merge import propose_merge
from codes.format_corrector.liteparse_bridge import load_liteparse_dict
from codes.format_corrector.models import TaskStatus, TaskType
from codes.pdf_extractor.widgets import PDFPreviewWidget, ZoomableScrollArea


def _fill_table_widget(widget: QTableWidget, data, *, highlight_from_row: int = -1):
    """把二维数组填进 QTableWidget；highlight_from_row 起高亮（合并新增行）。

    文本段落的 data 常为 str；若直接 iterate 会变成「一字一行」假表，这里先规范化。
    """
    if data is None:
        data = []
    elif isinstance(data, str):
        # 整段文本 → 单列多行（按换行），绝不当字符矩阵
        lines = [ln for ln in data.splitlines() if ln.strip()] or ([data] if data.strip() else [])
        data = [[ln] for ln in lines]
    elif not isinstance(data, list):
        data = [[str(data)]]
    else:
        # 行若不是 list（异常结构），包一层，避免对 str 行再按字符拆开
        norm = []
        for row in data:
            if isinstance(row, list):
                norm.append(row)
            elif isinstance(row, str):
                norm.append([row])
            else:
                norm.append([str(row) if row is not None else ""])
        data = norm

    rows = len(data)
    cols = max((len(r) for r in data), default=0)
    widget.clear()
    widget.setRowCount(rows)
    widget.setColumnCount(cols)
    hi = QBrush(QColor("#FFF3CD"))
    for ri, row in enumerate(data):
        for ci in range(cols):
            val = row[ci] if ci < len(row) else ""
            item = QTableWidgetItem("" if val is None else str(val))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if highlight_from_row >= 0 and ri >= highlight_from_row:
                item.setBackground(hi)
            widget.setItem(ri, ci, item)
    widget.resizeColumnsToContents()


class FormatCorrectorTab(QWidget):
    """可疑表格式纠错工作台：先预览合并效果，再逐条接受/拒绝。"""

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._report: Optional[FormatCorrectionReport] = None
        self._pending_tables = None
        self._tables_snapshot_before_apply = None
        self._accepted_ids = set()
        self._rejected_ids = set()
        self._current_task_id = None
        self._preview_merged = None
        self._preview_meta = {}
        self._pdf_pages: List[int] = []  # 当前任务可切换的 PDF 页
        self._pdf_page_idx = 0
        self._filtered_task_ids: List[str] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        title = QLabel("格式纠错（独立模块）")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #1A5276;")
        root.addWidget(title)

        hint = QLabel(
            "流程：扫描 → 左侧点任务 → 对照左侧 PDF 原页与右侧合并预览 → 接受/拒绝 →「应用已接受」。"
            "未点应用前不会改对比预览。不改正 OCR。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #566573; padding-bottom: 6px;")
        root.addWidget(hint)

        bar = QHBoxLayout()
        self.use_llm_cb = QCheckBox("启用 LLM 裁判")
        self.write_back_cb = QCheckBox("应用后写回 data.json")
        self.write_back_cb.setChecked(False)
        bar.addWidget(self.use_llm_cb)
        bar.addWidget(self.write_back_cb)
        bar.addStretch()

        self.scan_btn = QPushButton("扫描可疑表")
        self.scan_btn.clicked.connect(self._on_scan)
        bar.addWidget(self.scan_btn)

        self.accept_btn = QPushButton("✓ 接受此项")
        self.accept_btn.setToolTip("接受当前预览的修正（尚未写回，需再点「应用已接受」）")
        self.accept_btn.clicked.connect(self._on_accept_current)
        self.accept_btn.setEnabled(False)
        self.accept_btn.setStyleSheet(
            "QPushButton { background:#27AE60; color:white; padding:4px 12px; border-radius:4px; }"
        )
        bar.addWidget(self.accept_btn)

        self.reject_btn = QPushButton("✗ 拒绝此项")
        self.reject_btn.clicked.connect(self._on_reject_current)
        self.reject_btn.setEnabled(False)
        self.reject_btn.setStyleSheet(
            "QPushButton { background:#E74C3C; color:white; padding:4px 12px; border-radius:4px; }"
        )
        bar.addWidget(self.reject_btn)

        self.apply_btn = QPushButton("应用已接受")
        self.apply_btn.setToolTip("把已接受的任务真正写进对比预览（可再撤销上次应用）")
        self.apply_btn.clicked.connect(self._on_apply_accepted)
        self.apply_btn.setEnabled(False)
        bar.addWidget(self.apply_btn)

        self.undo_btn = QPushButton("撤销上次应用")
        self.undo_btn.clicked.connect(self._on_undo_apply)
        self.undo_btn.setEnabled(False)
        bar.addWidget(self.undo_btn)
        root.addLayout(bar)

        self.stats_label = QLabel("尚未扫描")
        self.stats_label.setStyleSheet("color: #7F8C8D;")
        root.addWidget(self.stats_label)

        main_split = QSplitter(Qt.Horizontal)

        # ---- PDF 原页预览（与对比预览同源 preview_images）----
        pdf_panel = QWidget()
        pdf_l = QVBoxLayout(pdf_panel)
        pdf_l.setContentsMargins(0, 0, 0, 0)
        pdf_header = QHBoxLayout()
        pdf_header.addWidget(QLabel("📄 PDF 原页"))
        self.pdf_page_label = QLabel("—")
        self.pdf_page_label.setStyleSheet("color: #566573;")
        pdf_header.addWidget(self.pdf_page_label)
        pdf_header.addStretch()
        self.pdf_prev_btn = QPushButton("◀ 前页")
        self.pdf_prev_btn.setEnabled(False)
        self.pdf_prev_btn.clicked.connect(lambda: self._shift_pdf_page(-1))
        self.pdf_next_btn = QPushButton("后页 ▶")
        self.pdf_next_btn.setEnabled(False)
        self.pdf_next_btn.clicked.connect(lambda: self._shift_pdf_page(1))
        pdf_header.addWidget(self.pdf_prev_btn)
        pdf_header.addWidget(self.pdf_next_btn)
        pdf_l.addLayout(pdf_header)

        self.pdf_scroll_area = ZoomableScrollArea()
        self.pdf_scroll_area.setMinimumWidth(240)
        self.pdf_scroll_area.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")
        self.pdf_preview_widget = PDFPreviewWidget()
        self.pdf_scroll_area.setWidget(self.pdf_preview_widget)
        self.pdf_scroll_area.setWidgetResizable(False)
        self.pdf_scroll_area.zoomChanged.connect(
            lambda f: self.pdf_preview_widget.set_zoom(f)
        )
        pdf_l.addWidget(self.pdf_scroll_area, 1)
        self.pdf_hint = QLabel("选中任务后显示对应 PDF 页（需已生成预览图）")
        self.pdf_hint.setWordWrap(True)
        self.pdf_hint.setStyleSheet("color: #95A5A6; font-size: 11px;")
        pdf_l.addWidget(self.pdf_hint)
        main_split.addWidget(pdf_panel)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.addWidget(QLabel("任务列表（先点开预览，再接受）"))

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("筛选:"))
        self.task_kind_filter = QComboBox()
        self.task_kind_filter.addItem("全部任务", "all")
        self.task_kind_filter.addItem("仅合并类", "merge")
        self.task_kind_filter.addItem("仅非合并类", "non_merge")
        self.task_kind_filter.setToolTip(
            "合并类：跨页合并、缺表头跨页候选（挂了前后表关联）\n"
            "非合并类：空行空列分割、文表边界、仅缺表头标记等"
        )
        self.task_kind_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.task_kind_filter, 1)

        self.task_status_filter = QComboBox()
        self.task_status_filter.addItem("全部状态", "all")
        self.task_status_filter.addItem("待处理", "pending")
        self.task_status_filter.addItem("已接受", "accepted")
        self.task_status_filter.addItem("已拒绝", "rejected")
        self.task_status_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.task_status_filter)
        left_l.addLayout(filter_row)

        nav_row = QHBoxLayout()
        self.prev_task_btn = QPushButton("◀ 上一项")
        self.prev_task_btn.setToolTip("筛选结果中的上一项")
        self.prev_task_btn.clicked.connect(lambda: self._shift_filtered_task(-1))
        self.next_task_btn = QPushButton("下一项 ▶")
        self.next_task_btn.setToolTip("筛选结果中的下一项")
        self.next_task_btn.clicked.connect(lambda: self._shift_filtered_task(1))
        self.filter_count_label = QLabel("—")
        self.filter_count_label.setStyleSheet("color: #7F8C8D;")
        nav_row.addWidget(self.prev_task_btn)
        nav_row.addWidget(self.next_task_btn)
        nav_row.addWidget(self.filter_count_label, 1)
        left_l.addLayout(nav_row)

        self.task_list = QListWidget()
        self.task_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.task_list.itemSelectionChanged.connect(self._on_select)
        left_l.addWidget(self.task_list)
        main_split.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)

        self.preview_title = QLabel("请选择左侧任务查看合并前 / 合并后预览")
        self.preview_title.setWordWrap(True)
        self.preview_title.setStyleSheet("font-weight: bold; color: #1A5276;")
        right_l.addWidget(self.preview_title)

        self.preview_tabs = QTabWidget()
        self.tbl_prev = QTableWidget()
        self.tbl_next = QTableWidget()
        self.tbl_merged = QTableWidget()
        for w in (self.tbl_prev, self.tbl_next, self.tbl_merged):
            w.setAlternatingRowColors(True)
        self.preview_tabs.addTab(self.tbl_prev, "① 前表（合并前）")
        self.preview_tabs.addTab(self.tbl_next, "② 后表（合并前）")
        self.preview_tabs.addTab(self.tbl_merged, "③ 合并后预览")
        right_l.addWidget(self.preview_tabs, 3)

        detail_box = QGroupBox("任务说明")
        detail_l = QVBoxLayout(detail_box)
        self.detail = QTextBrowser()
        self.detail.setMaximumHeight(140)
        detail_l.addWidget(self.detail)
        right_l.addWidget(detail_box)

        main_split.addWidget(right)
        main_split.setStretchFactor(0, 2)
        main_split.setStretchFactor(1, 1)
        main_split.setStretchFactor(2, 3)
        root.addWidget(main_split, 1)

    def load_report(self, report: FormatCorrectionReport):
        self._report = report
        self._pending_tables = None
        self._accepted_ids.clear()
        self._rejected_ids.clear()
        tables = self._current_tables()
        if tables:
            for t in self._report.tasks:
                if t.task_type == TaskType.CROSS_PAGE_MERGE:
                    propose_merge(t, tables)
        self._reload_list()

    def _current_pdf(self):
        if not self.main_window:
            return None
        return getattr(self.main_window, "current_file", None)

    def _current_tables(self):
        mw = self.main_window
        if not mw or not getattr(mw, "processed_results", None):
            return []
        return list(mw.processed_results.get("tables") or [])

    def _on_scan(self):
        pdf = self._current_pdf()
        tables = self._current_tables()
        if not tables:
            QMessageBox.warning(self, "无数据", "请先加载/提取 PDF，确保对比预览中有表格数据。")
            return

        engine = FormatCorrectorEngine(
            pdf or "",
            use_llm=self.use_llm_cb.isChecked(),
            auto_apply=False,
            pre_structure_split=True,
        )
        liteparse = load_liteparse_dict(pdf) if pdf else None
        self._report = engine.run_on_tables(tables, liteparse)
        # 结构预拆分后的表写回，预览「前表」不再夹（五）/重复表头
        working = engine.last_working_tables or tables
        if self.main_window and self.main_window.processed_results is not None:
            self.main_window.processed_results["tables"] = working
            self.main_window.processed_results["total_tables"] = len(working)
            if self._report.summary.get("structure_presplit_count"):
                self.main_window.processed_results["_format_structure_presplit"] = True
            tcm = getattr(self.main_window, "table_compare_manager", None)
            if tcm and hasattr(tcm, "apply_table_filter"):
                try:
                    tcm.apply_table_filter()
                except Exception:
                    pass
        tables = working
        for t in self._report.tasks:
            if t.task_type == TaskType.CROSS_PAGE_MERGE:
                propose_merge(t, tables)
        self._accepted_ids.clear()
        self._rejected_ids.clear()
        self._pending_tables = None
        self._reload_list()
        n_split = self._report.summary.get("structure_presplit_count", 0)
        QMessageBox.information(
            self,
            "扫描完成",
            f"共 {self._report.summary.get('task_count', 0)} 项"
            + (f"；已结构预拆分 {n_split} 处粘连表" if n_split else "")
            + "。\n"
            "请点左侧任务对照 PDF 与「合并后预览」，确认后「接受此项」，\n"
            "最后「应用已接受」写入对比预览。",
        )

    def _task_location_label(self, task) -> str:
        loc = (task.evidence or {}).get("location")
        if loc:
            return str(loc)
        from codes.format_corrector.candidates import format_location

        tables = self._current_tables()
        if tables:
            return format_location(tables, task.table_index, task.related_indices or None)
        return f"P{task.page or '?'}_?"

    def _is_merge_task(self, task) -> bool:
        """合并类：跨页合并，或挂了前表关联的缺表头跨页候选。"""
        if task.task_type == TaskType.CROSS_PAGE_MERGE:
            return True
        if task.task_type == TaskType.HEADER_CROSS_PAGE and task.related_indices:
            return True
        return False

    def _task_matches_filters(self, task) -> bool:
        kind = self.task_kind_filter.currentData() if hasattr(self, "task_kind_filter") else "all"
        status = (
            self.task_status_filter.currentData()
            if hasattr(self, "task_status_filter")
            else "all"
        )
        is_merge = self._is_merge_task(task)
        if kind == "merge" and not is_merge:
            return False
        if kind == "non_merge" and is_merge:
            return False

        accepted = task.task_id in self._accepted_ids
        rejected = task.task_id in self._rejected_ids
        if status == "pending" and (accepted or rejected or task.status == TaskStatus.APPLIED):
            return False
        if status == "accepted" and not accepted:
            return False
        if status == "rejected" and not rejected:
            return False
        return True

    def _on_filter_changed(self):
        keep_id = self._current_task_id
        self._reload_list()
        if keep_id and keep_id in self._filtered_task_ids:
            self._select_task_id(keep_id)
        elif self._filtered_task_ids:
            self._select_task_id(self._filtered_task_ids[0])
        else:
            self._current_task_id = None
            self._clear_preview()
            self.accept_btn.setEnabled(False)
            self.reject_btn.setEnabled(False)
            self.preview_title.setText("当前筛选下无任务")

    def _shift_filtered_task(self, delta: int):
        ids = self._filtered_task_ids
        if not ids:
            return
        try:
            cur = ids.index(self._current_task_id) if self._current_task_id in ids else -1
        except ValueError:
            cur = -1
        nxt = 0 if cur < 0 else (cur + delta) % len(ids)
        self._select_task_id(ids[nxt])

    def _reload_list(self):
        self.task_list.blockSignals(True)
        self.task_list.clear()
        self._filtered_task_ids = []
        if not self._report:
            self.task_list.blockSignals(False)
            if hasattr(self, "filter_count_label"):
                self.filter_count_label.setText("—")
            return
        s = self._report.summary or {}
        visible = [t for t in self._report.tasks if self._task_matches_filters(t)]
        self._filtered_task_ids = [t.task_id for t in visible]
        n_merge = sum(1 for t in self._report.tasks if self._is_merge_task(t))
        n_non = len(self._report.tasks) - n_merge
        self.stats_label.setText(
            f"任务 {s.get('task_count', 0)}（合并 {n_merge} / 非合并 {n_non}）| "
            f"已接受 {len(self._accepted_ids)} | 已拒绝 {len(self._rejected_ids)} | "
            f"类型 {s.get('by_type', {})}"
        )
        if hasattr(self, "filter_count_label"):
            kind = self.task_kind_filter.currentText()
            st = self.task_status_filter.currentText()
            self.filter_count_label.setText(
                f"显示 {len(visible)}/{len(self._report.tasks)} · {kind} · {st}"
            )
        for t in visible:
            loc = self._task_location_label(t)
            mark = ""
            if t.task_id in self._accepted_ids:
                mark = "✓ "
            elif t.task_id in self._rejected_ids:
                mark = "✗ "
            elif t.status == TaskStatus.APPLIED:
                mark = "✔已应用 "
            kind_tag = "合并" if self._is_merge_task(t) else "其他"
            item = QListWidgetItem(
                f"{mark}[{kind_tag}] {loc}  [{t.confidence.value}] {t.task_type.value} "
                f"— {t.reason[:50]}"
            )
            item.setData(Qt.UserRole, t.task_id)
            if t.task_id in self._accepted_ids:
                item.setBackground(QBrush(QColor("#D5F5E3")))
            elif t.task_id in self._rejected_ids:
                item.setBackground(QBrush(QColor("#FADBD8")))
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)
        self.apply_btn.setEnabled(bool(self._accepted_ids))
        has = bool(self._filtered_task_ids)
        if hasattr(self, "prev_task_btn"):
            self.prev_task_btn.setEnabled(has)
            self.next_task_btn.setEnabled(has)

    def _clear_preview(self):
        for w in (self.tbl_prev, self.tbl_next, self.tbl_merged):
            w.clear()
            w.setRowCount(0)
            w.setColumnCount(0)
        self.preview_tabs.setTabText(0, "① 前表（合并前）")
        self.preview_tabs.setTabText(1, "② 后表（合并前）")
        self.preview_tabs.setTabText(2, "③ 合并后预览")
        self._preview_merged = None
        self._preview_meta = {}

    def _on_select(self):
        items = self.task_list.selectedItems()
        if not items or not self._report:
            return
        tid = items[0].data(Qt.UserRole)
        task = next((x for x in self._report.tasks if x.task_id == tid), None)
        if not task:
            return
        self._current_task_id = tid
        self._show_task_preview(task)

    def _task_pdf_pages(self, task) -> List[int]:
        """当前任务涉及的 PDF 页码（1-based，去重保序）。"""
        tables = self._current_tables()
        pages: List[int] = []

        def _add(idx: Optional[int]):
            if idx is None or not tables or idx < 0 or idx >= len(tables):
                return
            p = int(tables[idx].get("page") or 0)
            if p > 0 and p not in pages:
                pages.append(p)

        if task.task_type == TaskType.CROSS_PAGE_MERGE:
            keep = int((task.proposal or {}).get("keep_index", task.table_index))
            absorb = int((task.proposal or {}).get(
                "absorb_index",
                (task.related_indices or [-1])[0],
            ))
            _add(keep)
            _add(absorb)
        elif task.related_indices:
            for ri in task.related_indices:
                _add(ri)
            _add(task.table_index)
        else:
            _add(task.table_index)
            if task.page and task.page not in pages:
                pages.append(int(task.page))
        return pages

    def _show_pdf_page(self, page: int) -> bool:
        """显示 1-based PDF 页预览。"""
        mw = self.main_window
        if not mw:
            self.pdf_hint.setText("无主窗口，无法加载 PDF 预览")
            return False
        preview_images = getattr(mw, "preview_images", None) or []
        current_file = getattr(mw, "current_file", None)
        if not preview_images:
            self.pdf_hint.setText(
                "尚未生成 PDF 预览图。请先在「处理」完成解析，或到「对比预览」打开过该文档。"
            )
            self.pdf_page_label.setText("—")
            return False
        if page < 1 or page > len(preview_images):
            self.pdf_hint.setText(f"页码 P{page} 超出预览范围（共 {len(preview_images)} 页）")
            return False
        img_path = preview_images[page - 1]
        if not img_path or not os.path.exists(img_path):
            self.pdf_hint.setText(f"预览文件不存在：P{page}")
            return False
        self.pdf_preview_widget.set_preview(
            img_path, page - 1, pdf_path=current_file
        )
        self.pdf_page_label.setText(f"P{page}")
        self.pdf_hint.setText("滚轮缩放；跨页任务可用「前页/后页」切换相关页")
        return True

    def _sync_pdf_preview(self, task) -> None:
        pages = self._task_pdf_pages(task)
        self._pdf_pages = pages
        self._pdf_page_idx = 0
        multi = len(pages) > 1
        self.pdf_prev_btn.setEnabled(multi)
        self.pdf_next_btn.setEnabled(multi)
        if not pages:
            self.pdf_page_label.setText("—")
            self.pdf_hint.setText("当前任务无有效页码")
            return
        self._show_pdf_page(pages[0])
        if multi:
            self.pdf_page_label.setText(
                f"P{pages[0]}（共 {len(pages)} 页：{'/'.join('P'+str(p) for p in pages)}）"
            )

    def _shift_pdf_page(self, delta: int) -> None:
        if not self._pdf_pages:
            return
        self._pdf_page_idx = (self._pdf_page_idx + delta) % len(self._pdf_pages)
        page = self._pdf_pages[self._pdf_page_idx]
        self._show_pdf_page(page)
        if len(self._pdf_pages) > 1:
            self.pdf_page_label.setText(
                f"P{page}（{self._pdf_page_idx + 1}/{len(self._pdf_pages)}："
                f"{'/'.join('P'+str(p) for p in self._pdf_pages)}）"
            )

    def _show_task_preview(self, task):
        import json

        tables = self._current_tables()
        loc = self._task_location_label(task)
        self._clear_preview()
        self.accept_btn.setEnabled(task.status != TaskStatus.APPLIED)
        self.reject_btn.setEnabled(task.status != TaskStatus.APPLIED)
        self._sync_pdf_preview(task)

        payload = task.to_dict()
        payload["location"] = loc
        payload["review"] = (
            "accepted" if task.task_id in self._accepted_ids
            else "rejected" if task.task_id in self._rejected_ids
            else "pending"
        )

        if task.task_type == TaskType.CROSS_PAGE_MERGE and tables:
            keep = int((task.proposal or {}).get("keep_index", task.table_index))
            absorb = int((task.proposal or {}).get(
                "absorb_index",
                (task.related_indices or [-1])[0],
            ))
            if 0 <= keep < len(tables) and 0 <= absorb < len(tables):
                prev = tables[keep].get("data") or []
                nxt = tables[absorb].get("data") or []
                merged, _allowed, skip, note = merge_tables_preserve(prev, nxt)
                self._preview_merged = merged
                self._preview_meta = {"keep": keep, "absorb": absorb, "skip": skip, "note": note}
                _fill_table_widget(self.tbl_prev, prev)
                _fill_table_widget(self.tbl_next, nxt)
                _fill_table_widget(self.tbl_merged, merged, highlight_from_row=len(prev))
                self.preview_tabs.setCurrentWidget(self.tbl_merged)
                self.preview_title.setText(
                    f"{loc}  合并预览：前表 {len(prev)} 行 + 后表 {len(nxt)} 行 "
                    f"→ {len(merged)} 行（黄底=后表并入部分；{note}）\n"
                    "确认无误后点「接受此项」，全部确认完再点「应用已接受」。"
                )
                payload["preview"] = {
                    "prev_rows": len(prev),
                    "next_rows": len(nxt),
                    "merged_rows": len(merged),
                    "skipped_header_rows": skip,
                    "note": note,
                    "conservation": task.conservation_detail,
                }
            else:
                self.preview_title.setText(f"{loc}  索引无效，无法预览")
        elif task.task_type == TaskType.HEADER_CROSS_PAGE and tables:
            cur = task.table_index
            prev_i = (task.related_indices or [None])[0]
            if prev_i is not None and 0 <= prev_i < len(tables) and 0 <= cur < len(tables):
                prev = tables[prev_i].get("data") or []
                nxt = tables[cur].get("data") or []
                merged, _, skip, note = merge_tables_preserve(prev, nxt)
                _fill_table_widget(self.tbl_prev, prev)
                _fill_table_widget(self.tbl_next, nxt)
                _fill_table_widget(self.tbl_merged, merged, highlight_from_row=len(prev))
                self.preview_tabs.setTabText(0, "① 疑似前表")
                self.preview_tabs.setTabText(1, "② 缺表头表")
                self.preview_tabs.setTabText(2, "③ 若合并则效果")
                self.preview_tabs.setCurrentWidget(self.tbl_merged)
                self.preview_title.setText(
                    f"{loc}  缺表头跨页候选预览（尚未合并）。"
                    "若确认是续表，请到对应的 cross_page_merge 任务点「接受此项」。"
                )
            else:
                data = tables[cur].get("data") if 0 <= cur < len(tables) else []
                _fill_table_widget(self.tbl_next, data or [])
                self.preview_title.setText(f"{loc}  仅缺表头标记，无相邻前表可合并预览")
        else:
            idx = task.table_index
            if tables and 0 <= idx < len(tables):
                _fill_table_widget(self.tbl_prev, tables[idx].get("data") or [])
                self.preview_title.setText(
                    f"{loc}  [{task.task_type.value}] 当前表内容（请对照说明接受/拒绝）"
                )
            else:
                self.preview_title.setText(f"{loc}  无表数据可预览")

        self.detail.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))

    def _advance_after_decision(self, decided_id: str):
        """接受/拒绝后跳到筛选列表中下一条仍待处理的任务。"""
        ids = list(self._filtered_task_ids)
        # 若当前筛的是「待处理」，列表刷新后 decided 已消失，从原位置找下一条
        try:
            pos = ids.index(decided_id)
        except ValueError:
            pos = -1
        self._reload_list()
        pending = [
            tid for tid in self._filtered_task_ids
            if tid not in self._accepted_ids
            and tid not in self._rejected_ids
        ]
        if not pending:
            if decided_id in self._filtered_task_ids:
                self._select_task_id(decided_id)
            elif self._filtered_task_ids:
                self._select_task_id(self._filtered_task_ids[0])
            return
        # 优先：原位置之后的下一条待处理
        nxt = None
        if pos >= 0:
            after = [
                tid for tid in ids[pos + 1 :]
                if tid in pending
            ]
            if after:
                nxt = after[0]
        if nxt is None:
            nxt = pending[0]
        self._select_task_id(nxt)

    def _on_accept_current(self):
        if not self._current_task_id:
            return
        decided = self._current_task_id
        self._accepted_ids.add(decided)
        self._rejected_ids.discard(decided)
        self._advance_after_decision(decided)

    def _on_reject_current(self):
        if not self._current_task_id:
            return
        decided = self._current_task_id
        self._rejected_ids.add(decided)
        self._accepted_ids.discard(decided)
        self._advance_after_decision(decided)

    def _select_task_id(self, tid: str):
        for i in range(self.task_list.count()):
            if self.task_list.item(i).data(Qt.UserRole) == tid:
                self.task_list.setCurrentRow(i)
                break

    def _on_apply_accepted(self):
        if not self._report or not self._accepted_ids:
            QMessageBox.information(self, "无已接受项", "请先预览并点「接受此项」。")
            return
        reply = QMessageBox.question(
            self,
            "确认应用",
            f"将把已接受的 {len(self._accepted_ids)} 项写入对比预览。\n"
            "写错可用「撤销上次应用」恢复。\n继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        pdf = self._current_pdf()
        tables = self._current_tables()
        if not tables:
            return

        self._tables_snapshot_before_apply = deepcopy(tables)
        self.undo_btn.setEnabled(True)

        engine = FormatCorrectorEngine(pdf or "", use_llm=False, auto_apply=False)
        liteparse = load_liteparse_dict(pdf) if pdf else None
        new_tables, self._report = engine.apply(
            tables,
            self._report,
            only_auto=False,
            accepted_ids=set(self._accepted_ids),
            liteparse_data=liteparse,
            remove_text_rows=False,
        )

        if self.main_window and self.main_window.processed_results is not None:
            self.main_window.processed_results["tables"] = new_tables
            self.main_window.processed_results["total_tables"] = len(new_tables)
            self.main_window.processed_results["_format_corrector_applied"] = True

        if self.write_back_cb.isChecked() and pdf:
            try:
                engine.write_back_mid_cache(
                    new_tables, payload=self.main_window.processed_results
                )
            except Exception as e:
                QMessageBox.warning(self, "写回失败", str(e))

        applied = [t for t in self._report.tasks if t.status == TaskStatus.APPLIED]
        merge_keeps = [
            int((t.proposal or {}).get("keep_index", t.table_index))
            for t in applied
            if t.task_type == TaskType.CROSS_PAGE_MERGE
        ]
        for t in applied:
            self._accepted_ids.discard(t.task_id)

        tcm = getattr(self.main_window, "table_compare_manager", None)
        focus = merge_keeps[0] if merge_keeps else None
        if tcm and hasattr(tcm, "refresh_after_format_correction"):
            tcm.refresh_after_format_correction(focus_index=focus)

        self._reload_list()
        QMessageBox.information(
            self,
            "已写入对比预览",
            f"已应用 {len(applied)} 项。\n"
            "对比预览中：「🔗合」= 合并后的前表；「↪」= 已并入的后表占位。\n"
            "若有误，点「撤销上次应用」。",
        )

    def _on_undo_apply(self):
        if not self._tables_snapshot_before_apply:
            QMessageBox.information(self, "无法撤销", "没有可撤销的应用快照。")
            return
        if self.main_window and self.main_window.processed_results is not None:
            self.main_window.processed_results["tables"] = deepcopy(
                self._tables_snapshot_before_apply
            )
            self.main_window.processed_results["total_tables"] = len(
                self._tables_snapshot_before_apply
            )
        if self._report:
            for t in self._report.tasks:
                if t.status == TaskStatus.APPLIED:
                    t.status = TaskStatus.PROPOSED
        self._tables_snapshot_before_apply = None
        self.undo_btn.setEnabled(False)

        tcm = getattr(self.main_window, "table_compare_manager", None)
        if tcm and hasattr(tcm, "refresh_after_format_correction"):
            tcm.refresh_after_format_correction()
        self._reload_list()
        QMessageBox.information(self, "已撤销", "对比预览已恢复为上次应用前的表格。")
