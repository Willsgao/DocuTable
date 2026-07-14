# -*- coding: utf-8
"""Table Engine — 约束网格（CGR）回归。"""

import sys

from codes.table_engine.config import DEFAULT_PILLAR_CACHE
from codes.table_engine.geometry.grid_infer import infer_constraint_grid
from codes.table_engine.geometry.item_bridge import source_items_to_dicts
from codes.table_engine.geometry.row_dict import cluster_items_by_y
from codes.table_engine.geometry.row_refiner import refine_clustered_rows
from codes.table_engine.pipeline import build_page
from codes.table_engine.source.liteparse_loader import load_liteparse_document
from codes.table_engine.table_access import dense_rows
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


def _grid_for_page(page_num: int):
    lite = load_liteparse_document(DEFAULT_PILLAR_CACHE)
    page = lite.get_page(page_num)
    if not page or not page.table_regions:
        return None, None
    region = max(page.table_regions, key=lambda r: (r.y1 - r.y0) * (r.x1 - r.x0))
    dicts = source_items_to_dicts([it for it in page.items])
    rows = refine_clustered_rows(cluster_items_by_y(dicts))
    grid = infer_constraint_grid(rows, region.x0 - 5, region.x1 + 5)
    return grid, region


def test_grid_infer_p34_four_cols() -> None:
    print("--- CGR P34 四列沟 ---")
    grid, _ = _grid_for_page(34)
    check("grid inferred", grid is not None)
    if not grid:
        return
    check(">= 4 cols", grid.col_count >= 4, str(grid.col_count))
    print(f"    centers={grid.column_centers}")


def test_build_p34_uses_grid() -> None:
    print("--- CGR P34 建表 ---")
    lite = load_liteparse_document(DEFAULT_PILLAR_CACHE)
    table = build_table_from_region(lite.get_page(34))
    check("table built", table is not None)
    if not table:
        return
    check("4 cols", table.grid.col_count == 4, str(table.grid.col_count))
    check("has pillar gsib", table.layout_id == "pillar_gsib", table.layout_id)
    dense = dense_rows(table)
    if len(dense) > 2 and len(dense[2]) >= 4:
        check("row2 split", dense[2][0].strip() == "1" and "规模" in dense[2][1])
        check("row3 empty cat", not dense[3][1].strip() and "金融机构" in dense[3][2])


def test_build_p33_still_three_cols() -> None:
    print("--- CGR P33 不破坏 disclosure ---")
    result = build_page(load_liteparse_document(DEFAULT_PILLAR_CACHE).get_page(33))
    table = result.tables[0] if result.tables else None
    check("built", table is not None)
    if table:
        check(">=3 cols", table.grid.col_count >= 3)
        check("disclosure kept", table.layout_id == "pillar_disclosure", table.layout_id)


def main() -> None:
    test_grid_infer_p34_four_cols()
    test_build_p34_uses_grid()
    test_build_p33_still_three_cols()
    print(f"\n=== Grid infer: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
