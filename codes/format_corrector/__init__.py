# -*- coding: utf-8 -*-
"""独立表格格式纠错模块。

不接入、不修改现有 ai_correction 流程。
用法：
  from codes.format_corrector import FormatCorrectorEngine
  engine = FormatCorrectorEngine(pdf_path, use_llm=False, auto_apply=False)
  report = engine.run_from_pdf_cache()
"""

from .engine import FormatCorrectorEngine
from .models import (
    Confidence,
    FormatCorrectionReport,
    FormatTask,
    TaskStatus,
    TaskType,
)

__all__ = [
    "FormatCorrectorEngine",
    "FormatCorrectionReport",
    "FormatTask",
    "TaskType",
    "TaskStatus",
    "Confidence",
]
