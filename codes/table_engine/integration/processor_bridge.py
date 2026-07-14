# -*- coding: utf-8
"""Processor 接入：DocumentBuilder + legacy_adapter。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from codes.liteparse_extractor.cache_manager import get_cache_dir_path
from codes.table_engine.export.legacy_adapter import document_to_legacy_list
from codes.table_engine.ocr.backend import OCR_REQUIRED_MESSAGE, ScannedPdfNotSupportedError
from codes.table_engine.ocr.pdf_classifier import PdfClassifier
from codes.table_engine.pipeline import DocumentBuilder


def resolve_pages_json_path(pdf_path: str) -> Path | None:
    """liteparse 缓存中的 pages.json。"""
    if not pdf_path:
        return None
    pages_json = get_cache_dir_path(pdf_path) / "pages.json"
    return pages_json if pages_json.is_file() else None


def run_table_engine_segmentation(
    pdf_path: str,
    *,
    skip_scanned_check: bool = False,
) -> Tuple[List[dict], dict, List[dict]]:
    """从 PDF 路径加载 liteparse 缓存 → Table Engine → legacy 条目。

    Returns:
        (all_entries, segmentation_report, table_entries_only)
    """
    classification = PdfClassifier.classify(pdf_path) if pdf_path else None

    if (
        classification
        and classification.is_scanned
        and not skip_scanned_check
    ):
        return [], {
            "method": "table_engine",
            "error": "scanned_pdf_ocr_required",
            "message": OCR_REQUIRED_MESSAGE,
            "pdf_kind": classification.kind,
            "ocr_backend": "stub",
            "text_pages": classification.text_pages,
            "image_pages": classification.image_pages,
        }, []

    pages_json = resolve_pages_json_path(pdf_path)
    if pages_json is None:
        report = {"method": "table_engine", "error": "no_pages_json"}
        if classification:
            report["pdf_kind"] = classification.kind
        return [], report, []

    document = DocumentBuilder().build(pages_json)
    legacy = document_to_legacy_list(document)

    n_table = sum(1 for e in legacy if e.get("type") == "table")
    n_text = sum(1 for e in legacy if e.get("type") == "text")

    report = {
        "method": "table_engine",
        "total_tables": n_table,
        "total_entries": len(legacy),
        "total_text": n_text,
        "pages_processed": document.build_report.pages_processed,
        "warnings": list(document.build_report.warnings),
        "fusion_stats": {
            "method": "table_engine",
            "tables_built": document.build_report.tables_built,
            "text_blocks": document.build_report.text_blocks,
        },
    }
    if classification:
        report["pdf_kind"] = classification.kind

    table_entries = [e for e in legacy if e.get("type") == "table"]
    return legacy, report, table_entries


def format_segmentation_report(tables: List[dict], report: dict) -> str:
    """生成分割报告文本（供 UI 对话框）。"""
    lines = [
        "=" * 60,
        "  Table Engine 分割报告",
        "=" * 60,
        f"  方法: {report.get('method', 'table_engine')}",
        f"  表格数: {report.get('total_tables', sum(1 for t in tables if t.get('type') == 'table'))}",
        f"  文本块: {report.get('total_text', sum(1 for t in tables if t.get('type') == 'text'))}",
        f"  总条目: {report.get('total_entries', len(tables))}",
    ]
    warnings = report.get("warnings") or []
    if warnings:
        lines.append(f"  警告: {len(warnings)}")
        for w in warnings[:5]:
            lines.append(f"    - {w}")
    if report.get("error") == "scanned_pdf_ocr_required":
        lines.append("  ⚠ 扫描 PDF：需 OCR 后端")
        lines.append(f"  {report.get('message', '')}")
    pdf_kind = report.get("pdf_kind")
    if pdf_kind:
        lines.append(f"  PDF类型: {pdf_kind}")
    real_count = sum(1 for t in tables if t.get("is_real_table"))
    lines.append(f"  财务数据表: {real_count}")
    lines.append("=" * 60)
    return "\n".join(lines)
