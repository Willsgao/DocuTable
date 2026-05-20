# -*- coding: utf-8 -*-
"""
PDFContext — PDF 共享上下文
一次打开 fitz.Document，全流程复用，消除 7 次重复 open 调用。
"""

from collections import OrderedDict
from pathlib import Path


class PDFContext:
    """PDF 共享上下文：打开一次，全局复用。

    使用方式：
        context = PDFContext("report.pdf")
        # 所有提取方法共用
        processor.is_image_pdf(context=context)
        processor.extract_text_tables(context=context)
        # 用完关闭
        context.close()
    """

    def __init__(self, pdf_path):
        import fitz
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)          # 唯一一次打开
        self.page_count = len(self.doc)

        # 文本缓存（全量保留，体积小）
        self._words_cache = {}       # {page_num: [words]}
        self._dict_cache = {}        # {page_num: dict}

        # 图片缓存 LRU（上限 10 页，防止内存爆炸）
        self._image_cache = OrderedDict()  # {page_num: QPixmap}
        self._max_image_cache = 10

    # ---- 页面访问 ----

    def get_page(self, page_num):
        """获取 fitz.Page 对象（0-based）"""
        return self.doc[page_num]

    # ---- words / dict 缓存 ----

    def get_words(self, page_num):
        """获取单页 words 列表（带缓存）"""
        if page_num not in self._words_cache:
            page = self.doc[page_num]
            words_raw = page.get_text("words")
            words = []
            for w in words_raw:
                words.append({
                    "x0": w[0], "y0": w[1],
                    "x1": w[2], "y1": w[3],
                    "text": w[4],
                    "baseline": w[3],
                })
            self._words_cache[page_num] = words
        return self._words_cache[page_num]

    def get_text_dict(self, page_num):
        """获取单页 dict 文本（带缓存）"""
        if page_num not in self._dict_cache:
            page = self.doc[page_num]
            self._dict_cache[page_num] = page.get_text("dict")
        return self._dict_cache[page_num]

    def get_page_text(self, page_num):
        """获取单页纯文本（带缓存，用于文本指纹匹配）"""
        if page_num not in self._dict_cache:
            page = self.doc[page_num]
            # get_text("text") 比 get_text("dict") 快 5-10 倍
            self._dict_cache[page_num] = page.get_text("text")
        return self._dict_cache[page_num]

    def get_drawings(self, page_num):
        """获取单页 drawings（不带缓存，数据量大）。
        保护 get_drawings() 的 C 扩展崩溃，失败时返回空列表。
        """
        page = self.doc[page_num]
        try:
            drawings_raw = page.get_drawings()
        except Exception:
            return []
        drawings = []
        for d in drawings_raw:
            rect = d["rect"]
            w = rect.width
            h = rect.height
            direction = None
            if w > h * 5:
                direction = "h"
            elif h > w * 5:
                direction = "v"
            drawings.append({
                "type": "line" if (w < h * 0.3 or h < w * 0.3) else "rect",
                "direction": direction,
                "x0": rect.x0, "y0": rect.y0,
                "x1": rect.x1, "y1": rect.y1,
                "color": d.get("color"),
                "width": d.get("width", 1),
                "fill": d.get("fill"),
            })
        return drawings

    # ---- 图片缓存 ----

    def get_page_image(self, page_num, scale=2.0):
        """获取单页渲染图 QPixmap（LRU 缓存，用于 UI 预览）"""
        from PyQt5.QtGui import QPixmap

        if page_num in self._image_cache:
            # 移到末尾（最近使用）
            self._image_cache.move_to_end(page_num)
            return self._image_cache[page_num]

        # 渲染新图
        page = self.doc[page_num]
        import fitz
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)

        # pix → QPixmap
        img_data = pix.tobytes("png")
        pixmap = QPixmap()
        pixmap.loadFromData(img_data, "PNG")

        # LRU 淘汰
        if len(self._image_cache) >= self._max_image_cache:
            self._image_cache.popitem(last=False)

        self._image_cache[page_num] = pixmap
        return pixmap

    def get_page_image_to_disk(self, page_num, output_path, scale=2.0):
        """渲染单页到磁盘 PNG（用于缓存持久化），返回输出路径"""
        page = self.doc[page_num]
        import fitz
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        pix.save(str(output_path))
        return str(output_path)

    def generate_all_previews(self, preview_dir, scale=2.0):
        """批量生成所有页面的预览图到磁盘目录"""
        preview_dir = Path(preview_dir)
        preview_dir.mkdir(parents=True, exist_ok=True)

        image_paths = []
        for page_num in range(self.page_count):
            output_path = preview_dir / f"preview_{page_num}.png"
            if not output_path.exists():
                self.get_page_image_to_disk(page_num, output_path, scale)
            image_paths.append(str(output_path))

        print(f"  [PDFContext] 预览图已生成到 {preview_dir}，共 {self.page_count} 页")
        return image_paths

    def generate_all_llm_images(self, output_dir, scale=2.0):
        """批量生成所有页面的 LLM 图片到磁盘目录"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        image_paths = []
        for page_num in range(self.page_count):
            output_path = output_dir / f"page_{page_num + 1}.png"
            if not output_path.exists():
                self.get_page_image_to_disk(page_num, output_path, scale)
            image_paths.append(str(output_path))

        print(f"  [PDFContext] LLM图片已生成到 {output_dir}，共 {self.page_count} 页")
        return image_paths

    # ---- 生命周期 ----

    def close(self):
        """关闭文档，清理所有缓存"""
        self._words_cache.clear()
        self._dict_cache.clear()
        self._image_cache.clear()
        if self.doc:
            self.doc.close()
            self.doc = None
        print(f"  [PDFContext] 已关闭: {self.pdf_path}")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
