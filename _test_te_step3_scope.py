# -*- coding: utf-8
"""Table Engine Step 3 — Scope + gap 表头回补 + build_page。"""

import argparse
import sys

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.pipeline import build_page, primary_table
from codes.table_engine.source.liteparse_loader import load_page
from codes.table_engine.table_access import cell_text, dense_rows, find_row_index
from codes.table_engine.table_builder import build_table_from_region

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


def _flat_table(t) -> str:
    return " ".join(c.text for row in t.rows for c in row if c)


def _header_flat(t) -> str:
    return " ".join(c.text for row in t.rows[:8] for c in row if c)


def test_scope_expansion(page_num: int) -> None:
    print(f"--- P{page_num} scope 上扩 ---")
    page = load_page(CACHE, page_num)
    result = build_page(page)
    t = primary_table(result)
    check("page built", t is not None)
    if not t:
        return
    region_y0 = float(t.metadata.get("region_y0", t.y0))
    scope_y0 = float(t.metadata.get("scope_y0", t.y0))
    check(
        "scope.y0 < region.y0",
        scope_y0 < region_y0 - 0.5,
        f"scope={scope_y0:.1f} region={region_y0:.1f}",
    )
    check("scope metadata present", "scope_y0" in t.metadata)


def test_header_band(page_num: int) -> None:
    print(f"--- P{page_num} 表头带 ---")
    page = load_page(CACHE, page_num)
    t = primary_table(build_page(page))
    check("table exists", t is not None)
    if not t:
        return
    hdr = _header_flat(t)
    check("unit row", "人民币" in hdr or "百万元" in hdr, hdr[:80])
    check("amount/code cols", "数额" in hdr and "代码" in hdr, hdr[:80])
    check("letter a", "a" in hdr.lower(), hdr[:80])
    check("letter b", "b" in hdr.lower(), hdr[:80])
    check("reporting date", "2024" in hdr and "12" in hdr, hdr[:80])


def test_p10_narrative_excluded() -> None:
    print("--- P10 3.1 叙述不进表 ---")
    page = load_page(CACHE, 10)
    result = build_page(page)
    t = primary_table(result)
    check("table built", t is not None)
    if t:
        flat = _flat_table(t)
        check("no 3.1 regulator text", "国家金融监督管理总局" not in flat)
        check("no 3.1 section title", "非资本债务工具" not in flat)
        check("row1 data", "385,621" in flat)
    joined_gap = "\n".join(g.text for g in result.gap_texts)
    check("gap has 3.1", "3.1" in joined_gap or "非资本债务" in joined_gap, joined_gap[:120])
    check("gap has narrative", "国家金融监督管理总局" in joined_gap or len(joined_gap) > 40)


def test_p11_continuation() -> None:
    print("--- P11 续表 ---")
    page = load_page(CACHE, 11)
    result = build_page(page)
    check("single primary table", len(result.tables) == 1, f"n={len(result.tables)}")
    t = primary_table(result)
    if not t:
        return
    hdr = _header_flat(t)
    check("header in continuation", "人民币" in hdr and "数额" in hdr)
    check("row 22", "22" in _flat_table(t))
    r22 = find_row_index(t, "22")
    if r22 is not None:
        check("row22 dash", cell_text(t, r22, 3) == "-")


def test_legacy_builder_compat() -> None:
    print("--- 旧 build_table_from_region 兼容 ---")
    t10 = build_table_from_region(load_page(CACHE, 10))
    t11 = build_table_from_region(load_page(CACHE, 11), region_index=1)
    check("P10 cc1", t10 is not None and t10.layout_id == "pillar_cc1")
    check("P11 cc1 cont", t11 is not None and t11.layout_id == "pillar_cc1")
    if t10:
        r1 = find_row_index(t10, "1")
        check("P10 row1", r1 is not None and "385,621" in cell_text(t10, r1, 2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, default=0, help="只测指定页")
    args = parser.parse_args()

    if args.page in (0, 10):
        test_scope_expansion(10)
        test_header_band(10)
        test_p10_narrative_excluded()
    if args.page in (0, 11):
        test_scope_expansion(11)
        test_header_band(11)
        test_p11_continuation()
    if args.page == 0:
        test_legacy_builder_compat()

    print(f"\n=== Step 3: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
