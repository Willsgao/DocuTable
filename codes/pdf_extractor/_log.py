# -*- coding: utf-8 -*-
"""
调试日志模块（无 Qt 依赖，主进程和子进程通用）

PyInstaller 打包后 console=False 时所有 stdout/stderr 不可见，
闪退时无法排查原因。此模块将日志写入文件，便于诊断。

日志文件位置：TEMP_DIR/debug_log.txt
"""

import os
import sys
import time
import traceback
import threading
from datetime import datetime


def _get_log_dir():
    """获取日志目录（与 TEMP_DIR 一致，但不依赖 utils 模块）"""
    for drive in ['D:', 'E:', 'F:']:
        d = os.path.join(drive, 'temp', 'pdf_extractor')
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, '.w')
            with open(test, 'w') as f:
                f.write('')
            os.remove(test)
            return d
        except Exception:
            continue
    # 兜底
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'debug_logs')
    os.makedirs(d, exist_ok=True)
    return d


_LOG_DIR = None
_LOG_LOCK = threading.Lock()


def _ensure_dir():
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = _get_log_dir()


def _log_path():
    _ensure_dir()
    frozen_tag = "_frozen" if getattr(sys, 'frozen', False) else ""
    return os.path.join(_LOG_DIR, f"debug_log{frozen_tag}.txt")


def write_log(msg, tag="INFO"):
    """写一行日志到文件。

    Args:
        msg: 日志内容
        tag: 日志标签（INFO/WARN/ERR/TRACE）
    """
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        pid = os.getpid()
        tid = threading.current_thread().name
        line = f"[{ts}][PID={pid}][{tid}][{tag}] {msg}\n"
        with _LOG_LOCK:
            with open(_log_path(), 'a', encoding='utf-8') as f:
                f.write(line)
                f.flush()
    except Exception:
        pass  # 日志写入失败不能影响主流程


def log_exception(msg=""):
    """写入异常堆栈到日志。"""
    exc_info = traceback.format_exc()
    prefix = f"{msg}\n" if msg else ""
    write_log(f"{prefix}{exc_info}", "ERR")


def log_startup():
    """写启动标记。"""
    frozen = "FROZEN" if getattr(sys, 'frozen', False) else "NORMAL"
    write_log(f"========== 进程启动 [{frozen}] sys.executable={sys.executable} ==========")
    write_log(f"sys.path[0]={sys.path[0] if sys.path else 'EMPTY'}")
    write_log(f"_MEIPASS={getattr(sys, '_MEIPASS', 'N/A')}")
    write_log(f"cwd={os.getcwd()}")
