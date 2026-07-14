"""
导出管理模块
处理Excel导出等功能
"""
import gc
import os

from PyQt5.QtWidgets import QFileDialog, QMessageBox

from codes.pdf_extractor import ExcelExporter


class ExportManager:
    """导出管理器"""

    def __init__(self, main_window):
        self.mw = main_window

    def export_to_excel(self):
        """导出到Excel"""
        if not self.mw.processed_results:
            QMessageBox.warning(self.mw, "警告", "没有可导出的数据")
            return

        tables = self.mw.processed_results.get('tables', [])
        # 只导出：人工标记为表格的 + 分类为财务数据表的（且解析成功的）
        success_tables = [t for t in tables
                          if t.get('parse_status') == 'success'
                          and (t.get('manual_mark') == 'table'
                               or t.get('table_category') in ('财务数据表', '数据表(缺表头)'))]
        empty_tables = [t for t in tables if t.get('parse_status') == 'empty']

        if not success_tables and not empty_tables:
            QMessageBox.warning(self.mw, "警告",
                                "没有可导出的表格数据\n"
                                "（导出规则：人工标记为「人工表格」+「财务数据表 / 数据表(缺表头)」）")
            return

        # 如果有空数据但没有成功数据，提示用户
        if not success_tables and empty_tables:
            reply = QMessageBox.question(
                self.mw, "确认导出",
                f"所有页面都是空数据（{len(empty_tables)} 页），是否仍要导出？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
            export_tables = empty_tables
        else:
            export_tables = success_tables

        filename = os.path.splitext(os.path.basename(self.mw.current_file))[0]
        file_path, _ = QFileDialog.getSaveFileName(
            self.mw, "导出Excel",
            f"{filename}_tables.xlsx",
            "Excel文件 (*.xlsx);;所有文件 (*.*)"
        )

        if file_path:
            self._do_export(export_tables, tables, file_path)

    def batch_export_tables(self):
        """批量导出"""
        self.export_to_excel()

    def _do_export(self, table_pages, tables, output_path):
        """执行导出"""
        try:
            # 导出期间暂停 GC，防止 C 扩展 refcount 冲突导致闪退
            gc.disable()
            exporter = ExcelExporter()
            success = exporter.export_tables(table_pages, output_path)

            if success:
                finance_count = sum(1 for t in table_pages if t.get('table_category') == '财务数据表')
                manual_count = sum(1 for t in table_pages if t.get('table_category') != '财务数据表')
                QMessageBox.information(self.mw, "导出成功",
                    f"文件已导出到:\n{output_path}\n\n"
                    f"共 {len(table_pages)} 页表格"
                    f"（财务数据表: {finance_count}，人工标记: {manual_count}）")
            else:
                QMessageBox.warning(self.mw, "导出失败", "导出过程中出现问题")
        except Exception as e:
            QMessageBox.critical(self.mw, "导出失败", f"导出时发生错误:\n{str(e)}")
        finally:
            gc.enable()

    def export_failed_pages_list(self):
        """导出失败页面列表"""
        if not self.mw.processed_results:
            return

        tables = self.mw.processed_results.get('tables', [])
        failed_tables = [t for t in tables if t.get('parse_status') != 'success']

        if not failed_tables:
            QMessageBox.information(self.mw, "提示", "没有失败的页面")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self.mw, "导出失败列表",
            "failed_pages.txt", "文本文件 (*.txt);;所有文件 (*.*)"
        )

        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                for table in failed_tables:
                    f.write(f"第{table['page']}页 - {table.get('page_type', 'unknown')}\n")
            QMessageBox.information(self.mw, "导出成功", f"已导出到:\n{file_path}")
