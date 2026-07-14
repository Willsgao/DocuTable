# -*- coding: utf-8 -*-
"""Table Engine Step 8 — OCR stub + UI 溯源验收。"""

import json
import subprocess
import sys
from pathlib import Path

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.integration.processor_bridge import run_table_engine_segmentation
from codes.table_engine.integration.trace_bridge import (
    format_cell_trace_line,
    trace_legacy_cell_bbox,
)
from codes.table_engine.ocr.backend import OCR_REQUIRED_MESSAGE, ScannedPdfNotSupportedError
from codes.table_engine.ocr.pdf_classifier import PdfClassifier
from codes.table_engine.ocr.stub import StubOcrBackend

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def _pillar_pdf_path() -> str:
    data = json.loads(Path(DEFAULT_PILLAR_CACHE).read_text(encoding="utf-8"))
    pdf = str(data.get("pdf_path", "")).replace("\\", "/")
    if not Path(pdf).exists():
        alt = Path("data/input_pdfs")
        if alt.exists():
            candidates = list(alt.glob("*601939*第三支柱*.pdf"))
            if candidates:
                return str(candidates[0]).replace("\\", "/")
    return pdf


def test_ocr_modules_exist() -> None:
    print("--- OCR 模块存在 ---")
    root = Path(__file__).resolve().parent
    for rel in (
        "codes/table_engine/ocr/backend.py",
        "codes/table_engine/ocr/stub.py",
        "codes/table_engine/ocr/pdf_classifier.py",
        "codes/table_engine/integration/trace_bridge.py",
    ):
        check(rel, (root / rel).is_file())


def test_stub_raises() -> None:
    print("--- StubOcrBackend ---")
    backend = StubOcrBackend()
    try:
        backend.extract_pages("dummy.pdf")
        check("stub raises", False, "no exception")
    except ScannedPdfNotSupportedError as e:
        check("stub raises ScannedPdfNotSupportedError", True)
        check("message mentions OCR", "OCR" in str(e))


def test_classifier_native_pdf() -> None:
    print("--- PdfClassifier 原生 PDF ---")
    pdf_path = _pillar_pdf_path()
    check("pillar pdf exists", bool(pdf_path) and Path(pdf_path).exists(), pdf_path)
    if not pdf_path or not Path(pdf_path).exists():
        return
    result = PdfClassifier.classify(pdf_path)
    check("pillar is native", result.kind == "native", result.kind)
    check("has text pages", result.text_pages > 0, str(result.text_pages))


def test_scanned_pdf_blocked() -> None:
    print("--- 扫描 PDF 拦截 ---")
    # 无真实扫描件时用 monkeypatch 分类结果
    from codes.table_engine.integration import processor_bridge as bridge

    orig = bridge.PdfClassifier.classify

    def _fake_scanned(_pdf):
        from codes.table_engine.ocr.pdf_classifier import PdfClassifyResult
        return PdfClassifyResult(kind="scanned", image_pages=3, text_pages=0)

    bridge.PdfClassifier.classify = staticmethod(_fake_scanned)
    try:
        entries, report, _ = run_table_engine_segmentation("any.pdf")
        check("scanned returns empty entries", entries == [])
        check("error code set", report.get("error") == "scanned_pdf_ocr_required")
        check("message set", OCR_REQUIRED_MESSAGE in report.get("message", ""))
    finally:
        bridge.PdfClassifier.classify = orig


def test_legacy_cell_trace() -> None:
    print("--- legacy cell trace ---")
    pdf_path = _pillar_pdf_path()
    if not pdf_path or not Path(pdf_path).exists():
        check("skip trace (no pdf)", True)
        return
    entries, _, _ = run_table_engine_segmentation(pdf_path)
    tables = [e for e in entries if e.get("type") == "table" and e.get("_structured")]
    check("has structured tables", bool(tables))
    if not tables:
        return
    table = tables[0]
    info = trace_legacy_cell_bbox(table, 0, 0, pdf_path)
    check("trace returns dict", info is not None)
    if info:
        check("has bbox_summary", "bbox_summary" in info)
        line = format_cell_trace_line(info)
        check("format line non-empty", bool(line))
        print(f"    sample: {line[:120]}")


def test_regression_prior_steps() -> None:
    print("--- Step 5/6/7 回归 ---")
    root = Path(__file__).resolve().parent
    for script in (
        "_test_te_step5_document.py",
        "_test_te_step6_processor.py",
        "_test_te_step7_cleanup.py",
    ):
        proc = subprocess.run(
            [sys.executable, str(root / script)],
            capture_output=True,
            text=True,
            cwd=root,
        )
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-1] if proc.stdout or proc.stderr else ""
        check(f"{script} exit 0", proc.returncode == 0, tail)


def main() -> None:
    test_ocr_modules_exist()
    test_stub_raises()
    test_classifier_native_pdf()
    test_scanned_pdf_blocked()
    test_legacy_cell_trace()
    test_regression_prior_steps()
    print(f"\n=== Step 8: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
