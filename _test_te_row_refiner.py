# -*- coding: utf-8
"""Table Engine — 行精修（body 锚定 + DSIB）回归。"""

import sys

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_liteparse_document
from codes.table_engine.table_access import dense_rows, cell_text

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def test_p35_dsib_rows_not_merged() -> None:
    print("--- P35 DSIB 数据行不纵并 ---")
    lite = load_liteparse_document(DEFAULT_PILLAR_CACHE)
    page = lite.get_page(35)
    check("page 35 exists", page is not None)
    if not page:
        return

    result = build_page(page)
    tables = [t for t in result.tables if t.page == 35]
    primary = max(tables, key=lambda t: len(t.rows)) if tables else None
    check("primary table built", primary is not None)
    if not primary:
        return

    n = len(primary.rows)
    check(">= 15 data rows", n >= 15, f"rows={n}")

    dense = dense_rows(primary)
    check("row2 规模 separate", "规模" in dense[2][0] and "关联度" not in dense[2][0])
    check(
        "row3 金融机构间资产 in indicator col",
        dense[3][1].strip() == "金融机构间资产",
        dense[3],
    )
    check(
        "row4 关联度+负债分列",
        dense[4][0].strip() == "关联度"
        and dense[4][1].strip() == "金融机构间负债",
        dense[4],
    )
    glued = any(
        "40,137,194" in c and "3,387,670" in c
        for row in dense
        for c in row
    )
    check("no glued values in one cell", not glued)

    # 二级指标应在标签列（col1），非挤进 col0
    for ri in range(3, min(8, len(dense))):
        c0 = dense[ri][0] if dense[ri] else ""
        c1 = dense[ri][1] if len(dense[ri]) > 1 else ""
        if "资产" in c1 or "负债" in c1:
            check(f"row{ri} label in col1", "资产" not in c0 and "负债" not in c0, f"{c0!r}|{c1!r}")
            break


def test_p11_cc1_label_wrap() -> None:
    print("--- P11 CC1 折行标签仍合并 ---")
    lite = load_liteparse_document(DEFAULT_PILLAR_CACHE)
    result = build_page(lite.get_page(11))
    table = max(result.tables, key=lambda t: t.y1 - t.y0) if result.tables else None
    check("P11 table", table is not None)
    if not table:
        return

    dense = dense_rows(table)
    check("no 232425 glue", not any("232425" in "".join(r) for r in dense))
    if len(dense) > 1:
        date_row = dense[1]
        check("date in amount col", "2024" in (date_row[2] if len(date_row) > 2 else ""), str(date_row))
        check("date not in label col", "2024" not in (date_row[1] if len(date_row) > 1 else ""), str(date_row))
    if len(table.rows) > 21:
        label = cell_text(table, 21, 1) if table.grid.col_count > 1 else ""
        check("row22 has label", len(label) > 4, label)


def test_p33_irrbb_three_columns() -> None:
    print("--- P33 IRRBB1 三列分开 ---")
    lite = load_liteparse_document(DEFAULT_PILLAR_CACHE)
    result = build_page(lite.get_page(33))
    table = result.tables[0] if result.tables else None
    check("table built", table is not None)
    if not table:
        return
    check("layout disclosure", table.layout_id == "pillar_disclosure", table.layout_id)
    check(">= 3 cols", table.grid.col_count >= 3, str(table.grid.col_count))
    dense = dense_rows(table)
    data_row = next(
        (row for row in dense if any("平行向上" in c for c in row)),
        None,
    )
    check("data row found", data_row is not None)
    if data_row and len(data_row) >= 3:
        check("label col0", "平行" in data_row[0])
        check("value a separate", "(454,022)" in data_row[1])
        check("value b separate", "115,645" in data_row[2])
    glued = any(
        "(454,022)" in c and "115,645" in c
        for row in dense[:12]
        for c in row
    )
    check("no ab values glued", not glued)
    if len(dense) > 2:
        check("measure header row", "经济价值" in dense[1][1] or "经济" in dense[1][1])
        check("period row separate", "期间" in dense[2][0] and "2024" in dense[2][1])
        check("no header date glue", "22024" not in "".join(dense[1]) and "32024" not in "".join(dense[1]))


def test_p34_gsib_four_text_columns() -> None:
    print("--- P34 GSIB1 四列文本 ---")
    lite = load_liteparse_document(DEFAULT_PILLAR_CACHE)
    result = build_page(lite.get_page(34))
    table = result.tables[0] if result.tables else None
    check("table built", table is not None)
    if not table:
        return
    check("layout gsib", table.layout_id == "pillar_gsib", table.layout_id)
    check("4 cols", table.grid.col_count == 4, str(table.grid.col_count))
    dense = dense_rows(table)
    if len(dense) > 2 and len(dense[2]) >= 4:
        check("row2 serial", dense[2][0].strip() == "1")
        check("row2 category", "规模" in dense[2][1])
        check("row2 indicator", "调整" in dense[2][2])
        check("row2 value", "43,104,261" in dense[2][3])
    if len(dense) > 3 and len(dense[3]) >= 4:
        check("row3 serial only col0", dense[3][0].strip() == "2")
        check("row3 empty category", not dense[3][1].strip())
        check("row3 indicator col2", "金融机构" in dense[3][2])
    glued = any(
        "规模" in dense[i][0] and "调整" in dense[i][0]
        for i in range(2, min(10, len(dense)))
        if len(dense[i]) >= 1
    )
    check("no text cols glued in col0", not glued)


def test_p9_ov1_date_columns() -> None:
    print("--- P9 OV1 三期表头分列 ---")
    lite = load_liteparse_document(DEFAULT_PILLAR_CACHE)
    result = build_page(lite.get_page(9))
    table = result.tables[0] if result.tables else None
    check("table built", table is not None)
    if not table:
        return
    check("layout disclosure", table.layout_id == "pillar_disclosure", table.layout_id)
    check("5 cols", table.grid.col_count == 5, str(table.grid.col_count))
    dense = dense_rows(table)
    if len(dense) > 1 and len(dense[1]) >= 5:
        y2024 = [c for c in dense[1] if "2024" in c]
        check("three 2024 headers", len(y2024) >= 3, str(dense[1]))
        check("no glued 2024", not any(c.count("2024") >= 2 for c in dense[1]))
    hdr_rows = dense[:3] if len(dense) >= 3 else dense
    hdr_flat = "".join("".join(r) for r in hdr_rows)
    check("date col present", "9" in hdr_flat and "30" in hdr_flat and "2024" in hdr_flat)


def main() -> None:
    test_p35_dsib_rows_not_merged()
    test_p33_irrbb_three_columns()
    test_p34_gsib_four_text_columns()
    test_p9_ov1_date_columns()
    test_p11_cc1_label_wrap()
    print(f"\n=== Row refiner: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
