# -*- coding: utf-8 -*-
"""OCR 后端接口（Step 8 stub）。"""

from codes.table_engine.ocr.backend import (
    OCR_REQUIRED_MESSAGE,
    OcrBackend,
    ScannedPdfNotSupportedError,
    get_ocr_backend,
)
from codes.table_engine.ocr.pdf_classifier import PdfClassifyResult, PdfClassifier
from codes.table_engine.ocr.stub import StubOcrBackend

__all__ = [
    "OCR_REQUIRED_MESSAGE",
    "OcrBackend",
    "PdfClassifyResult",
    "PdfClassifier",
    "ScannedPdfNotSupportedError",
    "StubOcrBackend",
    "get_ocr_backend",
]
