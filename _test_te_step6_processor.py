# -*- coding: utf-8
"""Table Engine Step 6 — processor 单轨接入验收。"""

import json
import sys
from pathlib import Path

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.integration.processor_bridge import run_table_engine_segmentation
from codes.table_engine.pipeline import DocumentBuilder
from codes.table_engine.export.legacy_adapter import document_to_legacy_list

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


def test_processor_no_hybrid_import() -> None:
    print("--- processor 无 hybrid 建表路径 ---")
    src = Path("codes/pdf_extractor/processor.py").read_text(encoding="utf-8")
    check("no hybrid_segmenter import", "from codes.table_validator.hybrid_segmenter import" not in src)
    check("no hybrid_segment_tables call", "hybrid_segment_tables(" not in src)
    check("uses table_engine bridge", "run_table_engine_segmentation" in src)


def test_bridge_matches_step5() -> None:
    print("--- bridge 与 Step5 一致 ---")
    pdf_path = _pillar_pdf_path()
    check("pillar pdf exists", bool(pdf_path) and Path(pdf_path).exists(), pdf_path)

    te_entries, report, table_only = run_table_engine_segmentation(pdf_path)
    step5 = document_to_legacy_list(DocumentBuilder().build(DEFAULT_PILLAR_CACHE))

    check("bridge produced entries", len(te_entries) > 0)
    check("report method table_engine", report.get("method") == "table_engine")
    check("entry count match step5", len(te_entries) == len(step5), f"{len(te_entries)} vs {len(step5)}")
    check("table count match", sum(1 for e in te_entries if e.get("type") == "table") == sum(
        1 for e in step5 if e.get("type") == "table"
    ))
    check("all extractor table_engine", all(e.get("extractor") == "table_engine" for e in te_entries))
    check("no hybrid segment_source", not any(
        str(e.get("segment_source", "")).startswith("hybrid") for e in te_entries
    ))


def test_p0_pages_in_bridge() -> None:
    print("--- P0 页 bridge 抽查 ---")
    pdf_path = _pillar_pdf_path()
    entries, _, _ = run_table_engine_segmentation(pdf_path)
    by_page = {}
    for e in entries:
        by_page.setdefault(e.get("page"), []).append(e)
    for pn in (10, 11, 13, 27):
        page_entries = by_page.get(pn, [])
        n_table = sum(1 for e in page_entries if e.get("type") == "table")
        check(f"P{pn} has table", n_table >= 1, f"tables={n_table}")


def main() -> None:
    test_processor_no_hybrid_import()
    test_bridge_matches_step5()
    test_p0_pages_in_bridge()
    print(f"\n=== Step 6: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
