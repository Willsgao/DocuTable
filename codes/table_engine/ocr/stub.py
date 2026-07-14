# -*- coding: utf-8 -*-
"""OCR stub：扫描 PDF 明确失败，不静默产出错误表格。"""

from __future__ import annotations

from typing import List

from codes.table_engine.models import PageSource
from codes.table_engine.ocr.backend import OCR_REQUIRED_MESSAGE, ScannedPdfNotSupportedError


class StubOcrBackend:
    """占位 OCR 后端；调用即抛出，供 UI / processor 捕获并提示用户。"""

    def extract_pages(self, pdf_path: str) -> List[PageSource]:
        raise ScannedPdfNotSupportedError(OCR_REQUIRED_MESSAGE)
