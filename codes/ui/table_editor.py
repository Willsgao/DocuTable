"""
表格编辑器模块
处理表格的编辑、撤销/重做、复制粘贴等功能
"""
from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem, QMenu
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont


class TableEditor:
    """表格编辑器管理器"""
    
    def __init__(self, main_window):
        self.main_window = main_window
        self.table_widget = main_window.native_table_widget
        self._wrap_enabled = False  # 自动换行状态
        
    def setup_table_connections(self):
        """设置表格信号连接"""
        if self.table_widget:
            self.table_widget.cellClicked.connect(self.main_window.on_table_selected)
            self.table_widget.cellChanged.connect(self.main_window.on_cell_changed)
            # 安装事件过滤器用于捕获双击
            self.table_widget.viewport().installEventFilter(self.main_window)
            
    def insert_row_above(self):
        """在当前行上方插入行"""
        current_row = self.table_widget.currentRow()
        if current_row >= 0:
            self.save_current_state()
            self.table_widget.insertRow(current_row)
            self.update_column_widths()
            
    def insert_row_below(self):
        """在当前行下方插入行"""
        current_row = self.table_widget.currentRow()
        self.save_current_state()
        self.table_widget.insertRow(current_row + 1 if current_row >= 0 else 0)
        self.update_column_widths()
        
    def insert_col_left(self):
        """在当前列左侧插入列"""
        current_col = self.table_widget.currentColumn()
        self.save_current_state()
        self.table_widget.insertColumn(current_col if current_col >= 0 else 0)
        self.update_column_widths()
        
    def insert_col_right(self):
        """在当前列右侧插入列"""
        current_col = self.table_widget.currentColumn()
        self.save_current_state()
        self.table_widget.insertColumn(current_col + 1 if current_col >= 0 else 0)
        self.update_column_widths()
        
    def delete_selected_rows(self):
        """删除选中的行"""
        selected_rows = set(item.row() for item in self.table_widget.selectedItems())
        if selected_rows:
            self.save_current_state()
            # 从大到小排序，避免删除后索引变化
            for row in sorted(selected_rows, reverse=True):
                self.table_widget.removeRow(row)
                
    def delete_selected_columns(self):
        """删除选中的列"""
        selected_cols = set(item.column() for item in self.table_widget.selectedItems())
        if selected_cols:
            self.save_current_state()
            for col in sorted(selected_cols, reverse=True):
                self.table_widget.removeColumn(col)
                
    def save_current_state(self):
        """保存当前表格状态用于撤销"""
        self.main_window.save_current_table_state()
        
    def update_column_widths(self):
        """更新列宽以适应内容"""
        if self.table_widget:
            self.table_widget.resizeColumnsToContents()
            
    def show_context_menu(self, position):
        """显示右键菜单"""
        menu = QMenu()
        
        # 撤销/重做
        undo_action = menu.addAction("↩️ 撤销")
        undo_action.triggered.connect(self.main_window.undo_change)
        undo_action.setEnabled(len(self.main_window.undo_stack) > 0)
        
        redo_action = menu.addAction("↪️ 重做")
        redo_action.triggered.connect(self.main_window.redo_change)
        redo_action.setEnabled(len(self.main_window.redo_stack) > 0)
        
        menu.addSeparator()
        
        # 插入操作
        insert_row_above = menu.addAction("⬆️ 上方插入行")
        insert_row_above.triggered.connect(self.insert_row_above)
        
        insert_row_below = menu.addAction("⬇️ 下方插入行")
        insert_row_below.triggered.connect(self.insert_row_below)
        
        insert_col_left = menu.addAction("⬅️ 左侧插入列")
        insert_col_left.triggered.connect(self.insert_col_left)
        
        insert_col_right = menu.addAction("➡️ 右侧插入列")
        insert_col_right.triggered.connect(self.insert_col_right)
        
        menu.addSeparator()
        
        # 删除操作
        delete_rows = menu.addAction("🗑️ 删除选中行")
        delete_rows.triggered.connect(self.delete_selected_rows)
        
        delete_cols = menu.addAction("🗑️ 删除选中列")
        delete_cols.triggered.connect(self.delete_selected_columns)
        
        menu.addSeparator()
        
        # 复制粘贴
        copy_action = menu.addAction("📋 复制")
        copy_action.triggered.connect(self.main_window.copy_from_table)
        
        paste_action = menu.addAction("📄 粘贴")
        paste_action.triggered.connect(self.main_window.paste_to_table)
        
        cut_action = menu.addAction("✂️ 剪切")
        cut_action.triggered.connect(self.main_window.cut_from_table)
        
        menu.addSeparator()
        
        # 自动换行
        wrap_label = "↩️ 自动换行 ✓" if self._wrap_enabled else "↩️ 自动换行"
        wrap_action = menu.addAction(wrap_label)
        wrap_action.triggered.connect(self._toggle_wrap_text)
        
        menu.exec_(self.table_widget.mapToGlobal(position))
    
    def _toggle_wrap_text(self):
        """打开/关闭单元格文本自动换行（仅影响选中列）"""
        selected_cols = set()
        for item in self.table_widget.selectedItems():
            selected_cols.add(item.column())
        if not selected_cols:
            col = self.table_widget.currentColumn()
            if col >= 0:
                selected_cols.add(col)
        if not selected_cols:
            self._wrap_enabled = not self._wrap_enabled
        else:
            self._wrap_enabled = not self._wrap_enabled

        if self._wrap_enabled:
            # 按列宽计算每行最大字符，插入换行
            for col in range(self.table_widget.columnCount()):
                if selected_cols and col not in selected_cols:
                    continue
                col_w = max(20, self.table_widget.columnWidth(col))
                # 按中文字宽估算（约 14px/字）
                max_chars = max(5, int(col_w / 14))
                for row in range(self.table_widget.rowCount()):
                    item = self.table_widget.item(row, col)
                    if not item or not item.text():
                        continue
                    text = item.text()
                    if len(text) > max_chars:
                        wrapped = ""
                        for i in range(0, len(text), max_chars):
                            wrapped += text[i:i + max_chars] + "\n"
                        item.setText(wrapped.rstrip('\n'))
        else:
            # 取消换行：移除所有 \n
            for col in range(self.table_widget.columnCount()):
                if selected_cols and col not in selected_cols:
                    continue
                for row in range(self.table_widget.rowCount()):
                    item = self.table_widget.item(row, col)
                    if item and '\n' in (item.text() or ''):
                        item.setText(item.text().replace('\n', ''))

        self.table_widget.resizeRowsToContents()
