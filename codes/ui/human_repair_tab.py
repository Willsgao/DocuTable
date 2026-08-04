# -*- coding: utf-8 -*-
"""人工修复队列 Tab：审阅 LLM 提案 / 需人工表 → 接受/拒绝 → 写回。"""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTextBrowser, QCheckBox,
    QMessageBox, QSplitter, QAbstractItemView, QTableWidget,
    QTableWidgetItem, QTabWidget, QComboBox,
)

from codes.pdf_extractor.widgets import PDFPreviewWidget, ZoomableScrollArea
from codes.table_repair.human_queue import (
    DECISION_ACCEPTED,
    DECISION_PENDING,
    DECISION_REJECTED,
    HumanQueueItem,
    apply_queue_decisions,
    collect_human_queue,
    snapshot_tables,
)


def _fill_table_widget(widget: QTableWidget, data, *, changed_only: bool = False,
                       other=None):
    if data is None:
        data = []
    elif not isinstance(data, list):
        data = [[str(data)]]
    else:
        norm = []
        for row in data:
            if isinstance(row, list):
                norm.append(row)
            else:
                norm.append([str(row) if row is not None else ""])
        data = norm

    rows = len(data)
    cols = max((len(r) for r in data), default=0)
    other_n = other or []
    widget.clear()
    widget.setRowCount(rows)
    widget.setColumnCount(cols)
    hi = QBrush(QColor("#FFF3CD"))
    for ri, row in enumerate(data):
        for ci in range(cols):
            val = row[ci] if ci < len(row) else ""
            item = QTableWidgetItem("" if val is None else str(val))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if changed_only and other_n:
                ov = ""
                if ri < len(other_n) and ci < len(other_n[ri]):
                    ov = other_n[ri][ci]
                if str(val) != str(ov):
                    item.setBackground(hi)
            widget.setItem(ri, ci, item)
    widget.resizeColumnsToContents()


class _LlmProposeWorker(QThread):
    finished_signal = pyqtSignal(object, int)  # FacadeResult-compat, table_index

    def __init__(self, table: dict, table_index: int, parent=None):
        super().__init__(parent)
        self.table = table
        self.table_index = table_index

    def run(self):
        from codes.table_repair.llm_facade import repair_table_dict_with_facade

        result = repair_table_dict_with_facade(self.table, apply=False)
        self.finished_signal.emit(result, self.table_index)


class HumanRepairTab(QWidget):
    """统一人工队列工作台。"""

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._items: List[HumanQueueItem] = []
        self._decisions: Dict[str, str] = {}  # item_id -> decision
        self._current_id: Optional[str] = None
        self._snapshot_before_apply = None
        self._worker: Optional[_LlmProposeWorker] = None
        self._pdf_pages: List[int] = []
        self._pdf_page_idx = 0
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        title = QLabel("人工修复队列")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #1A5276;")
        root.addWidget(title)

        hint = QLabel(
            "只收「有 AI 提案」或「必须人工」两类。"
            "选中条目后左侧对照 PDF 原页，右侧看修复前/提案 → 接受/拒绝 →「应用已决定」。"
            "llm_candidate 不会自动进队。需已生成预览图（处理或对比预览过该文档）。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #566573; padding-bottom: 6px;")
        root.addWidget(hint)

        bar = QHBoxLayout()
        self.write_back_cb = QCheckBox("应用后写回 data.json")
        self.write_back_cb.setChecked(False)
        bar.addWidget(self.write_back_cb)
        bar.addStretch()

        self.refresh_btn = QPushButton("刷新队列")
        self.refresh_btn.clicked.connect(self.refresh_queue)
        bar.addWidget(self.refresh_btn)

        self.propose_btn = QPushButton("请求 LLM 提案")
        self.propose_btn.setToolTip("对当前无提案的表调用 Facade（不写回，仅生成提案）")
        self.propose_btn.clicked.connect(self._on_request_llm)
        self.propose_btn.setEnabled(False)
        bar.addWidget(self.propose_btn)

        self.accept_btn = QPushButton("✓ 接受")
        self.accept_btn.setStyleSheet(
            "QPushButton { background:#27AE60; color:white; padding:4px 12px; border-radius:4px; }"
        )
        self.accept_btn.clicked.connect(self._on_accept)
        self.accept_btn.setEnabled(False)
        bar.addWidget(self.accept_btn)

        self.reject_btn = QPushButton("✗ 拒绝")
        self.reject_btn.setStyleSheet(
            "QPushButton { background:#E74C3C; color:white; padding:4px 12px; border-radius:4px; }"
        )
        self.reject_btn.clicked.connect(self._on_reject)
        self.reject_btn.setEnabled(False)
        bar.addWidget(self.reject_btn)

        self.done_btn = QPushButton("标为手改完成")
        self.done_btn.setToolTip("无提案或已在对比页手改：确认当前表内容 OK")
        self.done_btn.clicked.connect(self._on_mark_done)
        self.done_btn.setEnabled(False)
        bar.addWidget(self.done_btn)

        self.apply_btn = QPushButton("应用已决定")
        self.apply_btn.clicked.connect(self._on_apply)
        self.apply_btn.setEnabled(False)
        bar.addWidget(self.apply_btn)

        self.undo_btn = QPushButton("撤销上次应用")
        self.undo_btn.clicked.connect(self._on_undo)
        self.undo_btn.setEnabled(False)
        bar.addWidget(self.undo_btn)
        root.addLayout(bar)

        self.stats_label = QLabel("尚未刷新")
        self.stats_label.setStyleSheet("color: #7F8C8D;")
        root.addWidget(self.stats_label)

        split = QSplitter(Qt.Horizontal)

        # ---- PDF 原页（与对比预览同源 preview_images）----
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
        self.pdf_scroll_area.setMinimumWidth(260)
        self.pdf_scroll_area.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")
        self.pdf_preview_widget = PDFPreviewWidget()
        self.pdf_scroll_area.setWidget(self.pdf_preview_widget)
        self.pdf_scroll_area.setWidgetResizable(False)
        self.pdf_scroll_area.zoomChanged.connect(
            lambda f: self.pdf_preview_widget.set_zoom(f)
        )
        pdf_l.addWidget(self.pdf_scroll_area, 1)
        self.pdf_hint = QLabel("选中队列条目后显示对应 PDF 页")
        self.pdf_hint.setWordWrap(True)
        self.pdf_hint.setStyleSheet("color: #95A5A6; font-size: 11px;")
        pdf_l.addWidget(self.pdf_hint)
        split.addWidget(pdf_panel)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.addWidget(QLabel("队列"))

        filt = QHBoxLayout()
        filt.addWidget(QLabel("筛选:"))
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部", "all")
        self.status_filter.addItem("有提案", "has_proposal")
        self.status_filter.addItem("无提案", "no_proposal")
        self.status_filter.addItem("待处理", "pending")
        self.status_filter.addItem("已接受", "accepted")
        self.status_filter.addItem("已拒绝", "rejected")
        self.status_filter.currentIndexChanged.connect(self._reload_list)
        filt.addWidget(self.status_filter, 1)
        left_l.addLayout(filt)

        self.task_list = QListWidget()
        self.task_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.task_list.itemSelectionChanged.connect(self._on_select)
        left_l.addWidget(self.task_list)
        split.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)

        self.detail_title = QLabel("请选择左侧条目")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-weight: bold; color: #1A5276;")
        right_l.addWidget(self.detail_title)

        self.meta_browser = QTextBrowser()
        self.meta_browser.setMaximumHeight(120)
        right_l.addWidget(self.meta_browser)

        self.preview_tabs = QTabWidget()
        self.tbl_before = QTableWidget()
        self.tbl_after = QTableWidget()
        for w in (self.tbl_before, self.tbl_after):
            w.setAlternatingRowColors(True)
        self.preview_tabs.addTab(self.tbl_before, "修复前")
        self.preview_tabs.addTab(self.tbl_after, "提案 / 当前")
        right_l.addWidget(self.preview_tabs, 1)

        self.report_browser = QTextBrowser()
        self.report_browser.setMaximumHeight(140)
        self.report_browser.setPlaceholderText("推理 / 报告")
        right_l.addWidget(self.report_browser)

        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 3)
        root.addWidget(split, 1)

    def _tables(self) -> list:
        mw = self.main_window
        if not mw or not getattr(mw, "processed_results", None):
            return []
        return list(mw.processed_results.get("tables") or [])

    def showEvent(self, event):
        super().showEvent(event)
        # 每次打开时若队列空则自动刷一次
        if not self._items:
            self.refresh_queue()

    def refresh_queue(self):
        tables = self._tables()
        if not tables:
            self._items = []
            self._reload_list()
            self.stats_label.setText("无表格数据，请先处理 PDF")
            return
        # 保留已有 decision
        old = dict(self._decisions)
        self._items = collect_human_queue(tables)
        for it in self._items:
            if it.item_id in old:
                it.decision = old[it.item_id]
                self._decisions[it.item_id] = old[it.item_id]
        self._reload_list()
        n_prop = sum(1 for i in self._items if i.has_proposal)
        self.stats_label.setText(
            f"队列 {len(self._items)} 项（有提案 {n_prop}）· "
            f"接受 {sum(1 for d in self._decisions.values() if d == DECISION_ACCEPTED)} / "
            f"拒绝 {sum(1 for d in self._decisions.values() if d == DECISION_REJECTED)}"
        )
        self.apply_btn.setEnabled(any(
            d in (DECISION_ACCEPTED, DECISION_REJECTED)
            for d in self._decisions.values()
        ))

    def _reload_list(self):
        filt = self.status_filter.currentData()
        self.task_list.blockSignals(True)
        self.task_list.clear()
        for it in self._items:
            dec = self._decisions.get(it.item_id, it.decision)
            it.decision = dec
            if filt == "has_proposal" and not it.has_proposal:
                continue
            if filt == "no_proposal" and it.has_proposal:
                continue
            if filt == "pending" and dec != DECISION_PENDING:
                continue
            if filt == "accepted" and dec != DECISION_ACCEPTED:
                continue
            if filt == "rejected" and dec != DECISION_REJECTED:
                continue
            mark = {
                DECISION_ACCEPTED: "✓",
                DECISION_REJECTED: "✗",
            }.get(dec, "·")
            prop = "提案" if it.has_proposal else "待办"
            tags = ",".join(it.problem_tags[:3]) or "-"
            text = (
                f"{mark} [{prop}] P{it.page} #{it.table_index} "
                f"{it.repair_status} | {tags}"
            )
            if it.caption:
                text += f" | {it.caption[:24]}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, it.item_id)
            if dec == DECISION_ACCEPTED:
                item.setForeground(QColor("#1E8449"))
            elif dec == DECISION_REJECTED:
                item.setForeground(QColor("#C0392B"))
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)
        if self._current_id:
            self._select_id(self._current_id)

    def _select_id(self, item_id: str):
        for i in range(self.task_list.count()):
            if self.task_list.item(i).data(Qt.UserRole) == item_id:
                self.task_list.setCurrentRow(i)
                break

    def _current_item(self) -> Optional[HumanQueueItem]:
        if not self._current_id:
            return None
        for it in self._items:
            if it.item_id == self._current_id:
                return it
        return None

    def _on_select(self):
        row = self.task_list.currentItem()
        if not row:
            self._current_id = None
            self._set_actions_enabled(False)
            self.pdf_page_label.setText("—")
            self.pdf_hint.setText("选中队列条目后显示对应 PDF 页")
            self.pdf_prev_btn.setEnabled(False)
            self.pdf_next_btn.setEnabled(False)
            return
        self._current_id = row.data(Qt.UserRole)
        it = self._current_item()
        if not it:
            self._set_actions_enabled(False)
            return
        self._set_actions_enabled(True)
        self.propose_btn.setEnabled(not it.has_proposal)
        self.detail_title.setText(
            f"P{it.page} 表#{it.table_index} · {it.repair_status}"
            + (f" · {it.caption}" if it.caption else "")
        )
        tags = ", ".join(it.problem_tags) or "（无）"
        rules = ", ".join(it.rule_ids) or "（无）"
        conf = f"{it.confidence:.0%}" if it.confidence else "—"
        pdf_pages = self._item_pdf_pages(it)
        pages_txt = ", ".join(f"P{p}" for p in pdf_pages) if pdf_pages else "—"
        self.meta_browser.setHtml(
            f"<b>PDF 页</b>: {pages_txt}<br>"
            f"<b>问题标签</b>: {tags}<br>"
            f"<b>质检规则</b>: {rules}<br>"
            f"<b>置信度</b>: {conf}<br>"
            f"<b>类别</b>: {it.table_category or '—'}<br>"
            f"<b>错误</b>: {it.llm_error or '—'}"
        )
        before = it.before_data
        after = it.proposed_data if it.has_proposal else (
            self._live_table_data(it.table_index) or before
        )
        _fill_table_widget(self.tbl_before, before)
        _fill_table_widget(
            self.tbl_after, after, changed_only=it.has_proposal, other=before
        )
        self.preview_tabs.setTabText(
            1, "提案（差异高亮）" if it.has_proposal else "当前表"
        )
        report = it.report_text or it.reasoning_summary or ""
        self.report_browser.setPlainText(report)
        self._sync_pdf_preview(it)

    def _item_pdf_pages(self, it: HumanQueueItem) -> List[int]:
        """条目相关 PDF 页（1-based，去重保序）：主页 + 合并来源页。"""
        pages: List[int] = []

        def _add(p):
            try:
                n = int(p)
            except (TypeError, ValueError):
                return
            if n > 0 and n not in pages:
                pages.append(n)

        _add(it.page)
        tables = self._tables()
        if 0 <= it.table_index < len(tables):
            t = tables[it.table_index]
            _add(t.get("page"))
            _add(t.get("page_num"))
            _add(t.get("end_page"))
            for p in t.get("_merged_from_pages") or []:
                _add(p)
            for p in t.get("pages") or []:
                _add(p)
        return pages

    def _show_pdf_page(self, page: int) -> bool:
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
            self.pdf_hint.setText(
                f"页码 P{page} 超出预览范围（共 {len(preview_images)} 页）"
            )
            return False
        img_path = preview_images[page - 1]
        if not img_path or not os.path.exists(str(img_path)):
            self.pdf_hint.setText(f"预览文件不存在：P{page}")
            return False
        self.pdf_preview_widget.set_preview(
            img_path, page - 1, pdf_path=current_file
        )
        self.pdf_page_label.setText(f"P{page}")
        self.pdf_hint.setText("滚轮缩放；跨页表可用「前页/后页」切换相关页")
        return True

    def _sync_pdf_preview(self, it: HumanQueueItem) -> None:
        pages = self._item_pdf_pages(it)
        self._pdf_pages = pages
        self._pdf_page_idx = 0
        multi = len(pages) > 1
        self.pdf_prev_btn.setEnabled(multi)
        self.pdf_next_btn.setEnabled(multi)
        if not pages:
            self.pdf_page_label.setText("—")
            self.pdf_hint.setText("当前条目无有效页码")
            return
        self._show_pdf_page(pages[0])
        if multi:
            self.pdf_page_label.setText(
                f"P{pages[0]}（共 {len(pages)} 页："
                f"{'/'.join('P' + str(p) for p in pages)}）"
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
                f"{'/'.join('P' + str(p) for p in self._pdf_pages)}）"
            )

    def _live_table_data(self, idx: int):
        tables = self._tables()
        if 0 <= idx < len(tables):
            return tables[idx].get("data") or []
        return []

    def _set_actions_enabled(self, on: bool):
        self.accept_btn.setEnabled(on)
        self.reject_btn.setEnabled(on)
        self.done_btn.setEnabled(on)
        if not on:
            self.propose_btn.setEnabled(False)

    def _set_decision(self, decision: str):
        it = self._current_item()
        if not it:
            return
        it.decision = decision
        self._decisions[it.item_id] = decision
        self._reload_list()
        self.apply_btn.setEnabled(True)
        n_a = sum(1 for d in self._decisions.values() if d == DECISION_ACCEPTED)
        n_r = sum(1 for d in self._decisions.values() if d == DECISION_REJECTED)
        self.stats_label.setText(
            f"队列 {len(self._items)} 项 · 接受 {n_a} / 拒绝 {n_r}（尚未应用）"
        )

    def _on_accept(self):
        it = self._current_item()
        if not it:
            return
        if not it.has_proposal:
            reply = QMessageBox.question(
                self,
                "无 LLM 提案",
                "当前条目没有可应用的提案。\n"
                "「接受」将把该表标为「手改完成」（确认当前 data 可用）。\n\n继续？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._set_decision(DECISION_ACCEPTED)

    def _on_reject(self):
        self._set_decision(DECISION_REJECTED)

    def _on_mark_done(self):
        """无提案快捷：等同接受并标 human_done。"""
        self._set_decision(DECISION_ACCEPTED)

    def _on_request_llm(self):
        it = self._current_item()
        if not it or it.has_proposal:
            return
        from codes.pdf_extractor import load_config

        cfg = load_config()
        if not str(cfg.get("deepseek_api_key") or "").strip():
            QMessageBox.warning(
                self,
                "未配置 DeepSeek API",
                "请先在「配置」页填写并保存 DeepSeek API Key。",
            )
            return
        tables = self._tables()
        if it.table_index < 0 or it.table_index >= len(tables):
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "进行中", "已有 LLM 请求在运行。")
            return
        self.propose_btn.setEnabled(False)
        self.propose_btn.setText("请求中...")
        self._worker = _LlmProposeWorker(tables[it.table_index], it.table_index)
        self._worker.finished_signal.connect(self._on_llm_done)
        self._worker.start()

    def _on_llm_done(self, result, table_index: int):
        self.propose_btn.setText("请求 LLM 提案")
        self.propose_btn.setEnabled(True)
        ok = bool(getattr(result, "success", False) or getattr(result, "applied", False))
        # FacadeResult: success + repaired_table
        repaired = getattr(result, "repaired_table", None) or []
        if not ok and not repaired:
            err = getattr(result, "llm_error", "") or str(
                getattr(result, "validation_errors", "")
            )
            QMessageBox.warning(self, "未生成提案", f"LLM 未给出可用提案：\n{err}")
        self.refresh_queue()
        self._select_id(f"t{table_index}")
        self._on_select()

    def _on_apply(self):
        tables = self._tables()
        if not tables:
            return
        pending_items = []
        for it in self._items:
            dec = self._decisions.get(it.item_id, it.decision)
            it.decision = dec
            if dec in (DECISION_ACCEPTED, DECISION_REJECTED):
                pending_items.append(it)
        if not pending_items:
            QMessageBox.information(self, "无决定", "请先接受或拒绝至少一项。")
            return
        reply = QMessageBox.question(
            self,
            "确认应用",
            f"将写回 {len(pending_items)} 项决定到对比预览数据。\n继续？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._snapshot_before_apply = snapshot_tables(tables)
        # 在 processed_results 上的同一 list 上改
        live = self.main_window.processed_results["tables"]
        a, r, s = apply_queue_decisions(live, pending_items)

        if self.write_back_cb.isChecked():
            self._try_write_data_json()

        self.undo_btn.setEnabled(True)
        # 清掉已应用的 decision
        for it in pending_items:
            self._decisions.pop(it.item_id, None)
        self.refresh_queue()

        # 刷新对比预览
        tcm = getattr(self.main_window, "table_compare_manager", None)
        if tcm and hasattr(tcm, "refresh_after_format_correction"):
            focus = pending_items[0].table_index if pending_items else None
            try:
                tcm.refresh_after_format_correction(focus_index=focus)
            except Exception:
                pass

        QMessageBox.information(
            self,
            "已应用",
            f"接受 {a} · 拒绝 {r} · 跳过 {s}",
        )
        if self.main_window and hasattr(self.main_window, "status_bar"):
            self.main_window.status_bar.showMessage(
                f"人工队列已应用: 接受{a} 拒绝{r}"
            )

    def _try_write_data_json(self):
        mw = self.main_window
        path = getattr(mw, "current_file", None)
        if not path:
            return
        try:
            from codes.pdf_extractor.utils import save_mid_data

            save_mid_data(path, mw.processed_results)
        except Exception as exc:
            QMessageBox.warning(self, "写回失败", f"无法写 data.json：\n{exc}")

    def _on_undo(self):
        if not self._snapshot_before_apply:
            return
        reply = QMessageBox.question(
            self,
            "撤销应用",
            "恢复到上次「应用已决定」之前的 tables 快照？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.main_window.processed_results["tables"] = deepcopy(
            self._snapshot_before_apply
        )
        self._snapshot_before_apply = None
        self.undo_btn.setEnabled(False)
        self.refresh_queue()
        tcm = getattr(self.main_window, "table_compare_manager", None)
        if tcm and hasattr(tcm, "refresh_after_format_correction"):
            try:
                tcm.refresh_after_format_correction()
            except Exception:
                pass

    def focus_table(self, table_index: int):
        """从对比页跳入时定位某表。"""
        self.refresh_queue()
        self._select_id(f"t{table_index}")
        self._on_select()
