# -*- coding: utf-8
"""Table Engine Step 5 — cell 溯源抽查。"""

import argparse
import sys

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.export.cell_trace import trace_cell, trace_cell_to_dicts
from codes.table_engine.pipeline import DocumentBuilder, primary_table
from codes.table_engine.source.liteparse_loader import load_liteparse_document
from codes.table_engine.table_access import cell_text

CACHE = DEFAULT_PILLAR_CACHE
PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, default=11)
    parser.add_argument("--row", type=int, default=5)
    parser.add_argument("--col", type=int, default=2)
    args = parser.parse_args()

    lite = load_liteparse_document(CACHE)
    doc = DocumentBuilder().build(CACHE)
    page_entries = [e for e in doc.entries if e.page == args.page and e.kind == "table"]
    check("page has table entry", bool(page_entries))
    if not page_entries:
        print(f"\n=== Step 5 trace: {PASS} passed, {FAIL} failed ===")
        sys.exit(1)

    table = max(page_entries, key=lambda e: (e.y1 - e.y0)).table
    check("table resolved", table is not None)
    if not table:
        sys.exit(1)

    row, col = args.row, args.col
    if row >= len(table.rows):
        row = min(row, len(table.rows) - 1)
    text = cell_text(table, row, col)
    print(f"  P{args.page} cell[{row},{col}] = {text!r}")

    hits = trace_cell(table, row, col, lite)
    check("has source_items", bool(hits))
    check("trace resolves coords", all(
        h.get("missing") or ("x0" in h and "y0" in h) for h in hits
    ))

    detail = trace_cell_to_dicts(table, row, col, lite)
    if detail:
        print("  --- trace ---")
        for item in detail.get("source_items", []):
            if item.get("missing"):
                print(f"    missing index={item.get('item_index')}")
            else:
                print(
                    f"    idx={item['item_index']} "
                    f"({item['x0']:.1f},{item['y0']:.1f})-({item['x1']:.1f},{item['y1']:.1f}) "
                    f"{item['text']!r}"
                )

    check("cell text non-empty or dash", bool(text) or text == "-")

    print(f"\n=== Step 5 trace: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
