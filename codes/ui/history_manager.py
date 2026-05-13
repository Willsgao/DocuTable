"""
历史记录管理模块
处理PDF解析历史记录、加载历史PDF等功能
"""
import os
import time
from datetime import datetime
from functools import partial

from PyQt5.QtWidgets import (
    QTableWidget, QTableWidgetItem, QMessageBox, QApplication, QPushButton
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor

from codes.pdf_extractor.utils import load_mid_data, save_pdf_history, get_project_root


class HistoryManager:
    """历史记录管理器"""

    def __init__(self, main_window):
        self.mw = main_window
        self.history_table = None
        self.history_filter_combo = None
        self.history_mode_combo = None
        self.history_stats_label = None
        self.history_preview = None
        self.loading_timer = None
        self.loading_start_time = None
        self.loading_row = -1

    def setup_history_tab(self, history_table, history_filter_combo,
                           history_mode_combo, history_stats_label, history_preview):
        """注入历史Tab的控件引用"""
        self.history_table = history_table
        self.history_filter_combo = history_filter_combo
        self.history_mode_combo = history_mode_combo
        self.history_stats_label = history_stats_label
        self.history_preview = history_preview

    def refresh_history_list(self):
        """刷新历史记录列表"""
        if not self.history_table:
            return

        self.history_table.setRowCount(0)

        # 根据筛选条件过滤
        filter_text = self.history_filter_combo.currentText() if self.history_filter_combo else "全部"
        show_latest_only = self.history_mode_combo.currentIndex() == 0 if self.history_mode_combo else True

        # 按文件名去重，只保留最新的
        latest_records = {}
        for record in self.mw.pdf_history:
            filename = record.get('filename', '')
            latest_records[filename] = record

        # 根据显示模式选择记录
        if show_latest_only:
            filtered_records = list(latest_records.values())
        else:
            filtered_records = list(self.mw.pdf_history)

        filtered_history = []
        for record in filtered_records:
            if filter_text == "全部":
                filtered_history.append(record)
            elif filter_text == "✅ 成功" and record.get('status') == 'success':
                filtered_history.append(record)
            elif filter_text == "❌ 图片类PDF" and record.get('status') == 'image_pdf':
                filtered_history.append(record)
            elif filter_text == "⚠️ 部分失败" and record.get('status') == 'partial':
                filtered_history.append(record)
            elif filter_text == "❌ 解析失败" and record.get('status') == 'failed':
                filtered_history.append(record)

        for record in filtered_history:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)

            # 删除按钮（第0列）
            delete_btn = QPushButton("删除")
            delete_btn.setCursor(Qt.PointingHandCursor)
            delete_btn.setFixedSize(45, 22)
            delete_btn.setStyleSheet("""
                QPushButton { background-color: #E74C3C; color: white; border: none;
                              padding: 2px 4px; border-radius: 3px; font-size: 11px; }
                QPushButton:hover { background-color: #C0392B; }
            """)
            delete_btn.clicked.connect(partial(self.delete_history_record, row))
            self.history_table.setCellWidget(row, 0, delete_btn)

            # 文件名（第1列）
            filename_item = QTableWidgetItem(record.get('filename', ''))
            filename_item.setData(Qt.UserRole, record)
            self.history_table.setItem(row, 1, filename_item)

            # 状态（第2列）
            status = record.get('status', 'unknown')
            status_text = {
                'success': '✅ 成功',
                'image_pdf': '❌ 图片类PDF',
                'partial': '⚠️ 部分失败',
                'failed': '❌ 解析失败'
            }.get(status, '❓ 未知')
            status_item = QTableWidgetItem(status_text)
            if status == 'success':
                status_item.setBackground(QColor('#E8F8F5'))
            elif status == 'image_pdf':
                status_item.setBackground(QColor('#FDEDEC'))
            elif status == 'partial':
                status_item.setBackground(QColor('#FEF9E7'))
            else:
                status_item.setBackground(QColor('#F5EEF8'))
            self.history_table.setItem(row, 2, status_item)

            # 总页数（第3列）
            self.history_table.setItem(row, 3, QTableWidgetItem(str(record.get('total_pages', 0))))

            # 成功数（第4列）
            success_count = record.get('success_count', 0)
            self.history_table.setItem(row, 4, QTableWidgetItem(str(success_count)))

            # 加载中（第5列，初始为空）
            self.history_table.setItem(row, 5, QTableWidgetItem(""))

            # 预览按钮（第6列）
            preview_btn = QPushButton("预览")
            preview_btn.setCursor(Qt.PointingHandCursor)
            preview_btn.setFixedSize(40, 22)
            preview_btn.clicked.connect(partial(self.on_preview_button_clicked, row))
            self.history_table.setCellWidget(row, 6, preview_btn)
            
            # 处理时间（第7列）
            processed_time = record.get('processed_time', record.get('timestamp', ''))
            if 'T' in processed_time:
                try:
                    dt = datetime.fromisoformat(processed_time)
                    processed_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    pass
            self.history_table.setItem(row, 7, QTableWidgetItem(processed_time))

        # 更新统计信息
        total = len(self.mw.pdf_history)
        unique_files = len(latest_records)
        mode_text = "最新一次" if show_latest_only else "全部"
        if self.history_stats_label:
            self.history_stats_label.setText(
                f"共 {unique_files} 个文件 / {total} 条记录 | "
                f"显示模式: {mode_text} | 当前显示 {len(filtered_history)} 条"
            )

    def filter_history_list(self):
        """根据筛选条件过滤历史记录"""
        self.refresh_history_list()

    def delete_history_record(self, row):
        """删除指定行的历史记录"""
        if not self.history_table or row < 0:
            return
        
        # 文件名现在在第1列
        item = self.history_table.item(row, 1)
        if not item:
            return
        
        record = item.data(Qt.UserRole)
        if not record:
            return
        
        filename = record.get('filename', '该文件')
        reply = QMessageBox.question(
            self.mw, '确认删除',
            f'确定要删除「{filename}」的历史记录吗？\n\n此操作不会删除原始文件。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            # 从历史记录中移除
            self.mw.pdf_history = [r for r in self.mw.pdf_history 
                                   if r.get('timestamp') != record.get('timestamp') 
                                   or r.get('filename') != record.get('filename')]
            save_pdf_history(self.mw.pdf_history)
            self.refresh_history_list()

    def clear_pdf_history(self):
        """清空历史记录"""
        reply = QMessageBox.question(
            self.mw, '确认清空',
            '确定要清空所有历史记录吗？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.mw.pdf_history = []
            save_pdf_history(self.mw.pdf_history)
            self.refresh_history_list()

    def add_to_history(self, filename, status, total_pages, success_count, file_path):
        """添加历史记录"""
        # 将绝对路径转换为相对于项目根目录的相对路径
        try:
            root = get_project_root()
            relative_path = os.path.relpath(file_path, root)
        except ValueError:
            # 跨驱动器时使用绝对路径
            relative_path = file_path

        record = {
            'filename': filename,
            'status': status,
            'total_pages': total_pages,
            'success_count': success_count,
            'file_path': relative_path,
            'processed_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        # 更新已有记录或添加新记录（使用相对路径比较）
        for i, r in enumerate(self.mw.pdf_history):
            if r.get('file_path') == relative_path:
                self.mw.pdf_history[i] = record
                break
        else:
            self.mw.pdf_history.insert(0, record)

        # 限制历史记录数量（最多100条）
        if len(self.mw.pdf_history) > 100:
            self.mw.pdf_history = self.mw.pdf_history[:100]

        save_pdf_history(self.mw.pdf_history)

        # 如果当前在历史记录Tab，刷新列表
        if hasattr(self.mw, 'tabs') and self.mw.tabs.currentWidget() == self.mw.history_tab:
            self.refresh_history_list()

    def on_history_cell_clicked(self, row, column):
        """单击单元格"""
        pass

    def _get_absolute_path(self, relative_path):
        """将相对路径转换为绝对路径"""
        if not relative_path:
            return None
        # 如果已经是绝对路径，直接返回
        if os.path.isabs(relative_path):
            return relative_path
        # 转换为绝对路径
        root = get_project_root()
        abs_path = os.path.normpath(os.path.join(root, relative_path))
        return abs_path

    def on_preview_button_clicked(self, row):
        """预览按钮点击"""
        if not self.history_table:
            return

        # 文件名现在在第1列
        item = self.history_table.item(row, 1)
        if item:
            record = item.data(Qt.UserRole)
            if record:
                relative_path = record.get('file_path')
                file_path = self._get_absolute_path(relative_path)
                
                if not file_path or not os.path.exists(file_path):
                    QMessageBox.warning(self.mw, "文件不存在", 
                        f"找不到文件：\n{file_path}\n\n可能已被移动或删除。")
                    return
                    
                self.mw.current_file = file_path
                cached_data = load_mid_data(file_path)
                print(f"\n[DEBUG] === 从历史记录加载 ===")
                print(f"[DEBUG] file_path: {file_path}")
                print(f"[DEBUG] cached_data 存在: {cached_data is not None}")
                if cached_data:
                    print(f"[DEBUG] cached_data keys: {list(cached_data.keys())}")
                    print(f"[DEBUG] is_image_pdf: {cached_data.get('is_image_pdf')}")
                    print(f"[DEBUG] image_cache_dir: '{cached_data.get('image_cache_dir', 'NOT_FOUND')}'")
                    print(f"[DEBUG] tables数量: {len(cached_data.get('tables', []))}")
                
                if not cached_data:
                    QMessageBox.warning(self.mw, "缓存失效", 
                        f"该文件的缓存已失效或版本过旧。\n\n"
                        f"文件：{record['filename']}\n"
                        f"路径：{file_path}\n\n"
                        f"请点击「处理」按钮重新解析。")
                    # 仍然更新UI，让用户可以重新处理
                    self.mw.file_label.setText(f"{record['filename']} [缓存失效]")
                    self.mw.file_label.setStyleSheet("color: #E74C3C; font-weight: bold;")
                    self.mw.process_btn.setEnabled(True)
                    return
                    
                self.mw.processed_results = cached_data
                self.mw.file_label.setText(f"{record['filename']} [从历史记录加载]")
                self.mw.file_label.setStyleSheet("color: #3498DB; font-weight: bold;")
                self.mw.process_btn.setEnabled(True)
                self.mw.on_processing_finished(cached_data)

                # 启动加载时间定时器
                self.loading_start_time = time.time()
                self.loading_row = row
                self.loading_timer = QTimer()
                self.loading_timer.timeout.connect(self._update_loading_time)
                self.loading_timer.start(500)

                # 在表格第5列（加载中）显示加载状态
                loading_item = QTableWidgetItem("⏳ 加载中...")
                loading_item.setForeground(QColor('#E67E22'))
                self.history_table.setItem(row, 5, loading_item)
                self.history_table.viewport().repaint()
                QApplication.processEvents()

                # 显示PDF预览区域的加载状态
                if self.mw.preview_manager:
                    self.mw.preview_manager.show_loading("正在生成预览")
                QApplication.processEvents()

                # 更新预览Tab（generate_pdf_preview_images 已在 on_processing_finished 中调用）
                if self.mw.preview_manager:
                    self.mw.preview_manager.update_preview_tab()

                # 切换到对比预览Tab
                if hasattr(self.mw, 'tabs'):
                    self.mw.tabs.setCurrentIndex(1)

                # 停止加载定时器
                if self.loading_timer:
                    self.loading_timer.stop()
                self.loading_start_time = None

                # 更新加载状态列为完成（第5列）
                loading_item = self.history_table.item(row, 5)
                if loading_item:
                    loading_item.setText("✅")
                    loading_item.setForeground(QColor('#27AE60'))

                if self.mw.preview_manager:
                    self.mw.preview_manager.hide_loading()

    def _update_loading_time(self):
        """更新加载时间"""
        if self.loading_start_time is None or self.loading_row < 0:
            return

        elapsed = time.time() - self.loading_start_time
        dots = "." * (int(elapsed) % 4)

        loading_item = self.history_table.item(self.loading_row, 5)
        if loading_item:
            loading_item.setText(f"⏳ 加载中{dots} {elapsed:.1f}s")
            self.history_table.viewport().repaint()
            QApplication.processEvents()

    def on_history_double_click(self, item):
        """历史项双击"""
        if not self.history_table:
            return

        row = self.history_table.row(item)
        if 0 <= row < len(self.mw.pdf_history):
            record = self.mw.pdf_history[row]
            relative_path = record.get('file_path')
            file_path = self._get_absolute_path(relative_path)

            if file_path and os.path.exists(file_path):
                self.mw.current_file = file_path
                cached_data = load_mid_data(file_path)
                if cached_data:
                    self.mw.processed_results = cached_data
                    self.mw.file_label.setText(f"{record['filename']} [已加载]")
                    self.mw.file_label.setStyleSheet("color: #3498DB; font-weight: bold;")
                    self.mw.process_btn.setEnabled(True)
                    self.mw.on_processing_finished(cached_data)
                    # 显示加载状态
                    if self.mw.preview_manager:
                        self.mw.preview_manager.show_loading("正在生成预览")
                    QApplication.processEvents()
                    if self.mw.preview_manager:
                        self.mw.preview_manager.generate_pdf_preview_images()
                    if self.mw.preview_manager:
                        self.mw.preview_manager.hide_loading()
                    if hasattr(self.mw, 'tabs'):
                        self.mw.tabs.setCurrentIndex(1)
                else:
                    QMessageBox.warning(self.mw, "缓存已删除", "该文件缓存已失效，请重新处理")

    def on_history_click(self, item):
        """历史项单击"""
        if not self.history_table:
            return

        row = self.history_table.row(item)
        if 0 <= row < len(self.mw.pdf_history):
            record = self.mw.pdf_history[row]
            info = f"文件: {record.get('filename', '未知')}\n"
            info += f"状态: {record.get('status', '未知')}\n"
            info += f"时间: {record.get('timestamp', '未知')}\n"
            info += f"成功: {record.get('success_count', 0)}/{record.get('total_pages', 0)}"
            if self.history_preview:
                self.history_preview.setText(info)
