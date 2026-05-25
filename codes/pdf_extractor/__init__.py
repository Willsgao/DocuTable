# -*- coding: utf-8 -*-
"""
PDF Table Extractor - 银行年报PDF表格提取工具
"""

from .utils import (
    cleanup_temp_files, TEMP_DIR,
    load_config, save_config,
    load_pdf_history, save_pdf_history,
    get_all_cached_files, delete_cache_file,
    get_cached_pdf_info,
    load_mid_data, save_mid_data,
    get_pdf_cache_dir, get_pdf_preview_dir,
    get_ai_correction_cache_path,
    save_ai_correction_cache,
    load_ai_correction_cache
)

from .widgets import (
    PDFPreviewWidget,
    ZoomableScrollArea,
    ZoomableTableWidget
)

from .processor import (
    PDFProcessor,
    VisionLLM,
    TableContextLLM,
    ExcelExporter,
    ProcessingWorker
)

from .ai_correction import (
    CorrectionResult,
    RuleChecker,
    RuleAutoFixer,
    LLMCorrector,
    CorrectionEngine
)

from .pdf_context import PDFContext

__all__ = [
    # utils
    'cleanup_temp_files', 'TEMP_DIR',
    'load_config', 'save_config',
    'load_pdf_history', 'save_pdf_history',
    'get_all_cached_files', 'delete_cache_file',
    'get_cached_pdf_info',
    'load_mid_data', 'save_mid_data',
    'get_pdf_cache_dir', 'get_pdf_preview_dir',
    'get_ai_correction_cache_path',
    'save_ai_correction_cache',
    'load_ai_correction_cache',
    # widgets
    'PDFPreviewWidget',
    'ZoomableScrollArea',
    'ZoomableTableWidget',
    # processor
    'PDFProcessor',
    'VisionLLM',
    'TableContextLLM',
    'ExcelExporter',
    'ProcessingWorker',
    # ai correction
    'CorrectionResult',
    'RuleChecker',
    'RuleAutoFixer',
    'LLMCorrector',
    'CorrectionEngine',
    # pdf context
    'PDFContext',
]
