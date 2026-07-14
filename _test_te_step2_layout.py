# -*- coding: utf-8
"""Table Engine Step 2 — Layout 插件 + StructuredTable golden。"""

import sys
from pathlib import Path

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.layout.registry import all_plugins
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_builder import build_table_from_region
from codes.table_engine.table_access import cell_text, col0_values, find_row_index, dense_rows

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


def _build(page_num: int, region_index=None):
    page = load_page(CACHE, page_num)
    return build_table_from_region(page, region_index=region_index)


def test_plugin_registry() -> None:
    print("--- plugin registry ---")
    ids = {p.layout_id for p in all_plugins()}
    check("has pillar_cc1", "pillar_cc1" in ids)
    check("has pillar_cc2", "pillar_cc2" in ids)
    check("has pillar_sec1", "pillar_sec1" in ids)
    check("has pillar_disclosure", "pillar_disclosure" in ids)
    check("has pillar_gsib", "pillar_gsib" in ids)
    check("has constraint_grid", "constraint_grid" in ids)
    check("has generic", "generic" in ids)


def test_cc1_p10() -> None:
    print("--- P10 CC1 主表 ---")
    t = _build(10)
    check("built", t is not None)
    if not t:
        return
    check("layout pillar_cc1", t.layout_id == "pillar_cc1")
    check("4 cols", t.grid.col_count == 4)
    check("roles amount/code", any(r.role == "amount" for r in t.grid.ranges))
    r1 = find_row_index(t, "1")
    check("row1", r1 is not None)
    if r1 is not None:
        check("385621 col2", "385,621" in cell_text(t, r1, 2))
        check("code col3", cell_text(t, r1, 3) == "e+g")
    hdr = dense_rows(t)[0] if t.rows else []
    check("unit col0", "人民币" in str(hdr[0] if hdr else ""))
    check("grid confidence", t.metadata.get("layout_confidence", 0) >= 0.9)


def test_cc1_p11_continuation() -> None:
    print("--- P11 CC1 续表 ---")
    t = _build(11, region_index=1)
    check("built", t is not None)
    if not t:
        return
    check("layout cc1", t.layout_id == "pillar_cc1")
    check("4 cols", t.grid.col_count == 4)
    c0 = col0_values(t)
    check("no 23242526", not any("232425" in c for c in c0))
    check("no 272829 glue", not any("272829" in c for c in c0))
    check("row 22", "22" in c0)
    check("row 26", "26" in c0)
    check("row 53", "53" in c0)
    r22 = find_row_index(t, "22")
    if r22 is not None:
        check("row22 label wrap", "递延税" in cell_text(t, r22, 1))
        check("row22 dash col3", cell_text(t, r22, 3) == "-")
    r25 = find_row_index(t, "25")
    check("row25 amount col2", r25 is not None and "7,760" in cell_text(t, r25, 2))
    r45 = find_row_index(t, "45")
    if r45 is not None:
        check("row45 col3 not label", "直接" not in cell_text(t, r45, 3))
        check("row45 col3 dash", cell_text(t, r45, 3) in ("-", ""))


def test_cc2_p13() -> None:
    print("--- P13 CC2 ---")
    t = _build(13)
    check("built", t is not None)
    if not t:
        return
    check("layout cc2", t.layout_id == "pillar_cc2")
    check("5 cols", t.grid.col_count == 5, f"cols={t.grid.col_count}")
    flat = " ".join(cell_text(t, i, c) for i in range(min(6, len(t.rows))) for c in range(5))
    check("abc header", all(x in flat for x in ("a", "b", "c")))
    r1 = find_row_index(t, "1")
    if r1 is not None:
        check("row1 serial col0", cell_text(t, r1, 0) == "1")
        check("row1 label col1", "现金" in cell_text(t, r1, 1))
        check("row1 amount col2", "2,571,361" in cell_text(t, r1, 2))
        check("row1 amount col3", "2,571,361" in cell_text(t, r1, 3))
    r17 = find_row_index(t, "17")
    r18 = find_row_index(t, "18")
    check("row17 code b", r17 is not None and cell_text(t, r17, 4) == "b")
    check("row18 code a", r18 is not None and cell_text(t, r18, 4) == "a")


def test_sec1_p27() -> None:
    print("--- P27 SEC1 ---")
    t = _build(27, region_index=0)
    check("built", t is not None)
    if not t:
        return
    check("layout sec1", t.layout_id == "pillar_sec1")
    check(">=12 cols", t.grid.col_count >= 12)
    flat = " ".join(c.text for row in t.rows for c in row if c)
    check("足STC header", "足STC" in flat)
    r1 = find_row_index(t, "1")
    check("row1 label", r1 is not None and "零售" in cell_text(t, r1, 1))
    check("row1 value", "7,195" in flat)
    r2 = find_row_index(t, "2")
    check("row2 wrapped", r2 is not None and "住房抵押" in cell_text(t, r2, 1) and "款" in cell_text(t, r2, 1))
    r6 = find_row_index(t, "6")
    if r6 is None:
        r6 = next((i for i in range(len(t.rows)) if "公司类" in cell_text(t, i, 0) or "公司类" in cell_text(t, i, 1)), None)
    check("row6 company", r6 is not None and "公司类" in (cell_text(t, r6, 0) + cell_text(t, r6, 1)))
    r8 = find_row_index(t, "8")
    check("row8 loan wrap", r8 is not None and "商用房地产" in cell_text(t, r8, 1) and "贷款" in cell_text(t, r8, 1))


def main() -> int:
    if not CACHE.exists():
        print(f"skip: {CACHE}")
        return 0

    print("=== Step 2: layout plugins ===\n")
    test_plugin_registry()
    print()
    test_cc1_p10()
    print()
    test_cc1_p11_continuation()
    print()
    test_cc2_p13()
    print()
    test_sec1_p27()

    print(f"\n=== 结果: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        print("Step 2 未通过")
        return 1
    print("Step 2 通过 — 可进入 Step 3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
