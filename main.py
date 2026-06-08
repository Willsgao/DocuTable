#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
银行年报PDF解析工具 - 程序入口
"""
import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont
from codes.pdf_extractor import cleanup_temp_files, startup_cleanup
import atexit

from codes.ui.main_window import MainWindow

print("[INFO] 使用 QTableWidget 本地表格编辑器（无需 Office）")

# 启动时清理上次崩溃残留
startup_cleanup()

# 注册退出时清理
atexit.register(cleanup_temp_files)


# ============================================================
# 程序入口
# ============================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
