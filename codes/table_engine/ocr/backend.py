# -*- coding: utf-8 -*-
"""OCR 后端协议与工厂。"""

from __future__ import annotations

from typing import List, Protocol

from codes.table_engine.config import TableEngineConfig, default_config
from codes.table_engine.models import PageSource


OCR_REQUIRED_MESSAGE = (
    "该 PDF 为扫描件，Table Engine 需要 OCR 后端才能建表。"
    "当前仅提供 stub 接口（ocr_backend=stub），请使用可复制文字的原生 PDF，"
    "或等待 OCR 后端接入后再处理扫描件。"
)


class ScannedPdfNotSupportedError(RuntimeError):
    """扫描 PDF 在 stub OCR 下不可建表。"""


class OcrBackend(Protocol):
    def extract_pages(self, pdf_path: str) -> List[PageSource]:
        """输出与 liteparse 同形的 PageSource 列表。"""
        ...


def get_ocr_backend(config: TableEngineConfig | None = None) -> OcrBackend:
    cfg = config or default_config()
    if cfg.ocr_backend == "stub":
        from codes.table_engine.ocr.stub import StubOcrBackend
        return StubOcrBackend()
    raise ValueError(f"未知 OCR 后端: {cfg.ocr_backend!r}")
