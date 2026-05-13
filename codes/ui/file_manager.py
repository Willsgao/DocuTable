"""
文件管理模块
处理文件选择、缓存管理等功能
"""
import os

from PyQt5.QtWidgets import QListWidget, QMessageBox

from codes.pdf_extractor import (
    get_all_cached_files, delete_cache_file, TEMP_DIR
)


class FileManager:
    """文件管理器"""

    def __init__(self, main_window):
        self.mw = main_window
        self.cache_list_widget = None
        self.cache_stats_label = None

    def setup_cache_tab(self, cache_list_widget, cache_stats_label):
        """注入缓存Tab的控件引用"""
        self.cache_list_widget = cache_list_widget
        self.cache_stats_label = cache_stats_label

    def refresh_cache_list(self):
        """刷新缓存列表"""
        cached_files = get_all_cached_files()

        if self.cache_list_widget:
            self.cache_list_widget.clear()

        if not cached_files:
            if self.cache_stats_label:
                self.cache_stats_label.setText("📦 暂无缓存文件")
            return

        total_size = sum(f.get('cache_size', 0) for f in cached_files)
        if self.cache_stats_label:
            self.cache_stats_label.setText(
                f"📦 缓存文件: {len(cached_files)} 个, 总计: {self._format_size(total_size)}"
            )

        if self.cache_list_widget:
            for f in cached_files:
                filename = os.path.basename(f.get('pdf_path', '未知'))
                self.cache_list_widget.addItem(
                    f"{filename} ({self._format_size(f.get('cache_size', 0))})"
                )

    def _format_size(self, size):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def delete_selected_cache(self):
        """删除选中缓存"""
        if not self.cache_list_widget:
            return

        current_row = self.cache_list_widget.currentRow()
        if current_row < 0:
            return

        cached_files = get_all_cached_files()
        if current_row < len(cached_files):
            delete_cache_file(cached_files[current_row]['cache_file'])
            self.refresh_cache_list()

    def delete_all_cache(self):
        """清空所有缓存"""
        reply = QMessageBox.question(
            self.mw, '确认清空',
            '确定要清空所有缓存文件吗？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            cached_files = get_all_cached_files()
            for f in cached_files:
                delete_cache_file(f['cache_file'])
            self.refresh_cache_list()

    def cleanup_temp_files(self):
        """清理临时文件"""
        from codes.pdf_extractor import cleanup_temp_files
        cleanup_temp_files()
