# -*- coding: utf-8 -*-
"""
UI组件模块 - PDF预览、表格编辑等组件
"""

import os
from PyQt5.QtWidgets import (
    QWidget, QScrollArea, QMenu, QMessageBox, QApplication,
    QTableWidget, QTableWidgetItem, QHeaderView, QAction, QLineEdit
)
from PyQt5.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt5.QtGui import QPainter, QPen, QColor, QPixmap


class PDFPreviewWidget(QWidget):
    """
    PDF预览组件：支持原生文本选择复制和Ctrl+滚轮缩放
    使用PyMuPDF精确提取PDF文本，提供类似PDF阅读器的复制体验
    支持：
    - 拖动框选复制文字
    - 单击选中单元格并高亮显示
    """

    # 单击选中区域变化信号
    clicked_rect_changed = pyqtSignal(QRect)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_pixmap = None
        self.original_pixmap = None
        self.current_image_path = None
        self.current_page = None
        self.current_doc = None
        self.current_page_num = -1
        self.scale_factor = 1.0
        self.zoom_factor = 1.0
        self._auto_fit = True  # 默认自适应

        self.is_selecting = False
        self.selection_start = None
        self.selection_end = None
        self.selection_rect = QRect()

        # 单击选中相关
        self.clicked_rect = QRect()
        self._drag_start = None
        self._min_drag_distance = 5  # 最小拖动距离阈值

        self.setMouseTracking(True)
        self.setCursor(Qt.IBeamCursor)

        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), Qt.white)
        self.setPalette(p)

    def set_zoom(self, factor):
        """设置缩放比例"""
        self._auto_fit = False  # 手动缩放时关闭自适应
        self.zoom_factor = factor
        self._apply_zoom()

    def _apply_zoom(self):
        """应用缩放并更新尺寸"""
        if self.original_pixmap and not self.original_pixmap.isNull():
            scaled_w = int(self.original_pixmap.width() * self.zoom_factor)
            scaled_h = int(self.original_pixmap.height() * self.zoom_factor)
            self.current_pixmap = self.original_pixmap.scaled(
                scaled_w, scaled_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.setMinimumSize(scaled_w, scaled_h)
        self.update()

    def _auto_fit_to_viewport(self):
        """自动适应视口 - 缩放整页完全可见，无需滚动"""
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        # 获取父级scrollArea的视口大小
        parent = self.parentWidget()
        viewport_w = parent.width() if parent else self.width()
        viewport_h = parent.height() if parent else self.height()
        if hasattr(parent, 'viewport'):
            viewport_w = parent.viewport().width()
            viewport_h = parent.viewport().height()
        orig_w = self.original_pixmap.width()
        orig_h = self.original_pixmap.height()
        if orig_w > 0 and orig_h > 0 and viewport_w > 0 and viewport_h > 0:
            # 取宽度和高度缩放比例中较小的那个，确保整页可见
            scale_w = viewport_w / orig_w
            scale_h = viewport_h / orig_h
            self.zoom_factor = max(0.25, min(scale_w, scale_h) * 0.9)  # 留一点边距
            self._apply_zoom()
        # 自适应完成后关闭，后续只靠手动Ctrl+滚轮缩放
        self._auto_fit = False

    def clear_clicked_rect(self):
        """清除单击选中区域"""
        self.clicked_rect = QRect()
        self.update()
        try:
            self.clicked_rect_changed.emit(QRect())
        except Exception:
            pass

    def set_image(self, pixmap):
        """直接设置 QPixmap"""
        if pixmap:
            self.original_pixmap = pixmap
        else:
            self.original_pixmap = None
        self.current_image_path = None
        self.current_page_num = -1
        self.current_doc = None
        self.current_page = None
        self.scale_factor = 1.0
        self.clear_selection()
        # 重置缩放后重新适应
        self.zoom_factor = 1.0
        self.current_pixmap = None
        self.setMinimumSize(0, 0)
        self._auto_fit = True
        self._auto_fit_to_viewport()

    def set_preview(self, img_path, page_num, scale_factor=1.0, pdf_path=None):
        """设置预览
        Args:
            img_path: 预览图片路径（PNG）
            page_num: 页码
            scale_factor: 缩放因子
            pdf_path: 可选，原始PDF路径（用于文本复制）
        """
        # 关闭旧文档
        self._close_doc()

        self.current_image_path = img_path
        self.current_page_num = page_num
        self.scale_factor = scale_factor
        self.original_pixmap = None
        self.current_doc = None
        self.current_page = None
        self.current_pdf_path = pdf_path

        if img_path and os.path.exists(img_path):
            self.original_pixmap = QPixmap(img_path)

        # 如果提供了PDF路径，加载PDF页面供文本提取
        if pdf_path and os.path.exists(pdf_path):
            try:
                import fitz
                self.current_doc = fitz.open(pdf_path)
                if page_num < len(self.current_doc):
                    self.current_page = self.current_doc[page_num]
                else:
                    print(f"[WARN] set_preview: page_num={page_num} out of range (doc has {len(self.current_doc)} pages)")
            except Exception as e:
                print(f"[DEBUG] 加载PDF页面失败: {e}")

        self.clear_selection()
        # 每次切换页面都强制重置缩放后重新适应
        self.zoom_factor = 1.0
        self.current_pixmap = None
        self.setMinimumSize(0, 0)
        self._auto_fit = True
        self._auto_fit_to_viewport()

    def _close_doc(self):
        """安全关闭PDF文档"""
        if self.current_doc is not None:
            try:
                self.current_doc.close()
            except:
                pass
        self.current_doc = None
        self.current_page = None

    def load_pdf_page(self, pdf_path, page_num, render_pixmap):
        """加载PDF页面用于文本提取"""
        self._close_doc()
        self.current_image_path = None
        self.current_page_num = page_num
        self.current_doc = None
        self.current_page = None
        self.current_pixmap = render_pixmap

        if self.original_pixmap is None:
            self.original_pixmap = render_pixmap

        try:
            import fitz
            self.current_doc = fitz.open(pdf_path)
            if page_num < len(self.current_doc):
                self.current_page = self.current_doc[page_num]
            else:
                print(f"[WARN] load_pdf_page: page_num={page_num} out of range (doc has {len(self.current_doc)} pages)")
        except Exception as e:
            print(f"[DEBUG] 加载PDF页面失败: {e}")

        self.clear_selection()
        self.zoom_factor = 1.0
        self.current_pixmap = None
        self.setMinimumSize(0, 0)
        self._auto_fit = True
        self._auto_fit_to_viewport()

    def clear_selection(self):
        """清空选择"""
        self.is_selecting = False
        self.selection_start = None
        self.selection_end = None
        self.selection_rect = QRect()
        self.update()

    def clear(self):
        """清空预览"""
        self._close_doc()
        self.current_pixmap = None
        self.original_pixmap = None
        self.zoom_factor = 1.0
        self.current_page_num = -1
        self.clear_selection()
        self.update()

    def mousePressEvent(self, event):
        """鼠标按下 - 区分单击和拖动"""
        if event.button() == Qt.LeftButton:
            self._drag_start = event.pos()
            self.is_selecting = True
            self.selection_start = event.pos()
            self.selection_end = event.pos()
            self.selection_rect = QRect(self.selection_start, self.selection_end)
            self.update()

    def mouseMoveEvent(self, event):
        """鼠标移动 - 更新选择框"""
        if self.is_selecting and self._drag_start:
            self.selection_end = event.pos()
            self.selection_rect = QRect(self.selection_start, self.selection_end).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        """鼠标释放 - 区分单击和框选"""
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.is_selecting = False
            self.selection_end = event.pos()
            self.selection_rect = QRect(self.selection_start, self.selection_end).normalized()

            # 计算拖动距离
            drag_distance = (self.selection_end - self._drag_start).manhattanLength()

            if drag_distance < self._min_drag_distance:
                # 单击选中 - 设置选中区域高亮
                self._handle_single_click(self.selection_start)
            elif self.selection_rect.width() < 5 or self.selection_rect.height() < 5:
                # 拖动但区域太小
                self.clear_selection()
                self._show_status_message("选择区域太小，请重新框选更大的区域")
            else:
                # 正常框选 - 复制文字
                self.update()
                self.copy_selection()

            self._drag_start = None

    def _handle_single_click(self, pos):
        """处理单击 - 设置选中区域"""
        if not self.current_pixmap or self.current_pixmap.isNull():
            self._show_status_message("请先加载PDF文件")
            return

        pm_w, pm_h = self.current_pixmap.width(), self.current_pixmap.height()
        widget_w, widget_h = self.width(), self.height()
        offset_x = (widget_w - pm_w) // 2
        offset_y = (widget_h - pm_h) // 2

        # 计算点击位置相对于图片的坐标
        click_x = pos.x() - offset_x
        click_y = pos.y() - offset_y

        # 检查是否点击在图片范围内
        if 0 <= click_x <= pm_w and 0 <= click_y <= pm_h:
            # 设置一个小的选中区域（模拟单元格选中效果）
            cell_size = 50  # 选中区域大小
            self.clicked_rect = QRect(
                max(0, click_x - cell_size // 2),
                max(0, click_y - cell_size // 2),
                cell_size,
                cell_size
            )
            # 确保不超出图片范围
            self.clicked_rect = self.clicked_rect.intersected(QRect(0, 0, pm_w, pm_h))
            self.update()
            try:
                self.clicked_rect_changed.emit(self.clicked_rect)
            except Exception:
                pass
            self._show_status_message(f"已选中区域: ({self.clicked_rect.x()}, {self.clicked_rect.y()})")
        else:
            self.clear_clicked_rect()

    def paintEvent(self, event):
        """绘制事件 - 绘制图像和选择框"""
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        if self.current_pixmap and not self.current_pixmap.isNull():
            pm_w = self.current_pixmap.width()
            pm_h = self.current_pixmap.height()
            widget_w = self.width()
            widget_h = self.height()

            x = (widget_w - pm_w) // 2
            y = (widget_h - pm_h) // 2

            painter.drawPixmap(x, y, self.current_pixmap)
        else:
            painter.setPen(Qt.gray)
            painter.drawText(self.rect(), Qt.AlignCenter, "PDF预览区域\n处理完成后将在此显示")

        if self.selection_rect.isValid() and self.selection_rect.width() > 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(100, 149, 237, 80))
            painter.drawRect(self.selection_rect)

            painter.setPen(QPen(QColor(0, 120, 215), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.selection_rect)

        # 绘制单击选中区域（黄色边框，不同于框选的蓝色）
        if self.clicked_rect.isValid() and not self.clicked_rect.isEmpty():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 0, 60))  # 黄色半透明
            painter.drawRect(self.clicked_rect)

            painter.setPen(QPen(QColor(255, 165, 0), 3))  # 橙色边框，更醒目
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.clicked_rect)

        painter.end()

    def keyPressEvent(self, event):
        """键盘事件 - Ctrl+C 复制"""
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_C:
            self.copy_selection()
            event.accept()
        else:
            super().keyPressEvent(event)

    def copy_selection(self):
        """复制选中的文本"""
        if not self.selection_rect.isValid() or self.selection_rect.width() < 3:
            self._show_status_message("请先用鼠标框选要复制的文字区域")
            return

        if not self.current_page:
            self._show_status_message("PDF文本加载失败，无法复制")
            return

        pm = self.current_pixmap
        if not pm or pm.isNull():
            self._show_status_message("图像未正确加载")
            return

        import fitz
        page_rect = self.current_page.rect

        pm_w, pm_h = pm.width(), pm.height()
        widget_w, widget_h = self.width(), self.height()
        offset_x = (widget_w - pm_w) // 2
        offset_y = (widget_h - pm_h) // 2

        x0 = self.selection_rect.left() - offset_x
        y0 = self.selection_rect.top() - offset_y
        x1 = self.selection_rect.right() - offset_x
        y1 = self.selection_rect.bottom() - offset_y

        x0 = max(0, min(x0, pm_w))
        x1 = max(0, min(x1, pm_w))
        y0 = max(0, min(y0, pm_h))
        y1 = max(0, min(y1, pm_h))

        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0

        if x1 - x0 < 1 or y1 - y0 < 1:
            self._show_status_message("选择区域在图像范围之外")
            return

        print(f"[DEBUG COPY] page_num={self.current_page_num} zoom={self.zoom_factor:.4f}")
        print(f"[DEBUG COPY] sel_rect=({self.selection_rect.left()},{self.selection_rect.top()})-({self.selection_rect.right()},{self.selection_rect.bottom()})")
        print(f"[DEBUG COPY] widget=({widget_w},{widget_h}) offset=({offset_x},{offset_y})")
        print(f"[DEBUG COPY] cur_pixmap=({pm_w},{pm_h}) orig_pixmap=({self.original_pixmap.width() if self.original_pixmap else 0},{self.original_pixmap.height() if self.original_pixmap else 0})")
        print(f"[DEBUG COPY] page_rect=({page_rect.x0:.2f},{page_rect.y0:.2f})-({page_rect.x1:.2f},{page_rect.y1:.2f})")
        if self.current_page:
            print(f"[DEBUG COPY] page_rotation={self.current_page.rotation}")

        if self.zoom_factor != 1.0 and self.original_pixmap and not self.original_pixmap.isNull():
            display_to_original_x = self.original_pixmap.width() / pm_w if pm_w > 0 else 1
            display_to_original_y = self.original_pixmap.height() / pm_h if pm_h > 0 else 1

            print(f"[DEBUG COPY] zoom_path: display_to_original=({display_to_original_x:.4f},{display_to_original_y:.4f})")

            orig_x0 = x0 * display_to_original_x
            orig_y0 = y0 * display_to_original_y
            orig_x1 = x1 * display_to_original_x
            orig_y1 = y1 * display_to_original_y

            print(f"[DEBUG COPY] orig_coords=({orig_x0:.1f},{orig_y0:.1f})-({orig_x1:.1f},{orig_y1:.1f})")

            scale_x = page_rect.width / self.original_pixmap.width() if self.original_pixmap.width() > 0 else 1
            scale_y = page_rect.height / self.original_pixmap.height() if self.original_pixmap.height() > 0 else 1

            pdf_x0 = orig_x0 * scale_x
            pdf_y0 = orig_y0 * scale_y
            pdf_x1 = orig_x1 * scale_x
            pdf_y1 = orig_y1 * scale_y
        else:
            scale_x = page_rect.width / pm.width() if pm.width() > 0 else 1
            scale_y = page_rect.height / pm.height() if pm.height() > 0 else 1

            print(f"[DEBUG COPY] no_zoom_path: scale=({scale_x:.4f},{scale_y:.4f})")

            pdf_x0 = x0 * scale_x
            pdf_y0 = y0 * scale_y
            pdf_x1 = x1 * scale_x
            pdf_y1 = y1 * scale_y

        print(f"[DEBUG COPY] pdf_rect_in=({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f}) scale=({scale_x:.6f},{scale_y:.6f})")
        print(f"[DEBUG COPY] pdf_rect_out=({pdf_x0:.2f},{pdf_y0:.2f})-({pdf_x1:.2f},{pdf_y1:.2f})")

        pdf_rect = fitz.Rect(pdf_x0, pdf_y0, pdf_x1, pdf_y1)

        try:
            # 调试：把框选区域画在页面上保存为图片
            debug_page = self.current_doc.load_page(self.current_page_num)
            debug_pix = debug_page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            debug_annot = debug_page.add_rect_annot(pdf_rect)
            debug_annot.set_colors(stroke=(1, 0, 0), fill=(1, 0, 0))
            debug_annot.set_opacity(0.3)
            debug_annot.update()
            debug_pix2 = debug_page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            import tempfile
            debug_path = os.path.join(tempfile.gettempdir(), f"pdf_copy_debug_page{self.current_page_num}.png")
            debug_pix2.save(debug_path)
            debug_page.delete_annot(debug_annot)
            print(f"[DEBUG COPY] debug_image_saved={debug_path}")

            text = self.current_page.get_text("text", clip=pdf_rect)
            text = text.strip()

            print(f"[DEBUG COPY] extracted_text={repr(text[:100])}")

            if text:
                clipboard = QApplication.clipboard()
                clipboard.setText(text)
                self._show_status_message(f"已复制 {len(text)} 个字符到剪贴板")
            else:
                self._show_status_message("选中区域没有检测到文字（可能是扫描件）")
        except Exception as e:
            self._show_status_message(f"复制失败: {str(e)}")

    def _show_status_message(self, message):
        """显示状态消息"""
        if self.parent() and hasattr(self.parent(), 'status_bar'):
            self.parent().status_bar.showMessage(message, 5000)
        else:
            print(message)

    def copy_all_text(self):
        """复制页面全部文字"""
        if not self.current_page:
            self._show_status_message("PDF文本加载失败，无法复制页面文字")
            return

        try:
            text = self.current_page.get_text("text")
            text = text.strip()

            if text:
                clipboard = QApplication.clipboard()
                clipboard.setText(text)
                self._show_status_message(f"已复制页面全部文字 ({len(text)} 个字符) 到剪贴板")
            else:
                self._show_status_message("当前页面没有可复制的文本（可能是扫描件或图片型PDF）")
        except Exception as e:
            self._show_status_message(f"复制失败: {str(e)}")

    def copy_screenshot(self):
        """复制选中区域的图片到剪贴板"""
        if self.current_pixmap is None or self.current_pixmap.isNull():
            self._show_status_message("当前没有可复制的预览内容")
            return

        if not self.selection_rect.isValid() or self.selection_rect.isEmpty():
            self._show_status_message("请先用鼠标框选要复制的区域")
            return

        try:
            # 计算居中偏移（widget坐标 → pixmap坐标）
            pm_w, pm_h = self.current_pixmap.width(), self.current_pixmap.height()
            orig_w = self.original_pixmap.width() if self.original_pixmap else pm_w
            orig_h = self.original_pixmap.height() if self.original_pixmap else pm_h
            widget_w, widget_h = self.width(), self.height()
            offset_x = (widget_w - pm_w) // 2
            offset_y = (widget_h - pm_h) // 2

            # 先得到选中区域在缩放后 pixmap 上的坐标
            sel_x = self.selection_rect.x() - offset_x
            sel_y = self.selection_rect.y() - offset_y
            sel_w = self.selection_rect.width()
            sel_h = self.selection_rect.height()

            if self.zoom_factor != 1.0 and self.original_pixmap and not self.original_pixmap.isNull():
                # 把缩放后的坐标映射回原始图片，从 original_pixmap 裁剪获得高清截图
                sel_rect = QRect(
                    int(sel_x / self.zoom_factor),
                    int(sel_y / self.zoom_factor),
                    int(sel_w / self.zoom_factor),
                    int(sel_h / self.zoom_factor)
                )
                sel_rect = sel_rect.intersected(QRect(0, 0, orig_w, orig_h))
                cropped_pixmap = self.original_pixmap.copy(sel_rect)
            else:
                # 无缩放，直接从当前 pixmap 裁剪
                sel_rect = QRect(sel_x, sel_y, sel_w, sel_h)
                sel_rect = sel_rect.intersected(QRect(0, 0, pm_w, pm_h))
                cropped_pixmap = self.current_pixmap.copy(sel_rect)

            if sel_rect.isEmpty():
                self._show_status_message("选中区域无效")
                return

            if cropped_pixmap.isNull():
                self._show_status_message("裁剪图片失败")
                return

            clipboard = QApplication.clipboard()
            clipboard.setPixmap(cropped_pixmap)
            self._show_status_message(f"已复制选中区域截图 ({sel_rect.width()}x{sel_rect.height()})，可粘贴使用")
        except Exception as e:
            self._show_status_message(f"复制截图失败: {str(e)}")

    def contextMenuEvent(self, event):
        """右键菜单"""
        menu = QMenu(self)

        copy_sel_action = menu.addAction("复制选中文字")
        copy_sel_action.triggered.connect(self.copy_selection)
        copy_sel_action.setEnabled(self.selection_rect.isValid())

        copy_all_action = menu.addAction("复制页面全部文字")
        copy_all_action.triggered.connect(self.copy_all_text)

        menu.addSeparator()

        screenshot_action = menu.addAction("复制选中区域截图")
        screenshot_action.triggered.connect(self.copy_screenshot)
        has_selection = (self.selection_rect is not None and
                         self.selection_rect.isValid() and
                         not self.selection_rect.isEmpty())
        screenshot_action.setEnabled(has_selection)

        menu.addSeparator()

        clear_action = menu.addAction("清除选择")
        clear_action.triggered.connect(self.clear_selection)

        menu.exec_(event.globalPos())


class ZoomableScrollArea(QScrollArea):
    """支持Ctrl+滚轮缩放的ScrollArea"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom_factor = 1.0
        self.min_zoom = 0.25
        self.max_zoom = 5.0

    def wheelEvent(self, event):
        """处理滚轮事件，支持Ctrl+滚轮缩放"""
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_factor = min(self.zoom_factor * 1.15, self.max_zoom)
            else:
                self.zoom_factor = max(self.zoom_factor / 1.15, self.min_zoom)

            self.zoomChanged.emit(self.zoom_factor)
            # 同时将缩放应用到内部的PDF预览控件
            widget = self.widget()
            if widget and hasattr(widget, 'set_zoom'):
                widget.set_zoom(self.zoom_factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def set_zoom(self, factor):
        """设置缩放"""
        old = self.zoom_factor
        self.zoom_factor = max(self.min_zoom, min(factor, self.max_zoom))
        if self.zoom_factor != old:
            self.zoomChanged.emit(self.zoom_factor)
            widget = self.widget()
            if widget and hasattr(widget, 'set_zoom'):
                widget.set_zoom(self.zoom_factor)

    def reset_zoom(self):
        """重置缩放"""
        self.set_zoom(1.0)

    zoomChanged = pyqtSignal(float)


class ZoomableTableWidget(QTableWidget):
    """可缩放表格组件，支持 Excel 风格操作"""

    # 数据变化信号
    data_about_to_change = pyqtSignal()
    
    # 撤销/重做信号
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom_factor = 1.0
        self.base_font_size = 10
        
        # 拖拽填充
        self._fill_dragging = False
        self._fill_source = None  # (row, col) 来源单元格
        self._fill_handle_size = 8  # 填充柄大小
        self.setMouseTracking(True)
        
        # 启用剪贴板操作
        self.setAcceptDrops(False)

        # 表头设置
        self.setSortingEnabled(False)

        # 表头点击事件：左键选中整列，右键显示筛选菜单
        self.horizontalHeader().setSectionsClickable(True)
        self.horizontalHeader().sectionClicked.connect(self.on_header_clicked)
        self.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.horizontalHeader().customContextMenuRequested.connect(self.on_header_right_clicked)

    # ==================== 缩放 ====================

    def zoom_in(self):
        """放大"""
        self.zoom_factor = min(self.zoom_factor * 1.15, 3.0)
        self._apply_zoom()

    def zoom_out(self):
        """缩小"""
        self.zoom_factor = max(self.zoom_factor / 1.15, 0.3)
        self._apply_zoom()

    def _apply_zoom(self):
        """应用缩放"""
        font = self.font()
        new_size = max(6, min(int(self.base_font_size * self.zoom_factor), 24))
        font.setPointSize(new_size)
        self.setFont(font)
        self.resizeRowsToContents()
        self.resizeColumnsToContents()

    def wheelEvent(self, event):
        """滚轮事件 - Ctrl+滚轮缩放"""
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    # ==================== 拖拽填充（类 Excel 填充柄） ====================

    def _get_fill_handle_rect(self):
        """获取当前选中单元格的填充柄矩形"""
        ranges = self.selectedRanges()
        if not ranges:
            return None
        r = ranges[0]
        # 取选中区域的右下角单元格
        bottom_row = r.bottomRow()
        right_col = r.rightColumn()
        rect = self.visualRect(self.model().index(bottom_row, right_col))
        if rect.isValid():
            handle = QRect(
                rect.right() - self._fill_handle_size,
                rect.bottom() - self._fill_handle_size,
                self._fill_handle_size + 2,
                self._fill_handle_size + 2
            )
            return handle
        return None

    def mousePressEvent(self, event):
        """鼠标按下 - 检测是否点击了填充柄"""
        if event.button() == Qt.LeftButton:
            handle_rect = self._get_fill_handle_rect()
            if handle_rect and handle_rect.contains(event.pos()):
                ranges = self.selectedRanges()
                if ranges:
                    self._fill_dragging = True
                    r = ranges[0]
                    self._fill_source = (r.bottomRow(), r.rightColumn())
                    return  # 阻止默认选中行为
        self._fill_dragging = False
        self._fill_source = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """鼠标移动 - 拖拽填充"""
        if self._fill_dragging and self._fill_source:
            index = self.indexAt(event.pos())
            if index.isValid() and index.row() > self._fill_source[0]:
                # 实时预览
                self._preview_fill(index.row())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """鼠标释放 - 完成填充"""
        if self._fill_dragging and self._fill_source:
            self._fill_dragging = False
            index = self.indexAt(event.pos())
            if index.isValid() and index.row() > self._fill_source[0]:
                self._do_fill(index.row())
            self._fill_source = None
            return
        super().mouseReleaseEvent(event)

    def _preview_fill(self, target_row):
        """预览填充（临时高亮显示填充区域）"""
        # 简单实现：不做额外处理，释放时直接填充
        pass

    def _do_fill(self, target_row):
        """执行向下填充"""
        if not self._fill_source:
            return
        src_row, src_col = self._fill_source
        src_item = self.item(src_row, src_col)
        if not src_item:
            return
        src_text = src_item.text()
        
        self.data_about_to_change.emit()
        for r in range(src_row + 1, target_row + 1):
            for c in range(self.columnCount()):
                item = self.item(r, c)
                if item:
                    item.setText(src_text)
                else:
                    self.setItem(r, c, QTableWidgetItem(src_text))

    # ==================== 剪贴板操作 ====================

    def keyPressEvent(self, event):
        """键盘事件 - 支持 Ctrl+Z/Y/C/V/X, Delete 清空选中格"""
        if event.modifiers() == Qt.ControlModifier:
            if event.key() == Qt.Key_Z:
                self.undo_requested.emit()
                return
            elif event.key() == Qt.Key_Y:
                self.redo_requested.emit()
                return
            elif event.key() == Qt.Key_C:
                self._copy_selection()
                return
            elif event.key() == Qt.Key_V:
                self._paste_clipboard()
                return
            elif event.key() == Qt.Key_X:
                self._cut_selection()
                return
        elif event.key() == Qt.Key_Delete:
            self._clear_selection()
            return
        super().keyPressEvent(event)

    def _copy_selection(self):
        """复制选中区域"""
        selection = self.selectedRanges()
        if not selection:
            return
        r = selection[0]
        rows_text = []
        for row in range(r.topRow(), r.bottomRow() + 1):
            row_texts = []
            for col in range(r.leftColumn(), r.rightColumn() + 1):
                item = self.item(row, col)
                row_texts.append(item.text() if item else "")
            rows_text.append("\t".join(row_texts))
        full_text = "\n".join(rows_text)
        QApplication.clipboard().setText(full_text)

    def _paste_clipboard(self):
        """粘贴剪贴板内容到选中区域"""
        clipboard = QApplication.clipboard().text()
        if not clipboard:
            return
        
        selection = self.selectedRanges()
        if not selection:
            return
        
        rows_data = [row.split("\t") for row in clipboard.split("\n") if row]
        if not rows_data:
            return
        
        r = selection[0]
        start_row = r.topRow()
        start_col = r.leftColumn()
        
        self.data_about_to_change.emit()
        for ri, row_data in enumerate(rows_data):
            for ci, cell_text in enumerate(row_data):
                target_r = start_row + ri
                target_c = start_col + ci
                if target_r < self.rowCount() and target_c < self.columnCount():
                    item = self.item(target_r, target_c)
                    if item:
                        item.setText(cell_text)
                    else:
                        self.setItem(target_r, target_c, QTableWidgetItem(cell_text))

    def _clear_selection(self):
        """清除选中区域的所有内容"""
        selection = self.selectedRanges()
        if not selection:
            return
        self.data_about_to_change.emit()
        for r in selection:
            for row in range(r.topRow(), r.bottomRow() + 1):
                for col in range(r.leftColumn(), r.rightColumn() + 1):
                    item = self.item(row, col)
                    if item:
                        item.setText("")

    def _cut_selection(self):
        """剪切选中区域"""
        self._copy_selection()
        selection = self.selectedRanges()
        if not selection:
            return
        r = selection[0]
        self.data_about_to_change.emit()
        for row in range(r.topRow(), r.bottomRow() + 1):
            for col in range(r.leftColumn(), r.rightColumn() + 1):
                item = self.item(row, col)
                if item:
                    item.setText("")

    # ==================== 列筛选（右键表头） ====================

    def on_header_clicked(self, col_index):
        """表头左键点击：选中整列"""
        self.selectColumn(col_index)

    def on_header_right_clicked(self, pos):
        """表头右键点击：显示筛选菜单"""
        col_index = self.horizontalHeader().logicalIndexAt(pos)
        if col_index >= 0:
            self.show_filter_menu(col_index)

    def show_filter_menu(self, col_index):
        """显示筛选菜单"""
        # 获取该列所有唯一值
        unique_values = set()
        for row in range(self.rowCount()):
            item = self.item(row, col_index)
            if item and item.text().strip():
                unique_values.add(item.text().strip())

        if not unique_values:
            return

        sorted_values = sorted(unique_values)

        # 创建菜单
        menu = QMenu(self)
        menu.setTitle(f"筛选列 {col_index + 1}")

        # 全选/取消全选
        select_all_act = QAction("✓ 全选", menu)
        deselect_all_act = QAction("☐ 取消全选", menu)
        menu.addAction(select_all_act)
        menu.addAction(deselect_all_act)
        menu.addSeparator()

        # 复选框列表
        checkbox_actions = []
        for value in sorted_values:
            act = QAction(value, menu)
            act.setData(value)
            act.setCheckable(True)
            act.setChecked(True)
            menu.addAction(act)
            checkbox_actions.append(act)

        # 添加筛选和清除按钮
        menu.addSeparator()
        apply_act = QAction("✓ 应用筛选", menu)
        clear_act = QAction("✕ 清除筛选", menu)
        menu.addAction(apply_act)
        menu.addAction(clear_act)

        # 计算菜单位置（表头下方）
        header_pos = self.horizontalHeader().mapToGlobal(QPoint(
            self.horizontalHeader().sectionPosition(col_index), 0
        ))
        menu_pos = self.horizontalHeader().mapFromGlobal(header_pos)
        menu_pos.setY(menu_pos.y() + self.horizontalHeader().height())

        # 显示菜单
        action = menu.exec_(self.horizontalHeader().mapToGlobal(menu_pos))

        if action == apply_act:
            # 应用筛选
            checked_values = [a.data() for a in checkbox_actions if a.isChecked()]
            self.filter_column(col_index, checked_values)
        elif action == clear_act:
            # 清除筛选
            self.clear_column_filter(col_index)

    def filter_column(self, col_index, allowed_values):
        """按列筛选，只显示匹配的行"""
        if not allowed_values:
            for row in range(self.rowCount()):
                self.setRowHidden(row, False)
            return

        for row in range(self.rowCount()):
            item = self.item(row, col_index)
            if item:
                cell_value = item.text().strip()
                self.setRowHidden(row, cell_value not in allowed_values)
            else:
                self.setRowHidden(row, True)

    def clear_column_filter(self, col_index):
        """清除指定列的筛选，显示所有行"""
        for row in range(self.rowCount()):
            self.setRowHidden(row, False)
