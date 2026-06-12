# -*- coding: utf-8 -*-
"""
多进程子进程工作函数（独立模块，无 Qt 依赖）

PyInstaller 打包后，ProcessPoolExecutor 子进程需要重新导入包含目标函数
的模块。如果函数定义在 processor.py 中，导入链会触发：
  processor.py → codes/pdf_extractor/__init__.py → widgets.py → PyQt5 widgets
  在无 QApplication 的子进程中导入 PyQt5 widgets 会导致闪退。

将此模块保持为纯 Python，不导入任何 Qt / UI 相关代码，子进程安全。
"""

import os
import sys
import tempfile
from io import BytesIO

# 日志模块（无 Qt 依赖，子进程安全）
from codes.pdf_extractor._log import write_log, log_exception, log_startup


def parse_docx_tables(docx_buf, page_num):
    """从 DOCX 缓冲区解析表格并标记页码。

    Args:
        docx_buf: BytesIO 缓冲区
        page_num: 所属页码
    Returns:
        list[dict]: 表格数据列表
    """
    from docx import Document

    W = ('{http://schemas.openxmlformats.org/'
         'wordprocessingml/2006/main}')
    doc = Document(docx_buf)
    tables = []

    for table in doc.tables:
        try:
            rows_data = []
            merge_tracker = {}

            for r, tr in enumerate(table.rows):
                row_cells = []
                col_idx = 0

                for cell in tr.cells:
                    while merge_tracker.get((r, col_idx)):
                        row_cells.append("")
                        col_idx += 1

                    tc = cell._tc
                    tcPr = tc.find(f'{W}tcPr')
                    col_span = 1
                    row_start = True

                    if tcPr is not None:
                        gridSpan = tcPr.find(f'{W}gridSpan')
                        if gridSpan is not None:
                            col_span = int(gridSpan.get(f'{W}val', 1))
                        vMerge = tcPr.find(f'{W}vMerge')
                        if vMerge is not None:
                            if vMerge.get(f'{W}val') != 'restart':
                                row_start = False

                    if row_start:
                        text = cell.text.strip()
                        for span in range(col_span):
                            row_cells.append(text if span == 0 else "")
                    else:
                        for span in range(col_span):
                            row_cells.append("")

                    col_idx += col_span

                if row_cells:
                    rows_data.append(row_cells)

            if rows_data:
                tables.append({
                    "page": page_num,
                    "type": "table",
                    "data": rows_data,
                    "text": "",
                    "extractor": "docx_per_page",
                    "confidence": 0.95,
                    "rows": len(rows_data),
                    "cols": (max(len(r) for r in rows_data)
                             if rows_data else 0),
                    "has_border": True,
                    "context_text": "",
                })
        except Exception:
            pass
    return tables


def convert_batch(pdf_path, page_nums, temp_dir=None):
    """每个子进程处理一批页。

    在独立子进程中运行，PyMuPDF C 扩展状态完全隔离，
    彻底避免多线程共享导致的 refcount 崩溃。

    Args:
        pdf_path: PDF 文件路径
        page_nums: 页码列表
        temp_dir: 临时目录路径（可选，默认从 codes.pdf_extractor.utils 获取）
    Returns:
        list[dict]: 该批次的所有表格数据
    """
    log_startup()

    try:
        from pdf2docx import Converter
        write_log(f"pdf2docx 导入成功", "OK")
    except Exception as e:
        log_exception(f"pdf2docx 导入失败: {e}")
        return []

    # 延迟导入 TEMP_DIR，避免主进程导入时依赖 Qt 模块
    if temp_dir is None:
        try:
            from codes.pdf_extractor.utils import TEMP_DIR as _TEMP_DIR
            temp_dir = _TEMP_DIR
            write_log(f"TEMP_DIR 导入成功: {temp_dir}", "OK")
        except Exception as e:
            log_exception(f"TEMP_DIR 导入失败: {e}")
            return []

    batch_tables = []
    cv = None

    try:
        write_log(f"开始处理 {len(page_nums)} 页: {page_nums[:3]}... (PDF={pdf_path})")
        cv = Converter(pdf_path)
        write_log(f"Converter 创建成功")

        for page_num in page_nums:
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(
                    suffix='.docx', dir=temp_dir)
                os.close(fd)
                write_log(f"  P{page_num}: 开始转换...")

                cv.convert(
                    tmp_path,
                    start=page_num - 1,
                    end=page_num,
                    layout=False,
                    table_deduction=False,
                    multi_processing=False,
                    cpu_count=1,
                )
                write_log(f"  P{page_num}: convert 成功")

                with open(tmp_path, 'rb') as f:
                    buf = BytesIO(f.read())
                tables = parse_docx_tables(buf, page_num)
                batch_tables.extend(tables)
                buf.close()
                write_log(f"  P{page_num}: 解析出 {len(tables)} 个表格")

            except Exception:
                log_exception(f"  P{page_num}: 处理异常")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        write_log(f"批次完成: {len(batch_tables)} 个表格", "OK")
    except Exception:
        log_exception(f"convert_batch 致命异常")
    finally:
        if cv is not None:
            try:
                cv.close()
            except Exception:
                pass

    return batch_tables
