#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
银行年报PDF解析工具 - 程序入口
"""
import sys
import os
import traceback
import multiprocessing
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtGui import QFont
from codes.pdf_extractor import cleanup_temp_files, startup_cleanup
import atexit

from codes.ui.main_window import MainWindow


# ============================================================
# 全局异常钩子（捕获闪退前的最后异常）
# ============================================================
def _global_exception_hook(exc_type, exc_value, exc_tb):
    """全局未捕获异常处理：写日志 + 弹框提示。"""
    from codes.pdf_extractor._log import write_log, log_exception

    msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    write_log(f"!!! 全局未捕获异常 !!!", "ERR")
    write_log(msg, "ERR")

    # 尝试弹框（如果在 Qt 事件循环中）
    try:
        import threading
        if threading.current_thread() is threading.main_thread():
            QMessageBox.critical(
                None, "程序异常",
                f"发生未捕获的异常，详情已写入日志文件。\n\n"
                f"{exc_type.__name__}: {exc_value}"
            )
    except Exception:
        pass

    # 交给默认处理器
    sys.__excepthook__(exc_type, exc_value, exc_tb)


# 安装全局异常钩子
sys.excepthook = _global_exception_hook


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    # ★ PyInstaller 打包后，Windows multiprocessing 必须调用 freeze_support
    # 否则子进程会反复重新执行 .exe，导致无限弹出新窗口
    multiprocessing.freeze_support()

    # PyInstaller 冻结环境：设置 PDFIUM_LIB_PATH 指向 _internal 目录，
    # liteparse 的 Rust/PyO3 底层需要 pdfium.dll 才能解析 PDF
    if getattr(sys, 'frozen', False):
        _meipass = getattr(sys, '_MEIPASS', '')
        if _meipass and os.path.isdir(_meipass):
            os.environ.setdefault('PDFIUM_LIB_PATH', _meipass)

    # 写启动日志
    from codes.pdf_extractor._log import write_log, log_startup
    log_startup()

    print("[INFO] 使用 QTableWidget 本地表格编辑器（无需 Office）")
    write_log("GUI 启动中...")

    # 启动时清理上次崩溃残留
    startup_cleanup()

    # 注册退出时清理
    atexit.register(cleanup_temp_files)

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    window = MainWindow()
    window.show()
    write_log("GUI 主窗口已显示", "OK")
    sys.exit(app.exec_())
