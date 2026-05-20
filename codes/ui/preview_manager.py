"""
预览管理模块
处理PDF预览、缩放、加载动画等功能
"""
import os

from PyQt5.QtWidgets import QLabel, QApplication
from PyQt5.QtCore import Qt, QTimer, pyqtSignal

from codes.pdf_extractor import ZoomableScrollArea, PDFPreviewWidget, get_pdf_preview_dir, TEMP_DIR


class PreviewManager:
    """预览管理器"""

    zoom_changed = pyqtSignal(float)

    def __init__(self, main_window):
        self.mw = main_window
        self.current_page = 0

        # 清理旧的 TEMP_DIR 预览图（已迁移到 mid_cache 按文件管理）
        try:
            for f in os.listdir(TEMP_DIR):
                if f.startswith("preview_") and f.endswith(".png"):
                    os.remove(os.path.join(TEMP_DIR, f))
            marker = os.path.join(TEMP_DIR, "preview_file_marker.txt")
            if os.path.exists(marker):
                os.remove(marker)
        except:
            pass

        # 预览控件
        self.pdf_scroll_area = None
        self.pdf_preview_widget = None
        self.pdf_loading_label = None

        # 加载动画定时器
        self.pdf_loading_timer = QTimer()
        self.pdf_loading_timer.timeout.connect(self._update_loading_animation)
        self.pdf_loading_dots = 0

    def setup_pdf_preview(self, parent_widget):
        """设置PDF预览区域，返回scroll_area供调用者添加到布局"""
        self.pdf_scroll_area = ZoomableScrollArea()
        self.pdf_scroll_area.setWidgetResizable(False)
        self.pdf_scroll_area.setAlignment(Qt.AlignCenter)
        self.pdf_scroll_area.setMinimumHeight(200)
        self.pdf_scroll_area.setSizePolicy(
            self.mw.sizePolicy().horizontalPolicy(),
            self.mw.sizePolicy().verticalPolicy()
        )
        self.pdf_scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #f5f5f5;
            }
        """)

        self.pdf_preview_widget = PDFPreviewWidget()
        self.pdf_scroll_area.setWidget(self.pdf_preview_widget)

        # 连接缩放信号
        self.pdf_scroll_area.zoomChanged.connect(self.on_zoom_changed)

        return self.pdf_scroll_area

    def setup_pdf_loading_label(self):
        """设置加载状态标签，返回label供调用者添加到布局"""
        self.pdf_loading_label = QLabel("")
        self.pdf_loading_label.setStyleSheet("""
            QLabel {
                color: #5D6D7E;
                font-size: 12px;
                font-weight: bold;
                padding: 8px 16px;
                background-color: #f8f9fa;
                border-radius: 4px;
                border: 1px solid #BDC3C7;
            }
        """)
        self.pdf_loading_label.setAlignment(Qt.AlignCenter)
        self.pdf_loading_label.setVisible(False)

        return self.pdf_loading_label

    # ==================== 加载动画 ====================

    def show_loading(self, message="加载中"):
        """显示加载状态"""
        if self.pdf_loading_label:
            self.pdf_loading_label.setText(f"⏳ {message}...")
            self.pdf_loading_label.setVisible(True)
        self.pdf_loading_dots = 0
        self.pdf_loading_timer.start(300)

    def hide_loading(self):
        """隐藏加载状态"""
        self.pdf_loading_timer.stop()
        if self.pdf_loading_label:
            self.pdf_loading_label.setVisible(False)

    def _update_loading_animation(self):
        """更新加载动画"""
        if not self.pdf_loading_label:
            return
        text = self.pdf_loading_label.text()
        base_text = text.rsplit(".", 1)[0] if "." in text else text
        dots = "." * (self.pdf_loading_dots % 4)
        self.pdf_loading_label.setText(base_text + dots)
        self.pdf_loading_dots += 1

    # ==================== 缩放 ====================

    def on_zoom_changed(self, zoom_factor):
        """缩放改变时更新状态栏（供缩放信号连接）"""
        if hasattr(self.mw, 'status_bar'):
            mode = "自适应" if self.pdf_preview_widget and self.pdf_preview_widget._auto_fit else "手动"
            self.mw.status_bar.showMessage(f"缩放: {int(zoom_factor * 100)}% ({mode})")

    def set_zoom(self, factor):
        """设置缩放比例"""
        if self.pdf_scroll_area:
            self.pdf_scroll_area.set_zoom(factor)

    # ==================== 预览图片生成 ====================

    def generate_pdf_preview_images(self):
        """生成PDF预览图片（每个PDF独立的缓存目录，永不自动删除）"""
        print(f"\n[DEBUG] === generate_pdf_preview_images 开始 ===")
        print(f"[DEBUG] current_file: {self.mw.current_file}")
        print(f"[DEBUG] processed_results 是否存在: {self.mw.processed_results is not None}")
        
        if not self.mw.current_file or not self.mw.processed_results:
            print(f"[DEBUG] 提前返回：current_file 或 processed_results 为空")
            return

        preview_dir = get_pdf_preview_dir(self.mw.current_file)
        print(f"[DEBUG] preview_dir: {preview_dir}")
        os.makedirs(preview_dir, exist_ok=True)

        # 检查磁盘上是否已有预览图（每个PDF独立目录，直接通过目录名判断）
        try:
            cached_files = [
                f for f in os.listdir(preview_dir)
                if f.startswith("preview_") and f.endswith(".png")
            ]
        except Exception as e:
            print(f"[DEBUG] 读取preview_dir失败: {e}")
            cached_files = []
        print(f"[DEBUG] preview_dir中的文件数: {len(cached_files)}")
        
        # 按数字顺序排序，避免字符串排序导致 preview_10 排在 preview_2 前面
        def _extract_page_num(filename):
            try:
                return int(filename[len("preview_"):-len(".png")])
            except ValueError:
                return -1
        cached_files.sort(key=_extract_page_num)
        if cached_files:
            print(f"[DEBUG] 使用preview_dir中的缓存文件")
            self.mw.preview_images = [
                os.path.join(preview_dir, f) for f in cached_files
            ]
            print(f"[DEBUG] preview_images 已设置，共 {len(self.mw.preview_images)} 张")
            return
        
        # 如果是图片型PDF且有图片缓存目录，使用图片缓存
        image_cache_dir = self.mw.processed_results.get('image_cache_dir', '')
        print(f"[DEBUG] image_cache_dir from processed_results: '{image_cache_dir}'")
        print(f"[DEBUG] processed_results 是否有 is_image_pdf: {self.mw.processed_results.get('is_image_pdf')}")
        
        if image_cache_dir and os.path.isdir(image_cache_dir):
            print(f"[DEBUG] image_cache_dir 存在且是目录")
            try:
                cached_files = [
                    f for f in os.listdir(image_cache_dir)
                    if f.startswith("page_") and f.endswith(".png")
                ]
                print(f"[DEBUG] image_cache_dir中的文件数: {len(cached_files)}")
            except Exception as e:
                print(f"[DEBUG] 读取image_cache_dir失败: {e}")
                cached_files = []
            
            # 修正排序函数（图片缓存使用 page_ 前缀）
            def _extract_page_num_for_cache(filename):
                try:
                    return int(filename[len("page_"):-len(".png")])
                except ValueError:
                    return -1
            cached_files.sort(key=_extract_page_num_for_cache)
            if cached_files:
                print(f"[DEBUG] 使用image_cache_dir中的缓存文件")
                self.mw.preview_images = [
                    os.path.join(image_cache_dir, f) for f in cached_files
                ]
                print(f"[DEBUG] preview_images 已设置，共 {len(self.mw.preview_images)} 张")
                return
            else:
                # 图片缓存目录存在但是空的，记录警告
                print(f"[WARN] 图片缓存目录存在但是空的: {image_cache_dir}")
        
        # 如果没有预览图也没有图片缓存，尝试生成预览图
        print(f"[DEBUG] 准备用PyMuPDF生成预览图")
        if not cached_files:
            # 降级：缓存未命中时才逐页渲染（一次打开，避免重复 open）
            print(f"[DEBUG] 预览缓存未命中，降级渲染...")
            try:
                import fitz
                doc = fitz.open(self.mw.current_file)
                total_pages = len(doc)
                print(f"[DEBUG] PDF总页数: {total_pages}")
            except Exception as e:
                self.mw.status_bar.showMessage(f"获取PDF页数失败: {e}")
                print(f"[DEBUG] 获取页数失败: {e}")
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
                    print(f"[DEBUG] 第{page_num}页生成成功: {image_path}")
                except Exception as e:
                    self.mw.preview_images.append(None)
                    print(f"[WARN] 生成第{page_num + 1}页预览失败: {e}")
            doc.close()

        print(f"[DEBUG] 最终 preview_images 数量: {len(self.mw.preview_images)}")
        print(f"[DEBUG] === generate_pdf_preview_images 结束 ===\n")
        self.mw.status_bar.showMessage(f"预览图已生成，共 {total_pages} 页", 3000)

    # ==================== 预览Tab更新 ====================

    def update_preview_tab(self):
        """更新预览Tab显示（在processing完成后调用）"""
        if not self.mw.processed_results:
            return

        tables = self.mw.processed_results.get('tables', [])

        # 初始化页面类型标记
        for table in tables:
            if 'is_table' not in table:
                data = table.get('data', [])
                table['is_table'] = len(data) > 0

        # 重置筛选为"全部"，避免上次的筛选条件在新PDF中匹配不到任何表格
        if (self.mw.table_compare_manager and
            hasattr(self.mw.table_compare_manager, 'table_type_filter')):
            self.mw.table_compare_manager.table_type_filter.blockSignals(True)
            self.mw.table_compare_manager.table_type_filter.setCurrentIndex(0)
            self.mw.table_compare_manager.table_type_filter.blockSignals(False)

        # 委托给table_compare_manager应用筛选和刷新列表
        if self.mw.table_compare_manager:
            self.mw.table_compare_manager.apply_table_filter()

    # ==================== 预览导航 ====================

    def update_preview(self, page_index):
        """更新预览显示（委托给table_compare_manager处理）"""
        if self.mw.table_compare_manager:
            self.mw.table_compare_manager.update_preview_display()

    def prev_page(self):
        """上一页（委托给main.py的导航逻辑）"""
        if self.mw.table_compare_manager:
            self.mw.table_compare_manager.prev_filtered_page()

    def next_page(self):
        """下一页（委托给main.py的导航逻辑）"""
        if self.mw.table_compare_manager:
            self.mw.table_compare_manager.next_filtered_page()
