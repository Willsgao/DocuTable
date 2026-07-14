# -*- coding: utf-8
"""Table Engine Step 5 — 全文档 DocumentBuilder + legacy export。"""

import sys
from pathlib import Path

import yaml

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.export.legacy_adapter import (
    document_to_legacy_list,
    entry_to_legacy,
    verify_legacy_table_matches_structured,
)
from codes.table_engine.pipeline import DocumentBuilder, build_page
from codes.table_engine.source.liteparse_loader import load_liteparse_document

CACHE = DEFAULT_PILLAR_CACHE
GOLDEN = Path("tests/golden/pillar_pages.yaml")
PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def _load_golden() -> dict:
    if not GOLDEN.exists():
        return {"total_pages": 43, "table_pages_must_build": True, "p0_pages": []}
    return yaml.safe_load(GOLDEN.read_text(encoding="utf-8")) or {}


def test_document_builder() -> None:
    print("--- DocumentBuilder 全文档 ---")
    golden = _load_golden()
    doc = DocumentBuilder().build(CACHE)
    lite = load_liteparse_document(CACHE)

    check("total pages", doc.build_report.pages_processed == golden.get("total_pages", 43))
    n_table = sum(1 for e in doc.entries if e.kind == "table")
    n_text = sum(1 for e in doc.entries if e.kind == "text")
    print(f"  entries: table={n_table} text={n_text} warnings={len(doc.build_report.warnings)}")

    exp = golden.get("expected_totals") or {}
    if exp:
        check("tables in range", exp.get("min_tables", 0) <= n_table <= exp.get("max_tables", 999))
        check("text in range", exp.get("min_text", 0) <= n_text <= exp.get("max_text", 999))

    if golden.get("table_pages_must_build"):
        for page in lite.pages:
            if not page.is_table_page:
                continue
            built = build_page(page)
            check(
                f"P{page.page_number} table_page built",
                len(built.tables) >= 1,
                str(built.warnings),
            )

    p0_failures = []
    for spec in golden.get("p0_pages") or []:
        pn = int(spec["page"])
        page = lite.get_page(pn)
        if page is None:
            p0_failures.append(f"P{pn} missing")
            continue
        result = build_page(page)
        nt, nx = len([e for e in result.entries if e.kind == "table"]), len(
            [e for e in result.entries if e.kind == "text"]
        )
        ok = True
        if nt < spec.get("min_tables", 1):
            ok = False
        if nt > spec.get("max_tables", 99):
            ok = False
        if nx > spec.get("max_text", 99):
            ok = False
        layout = spec.get("layout")
        if layout and result.tables:
            from codes.table_engine.pipeline import primary_table

            pt = primary_table(result)
            if pt and pt.layout_id != layout:
                ok = False
        check(f"P{pn} P0", ok, f"tables={nt} text={nx}")
        if not ok:
            p0_failures.append(f"P{pn}")

    check("P0 pages all pass", not p0_failures, ", ".join(p0_failures))


def test_legacy_export() -> None:
    print("--- legacy export 一致性 ---")
    doc = DocumentBuilder().build(CACHE)
    legacy = document_to_legacy_list(doc)
    check("legacy count", len(legacy) == len(doc.entries))

    mismatches = 0
    for entry, leg in zip(doc.entries, legacy):
        if entry.kind != "table" or entry.table is None:
            continue
        if not verify_legacy_table_matches_structured(entry.table, leg):
            mismatches += 1
        single = entry_to_legacy(entry)
        check(
            f"P{entry.page} export rows",
            single.get("data") == entry.table.iter_rows_dense(),
            f"rows={single.get('rows')}",
        )
        if not verify_legacy_table_matches_structured(entry.table, single):
            mismatches += 1

    check("no export mismatch", mismatches == 0, f"n={mismatches}")


def main() -> None:
    test_document_builder()
    test_legacy_export()
    print(f"\n=== Step 5 document: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
