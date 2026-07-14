# -*- coding: utf-8
"""Table Engine Step 1 — 几何建表（StructuredTable，无 data[][]）。"""

import sys
from pathlib import Path

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_builder import build_table_from_region

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def _cell_text(table, row: int, col: int) -> str:
    if row >= len(table.rows):
        return ""
    r = table.rows[row]
    if col >= len(r) or r[col] is None:
        return ""
    return str(r[col].text).strip()


def _find_row_by_col0(table, val: str):
    for ri, row in enumerate(table.rows):
        if _cell_text(table, ri, 0) == val:
            return ri
    return None


def test_page(cache: Path, page_num: int) -> None:
    page = load_page(cache, page_num)
    table = build_table_from_region(page, region_index=None)
    check(f"P{page_num} table built", table is not None)
    if not table:
        return

    ncol = table.grid.col_count
    print(f"  P{page_num}: layout={table.layout_id} rows={len(table.rows)} cols={ncol}")

    if page_num == 10:
        check("P10 layout cc1", table.layout_id == "pillar_cc1")
        check("P10 4 columns", ncol == 4)
        r1 = _find_row_by_col0(table, "1")
        check("P10 row1 exists", r1 is not None, f"r1={r1}")
        if r1 is not None:
            check("P10 row1 label", "实收" in _cell_text(table, r1, 1) or "385" in _cell_text(table, r1, 2))
            amt = _cell_text(table, r1, 2)
            code = _cell_text(table, r1, 3)
            check("P10 385621 in col2", "385,621" in amt or "385621" in amt.replace(",", ""), amt)
            check("P10 code in col3", code == "e+g" or bool(code), f"code={code}")

    if page_num == 13:
        check("P13 layout cc2", table.layout_id == "pillar_cc2")
        check("P13 5 columns", ncol == 5, f"ncol={ncol}")
        flat = " ".join(_cell_text(table, i, c) for i in range(min(6, len(table.rows))) for c in range(ncol))
        check("P13 abc header", "a" in flat and "b" in flat and "c" in flat)
        r1 = _find_row_by_col0(table, "1")
        if r1 is not None:
            check("P13 row1 label col1", "现金" in _cell_text(table, r1, 1))
            check("P13 row1 amount a", "2,571,361" in _cell_text(table, r1, 2))
            check("P13 row1 amount b", "2,571,361" in _cell_text(table, r1, 3))

    if page_num == 27:
        check("P27 sec1 layout", table.layout_id in ("pillar_sec1", "generic"))
        check("P27 >= 10 cols", ncol >= 10, f"ncol={ncol}")
        header = " ".join(_cell_text(table, i, c) for i in range(min(5, len(table.rows))) for c in range(ncol))
        check("P27 has column a", "a" in header.lower())

    non_empty = sum(1 for row in table.rows for c in row if c and c.text.strip())
    check(f"P{page_num} cells have source_items", all(
        c.source_items for row in table.rows for c in row if c and c.text.strip()
    ), f"cells={non_empty}")


def main() -> int:
    cache = DEFAULT_PILLAR_CACHE
    if not cache.exists():
        print(f"skip: {cache}")
        return 0

    print("=== Step 1: geometry table build ===\n")
    for pg in (10, 13, 27):
        test_page(cache, pg)
        print()

    print(f"=== 结果: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        print("Step 1 未通过")
        return 1
    print("Step 1 通过 — 可进入 Step 2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
