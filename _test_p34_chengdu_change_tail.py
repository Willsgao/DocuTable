# -*- coding: utf-8 -*-
"""P34 成都银行：变化原因表续行（无表头 fragment）须保留全部金额且拆列。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.table_access import dense_rows


def _item(text: str, x0: float, y0: float, x1: float | None = None) -> SourceItem:
    x1 = x1 if x1 is not None else x0 + len(text) * 4.5
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y0 + 10),
        page=34,
        item_index=f"p34_{y0}_{x0}",
        y_mid=y0 + 5,
    )


def _make_p34_region0_page() -> PageSource:
    items: list[SourceItem] = []
    for label, v24, v23, tail, y in [
        ("其他负债", "2,836,188", "2,150,203", "31.90%待清算款项增加", 85.0),
        ("资本公积", "13,172,237", "8,791,988", "49.82%可转债转股", 100.0),
        ("其他综合收益", "1,308,622", "196,946", "564.46%债券估值变动", 115.0),
    ]:
        items.append(_item(label, 60, y))
        items.append(_item(v24, 120, y, 180))
        items.append(_item(v23, 350, y, 410))
        items.append(_item(tail, 420, y, 520))

    region = RegionBox(29, 78, 518, 140, 0.9, 0)
    return PageSource(
        page_number=34,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[region],
        is_table_page=True,
    )


def test_change_reason_tail_preserves_all_amounts_and_order():
    page = _make_p34_region0_page()
    result = build_page(page)
    table = next(e.table for e in result.entries if e.table)
    rows = dense_rows(table)
    assert len(rows) == 3, rows
    assert table.grid.col_count >= 5, table.grid.col_count

    expected = [
        ("其他负债", "2,836,188", "2,150,203", "31.90%", "待清算款项增加"),
        ("资本公积", "13,172,237", "8,791,988", "49.82%", "可转债转股"),
        ("其他综合收益", "1,308,622", "196,946", "564.46%", "债券估值变动"),
    ]
    for i, exp in enumerate(expected):
        flat = [str(c or "") for c in rows[i]]
        for part in exp:
            assert part in flat, (i, exp, flat)
        assert flat[0] == exp[0], flat


if __name__ == "__main__":
    test_change_reason_tail_preserves_all_amounts_and_order()
    print("P34 change reason tail OK")
