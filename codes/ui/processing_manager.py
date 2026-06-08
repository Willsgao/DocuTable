"""
PDF解析流程管理模块
处理文件选择、解析启动、进度更新、结果处理等流程
"""
import os
from datetime import datetime

from PyQt5.QtWidgets import QFileDialog, QMessageBox, QApplication
from PyQt5.QtCore import pyqtSignal, QObject, QTimer

from codes.pdf_extractor import (
    get_cached_pdf_info, load_mid_data, save_mid_data, ProcessingWorker
)


class ProcessingManager(QObject):
    """PDF解析流程管理器"""

    # 信号定义
    processing_started = pyqtSignal()
    processing_finished = pyqtSignal(dict)
    processing_error = pyqtSignal(str)
    file_selected = pyqtSignal(str, dict)  # path, cache_info
    progress_updated = pyqtSignal(int, str)  # value, msg

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        # 耗时计时器
        self._elapsed_seconds = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_timer)
        self._elapsed_timer.setInterval(1000)  # 每秒更新

    def select_file(self):
        """选择PDF文件，智能加载缓存"""
        path, _ = QFileDialog.getOpenFileName(
            self.mw, "选择PDF文件", "", "PDF文件 (*.pdf)"
        )
        if not path:
            return

        cache_info = get_cached_pdf_info(path)

        # 检查是否已解析（同一文件再次选择）
        if self.mw.current_file == path and self.mw.processed_results:
            reply = QMessageBox.question(
                self.mw, "文件已解析",
                f"您选择的是已解析过的文件：\n{os.path.basename(path)}\n\n是否要重新解析？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        # 尝试加载有效缓存
        elif cache_info.get('is_valid', False):
            cached_data = load_mid_data(path)
            if cached_data:
                self.mw.current_file = path
                self.mw.processed_results = cached_data
                self.mw.file_label.setText(f"{os.path.basename(path)} [从缓存加载]")
                self.mw.file_label.setStyleSheet("color: #3498DB; font-weight: bold;")
                self.mw.process_btn.setEnabled(True)

                # 生成预览图并更新UI（on_processing_finished中已处理预览图生成）
                self.on_processing_finished(cached_data)

                cache_time = cache_info.get('cached_time', '')
                if cache_time:
                    try:
                        cache_dt = datetime.fromisoformat(cache_time)
                        time_str = cache_dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        time_str = cache_time
                    self.mw.status_bar.showMessage(
                        f"已从缓存加载数据 (缓存时间: {time_str})", 8000
                    )
                else:
                    self.mw.status_bar.showMessage("已从缓存加载数据", 5000)
                return

        # 缓存已失效
        elif cache_info.get('exists'):
            QMessageBox.information(
                self.mw, "缓存已失效",
                f"发现该PDF的历史缓存数据，但PDF文件已被修改（哈希值不匹配）。\n\n"
                f"请点击\"开始处理\"重新解析PDF。", QMessageBox.Ok
            )

        # 新文件或未缓存
        self.mw.current_file = path
        self.mw.file_label.setText(f"{os.path.basename(path)} [待解析]")
        self.mw.file_label.setStyleSheet("color: #E74C3C; font-weight: bold;")
        self.mw.process_btn.setEnabled(True)
        self.mw.status_bar.showMessage(f"已选择: {path}，请点击\"开始处理\"解析PDF", 5000)

    def start_processing(self):
        """开始处理PDF"""
        if not self.mw.current_file:
            QMessageBox.warning(self.mw, "警告", "请先选择PDF文件")
            return

        mode_map = {0: "auto", 1: "text_only", 2: "ai_only"}
        mode = mode_map[self.mw.mode_combo.currentIndex()]
        max_pages = self.mw.max_pages_spin.value()

        if mode in ["auto", "ai_only"] and not self.mw.config.get("doubao_api_key"):
            reply = QMessageBox.question(
                self.mw, "提示",
                "您选择了需要AI识别的模式，但未配置API Key。\n"
                "如果PDF是图片型，可能无法正常处理。\n\n是否继续？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        self.mw.process_btn.setEnabled(False)
        self.mw.progress_bar.setVisible(True)
        self.mw.progress_bar.setValue(0)
        self.mw.preview_text.clear()
        self.mw.preview_text.append("正在处理中，请稍候...\n")

        self._start_timer()  # 开始计时

        self.mw.worker = ProcessingWorker(self.mw.current_file, mode, max_pages)
        self.mw.worker.progress.connect(self.update_progress)
        self.mw.worker.finished.connect(self.on_processing_finished)
        self.mw.worker.error.connect(self.on_processing_error)
        self.mw.worker.warning.connect(self.on_processing_warning)
        self.mw.worker.start()

        self.processing_started.emit()

    def _tick_timer(self):
        """每秒更新耗时显示"""
        self._elapsed_seconds += 1
        m, s = divmod(self._elapsed_seconds, 60)
        self.mw.progress_timer_label.setText(f"⏱ {m}:{s:02d}")

    def _start_timer(self):
        self._elapsed_seconds = 0
        self.mw.progress_timer_label.setVisible(True)
        self.mw.progress_timer_label.setText("⏱ 0:00")
        self._elapsed_timer.start()

    def _stop_timer(self):
        self._elapsed_timer.stop()
        self.mw.progress_timer_label.setVisible(False)

    def update_progress(self, value, msg):
        """更新进度（实时显示全部进度消息）"""
        self.mw.progress_bar.setValue(value)
        self.mw.status_bar.showMessage(msg)
        # 显示所有进度消息，不再仅每10%显示一次
        self.mw.preview_text.append(f"[{value:3d}%] {msg}")

    def on_processing_finished(self, result):
        """处理完成"""
        self._stop_timer()
        self.mw.progress_bar.setValue(100)
        self.mw.process_btn.setEnabled(True)
        self.mw.processed_results = result

        if self.mw.current_file:
            save_mid_data(self.mw.current_file, result)

        # 切换PDF时清空 AI纠错 Tab + 取消正在运行的纠错任务
        if hasattr(self.mw, 'table_compare_manager') and self.mw.table_compare_manager:
            self.mw.table_compare_manager.cancel_ai_correction_worker()
        if hasattr(self.mw, 'ai_correction_tab') and self.mw.ai_correction_tab:
            self.mw.ai_correction_tab.clear_results()
            # 尝试自动加载该PDF的AI纠错缓存
            self._try_load_ai_cache()

        filename = os.path.basename(self.mw.current_file) if self.mw.current_file else "未知文件"
        success_count = result.get('success_count', 0)
        review_count = result.get('review_count', 0)
        total_pages = result.get('total_pages', result.get('total_tables', 0))
        failed_count = result.get('failed_count', 0)

        if result.get('is_image_pdf'):
            status = 'image_pdf'
        elif failed_count == 0 and success_count > 0:
            status = 'success'
        elif success_count > 0 and failed_count > 0:
            status = 'partial'
        else:
            status = 'failed'

        # 添加到历史记录
        if hasattr(self.mw, 'history_manager'):
            self.mw.history_manager.add_to_history(
                filename, status, total_pages, success_count, self.mw.current_file
            )

        # 生成预览图（通过preview_manager）
        if hasattr(self.mw, 'preview_manager') and self.mw.preview_manager:
            self.mw.preview_manager.generate_pdf_preview_images()

        # 更新预览文本（追加摘要，不清除进度日志）
        self.mw.preview_text.append("\n" + "=" * 50)
        self.mw.preview_text.append("✅ 处理完成！\n")
        self.mw.preview_text.append(
            f"PDF类型: {'图片型（扫描件）' if result.get('is_image_pdf') else '文字型（可直接复制）'}\n"
        )
        self.mw.preview_text.append("页面统计:\n")
        self.mw.preview_text.append(f"  总页面数: {total_pages}\n")
        self.mw.preview_text.append(f"  ✅ 可信表格: {success_count} 个\n")
        if review_count > 0:
            self.mw.preview_text.append(f"  🔍 待复核: {review_count} 个\n")
        
        empty_count = result.get('empty_count', 0)
        if empty_count > 0:
            self.mw.preview_text.append(f"  ⚠️ 空数据（需配置API Key）: {empty_count} 个\n")
        
        self.mw.preview_text.append(f"  ❌ 已拒绝/失败: {failed_count} 个\n\n")

        if failed_count > 0:
            self.mw.preview_text.append(f"💡 提示: 有 {failed_count} 个页面未成功解析。\n")
            self.mw.preview_text.append("   请切换到「对比预览」Tab查看失败页面。\n\n")
        else:
            self.mw.preview_text.append("🎉 所有页面均成功解析！\n\n")

        # 显示自动分割统计
        seg_report = result.get('segmentation_report', {})
        before_count = len(result.get('tables_before_segmentation', []))
        after_count = len(result.get('tables', []))
        if before_count > 0 and after_count != before_count:
            real_count = sum(1 for t in result.get('tables', []) if t.get('quality_decision') == 'accepted')
            review_tab_count = sum(1 for t in result.get('tables', []) if t.get('quality_decision') == 'review')
            rej_count = sum(1 for t in result.get('tables', []) if t.get('quality_decision') == 'rejected')
            self.mw.preview_text.append("=" * 50 + "\n")
            self.mw.preview_text.append("📊 自动表格分割优化:\n")
            self.mw.preview_text.append(f"  原始表格数: {before_count}\n")
            self.mw.preview_text.append(f"  分割后表格数: {after_count}\n")
            self.mw.preview_text.append(f"  💰 可信财务表: {real_count}\n")
            if review_tab_count > 0:
                self.mw.preview_text.append(f"  🔍 待复核: {review_tab_count}\n")
            if rej_count > 0:
                self.mw.preview_text.append(f"  ❌ 已拒绝: {rej_count}\n")
            if seg_report.get("cross_page_merges", 0) > 0:
                self.mw.preview_text.append(f"  🔗 跨页拼接: {seg_report['cross_page_merges']} 处\n")
            self.mw.preview_text.append("\n")

        if result.get('tables'):
            self.mw.preview_text.append("=" * 50 + "\n")
            self.mw.preview_text.append("成功解析的表格预览:\n\n")

            success_tables = [t for t in result['tables'] if t.get('quality_decision') == 'accepted']
            for i, table in enumerate(success_tables[:5]):
                self.mw.preview_text.append(f"【第 {table['page']} 页】")
                data = table.get('data', [])
                for row in data[:10]:
                    row_str = " | ".join([str(c)[:20] for c in row[:5]])
                    self.mw.preview_text.append(f"  {row_str}")
                if len(data) > 10:
                    self.mw.preview_text.append(f"  ... (还有 {len(data) - 10} 行)")
                self.mw.preview_text.append("")

            if len(success_tables) > 5:
                self.mw.preview_text.append(f"\n（还有 {len(success_tables) - 5} 个表格未显示）")

            if hasattr(self.mw, 'preview_manager') and self.mw.preview_manager:
                self.mw.preview_manager.update_preview_tab()

        self.mw.export_btn.setEnabled(True)
        if hasattr(self.mw, 'goto_export_btn'):
            self.mw.goto_export_btn.setEnabled(True)

        if failed_count > 0 or review_count > 0:
            parts = [f"可信: {success_count}"]
            if review_count > 0:
                parts.append(f"待复核: {review_count}")
            if failed_count > 0:
                parts.append(f"已拒绝: {failed_count}")
            self.mw.status_bar.showMessage(
                f"处理完成！{', '.join(parts)}，请查看对比预览"
            )
        else:
            self.mw.status_bar.showMessage("处理完成！所有表格均可信")

        self.processing_finished.emit(result)

    def _try_load_ai_cache(self):
        """切换PDF后自动尝试加载已有的AI纠错缓存"""
        if not self.mw.current_file:
            return
        try:
            from codes.pdf_extractor import load_ai_correction_cache
            cached = load_ai_correction_cache(self.mw.current_file)
            if cached and hasattr(self.mw, 'ai_correction_tab') and self.mw.ai_correction_tab:
                self.mw.ai_correction_tab.set_results(
                    cached, self.mw.processed_results
                )
                print(f"[AI CACHE] 自动加载缓存的AI纠错结果 ({len(cached)} 张表)")
        except Exception as e:
            print(f"[AI CACHE] 自动加载失败: {e}")

    def on_processing_error(self, error_msg):
        """处理错误"""
        self._stop_timer()
        self.mw.progress_bar.setVisible(False)
        self.mw.process_btn.setEnabled(True)
        QMessageBox.critical(self.mw, "处理失败", f"处理PDF时发生错误:\n{error_msg}")
        self.processing_error.emit(error_msg)

    def on_processing_warning(self, warning_msg):
        """处理警告"""
        self.mw.preview_text.append(f"⚠️ {warning_msg}\n")
        self.mw.status_bar.showMessage(warning_msg, 5000)

    def generate_pdf_preview_images(self):
        """生成PDF预览图片（每个PDF独立目录）"""
        if not self.mw.current_file or not self.mw.processed_results:
            return

        from codes.pdf_extractor.utils import get_pdf_preview_dir
        import fitz

        preview_dir = get_pdf_preview_dir(self.mw.current_file)
        os.makedirs(preview_dir, exist_ok=True)

        # 检查是否已有缓存（Worker 已预渲染，通常命中）
        try:
            cached_files = [
                f for f in os.listdir(preview_dir)
                if f.startswith("preview_") and f.endswith(".png")
            ]
        except Exception:
            cached_files = []

        def _extract_page_num(filename):
            try:
                return int(filename[len("preview_"):-len(".png")])
            except ValueError:
                return -1
        cached_files.sort(key=_extract_page_num)
        if cached_files:
            self.mw.preview_images = [
                os.path.join(preview_dir, f) for f in cached_files
            ]
            print(f"  [processing] 预览缓存命中，共 {len(cached_files)} 张")
            return

        # 降级：缓存未命中时才逐页渲染（一次打开，避免重复 open）
        print(f"  [processing] 预览缓存未命中，降级渲染...")
        try:
            doc = fitz.open(self.mw.current_file)
            total_pages = len(doc)
        except Exception as e:
            self.mw.status_bar.showMessage(f"获取PDF页数失败: {e}")
            return

        self.mw.preview_images = []
        mat = fitz.Matrix(2.0, 2.0)
        for page_num in range(total_pages):
            image_path = os.path.join(preview_dir, f"preview_{page_num}.png")
            try:
                page = doc.load_page(page_num)
                pix = page.get_pixmap(matrix=mat)
                pix.save(image_path)
                self.mw.preview_images.append(image_path)
            except Exception as e:
                self.mw.preview_images.append(None)
                print(f"[WARN] 生成第{page_num + 1}页预览失败: {e}")
        doc.close()

        self.mw.status_bar.showMessage(f"预览图已生成，共 {total_pages} 页", 3000)

    def update_preview_tab(self):
        """更新预览Tab显示"""
        if not self.mw.processed_results:
            return

        tables = self.mw.processed_results.get('tables', [])

        # 按页面分组
        pages_with_tables = {}
        for t in tables:
            page = t.get('page', 0)
            if page not in pages_with_tables:
                pages_with_tables[page] = []
            pages_with_tables[page].append(t)

        # 更新表格列表
        if hasattr(self.mw, 'table_compare_manager'):
            self.mw.table_compare_manager.update_table_list(pages_with_tables)

    def reparse_current_page(self):
        """重新解析当前页面（预留）"""
        self.mw.status_bar.showMessage("AI重识别功能开发中...")
