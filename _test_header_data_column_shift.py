# -*- coding: utf-8 -*-
"""居中表头 + 右对齐数据 → 表头列下方空白、数据列无表头错位修复。"""
from __future__ import annotations

from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.split.grid_prune import (
    realign_value_header_column_shift,
    prune_blank_rows_columns,
)
from codes.table_engine.table_access import dense_rows


def _item(text: str, x: float, y: float, *, x1: float | None = None) -> SourceItem:
    x1 = x1 if x1 is not None else x + len(text) * 5.5
    return SourceItem(
        text=text,
        bbox=BBox(x, y, x1, y + 10),
        page=1,
        item_index=f"i_{y}_{x}",
        y_mid=y + 5,
    )


def _make_expense_table_page() -> PageSource:
    """模拟职工薪酬类 3 列表：表头居中、数值右对齐。"""
    items: list[SourceItem] = []
    items.append(_item("单位：千元", 400, 80, x1=470))

    items.append(_item("项目", 60, 110, x1=95))
    items.append(_item("2024 年", 220, 110, x1=300))
    items.append(_item("2023 年", 380, 110, x1=460))

    rows = [
        ("职工薪酬", 135, "3,155,167", "3,376,609"),
        ("折旧与摊销", 155, "541,792", "532,768"),
        ("租赁费", 175, "5,896", "10,177"),
        ("其他业务费用", 195, "1,796,997", "1,526,373"),
        ("合计", 215, "5,499,852", "5,445,927"),
    ]
    for label, y, v24, v23 in rows:
        items.append(_item(label, 60, y, x1=150))
        items.append(_item(v24, 300, y, x1=355))
        items.append(_item(v23, 460, y, x1=515))

    region = RegionBox(50, 100, 530, 230, 1.0, 0)
    return PageSource(
        page_number=1,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[region],
        is_table_page=True,
    )


def _col_count(table) -> int:
    rows = dense_rows(table)
    return max((len(r) for r in rows), default=0)


def test_expense_table_three_columns_aligned():
    page = _make_expense_table_page()
    result = build_page(page)
    tables = [e.table for e in result.entries if e.table]
    assert tables, "expected one table"
    table = tables[0]
    rows = dense_rows(table)
    ncol = _col_count(table)
    assert ncol == 3, f"expected 3 columns after realign, got {ncol}: {rows[:2]}"

    header_idx = next(
        i for i, row in enumerate(rows) if str(row[0] or "").strip() == "项目"
    )
    header = [str(c or "").strip() for c in rows[header_idx]]
    assert header[0] == "项目", header
    assert "2024" in header[1], header
    assert "2023" in header[2], header

    for row in rows[header_idx + 1 :]:
        cells = [str(c or "").strip() for c in row]
        if not any(cells):
            continue
        if cells[0] in ("单位：千元",) or "千元" in "".join(cells):
            continue
        assert cells[0], cells
        assert is_numeric(cells[1]), f"2024 col empty: {cells}"
        assert is_numeric(cells[2]), f"2023 col empty: {cells}"


def is_numeric(s: str) -> bool:
    from codes.table_engine.geometry.numeric import is_numeric_data_cell
    return is_numeric_data_cell(s)


def test_realign_on_misaligned_grid():
    """直接构造 5 列错位网格，校验 realign + prune。"""
    from codes.table_engine.models import BBox, Cell, ColumnGrid, ColumnRange, StructuredTable

    def mk(text: str, col: int, row: int, *, x0: float) -> Cell:
        w = 70.0
        return Cell(text, BBox(x0, row * 12, x0 + w, row * 12 + 10), row, col, [])

    ncol = 5
    rows = [
        [mk("项目", 0, 0, x0=60), None,
         mk("2024 年", 2, 0, x0=220), None,
         mk("2023 年", 4, 0, x0=400)],
        [mk("职工薪酬", 0, 1, x0=60), None,
         None, mk("3,155,167", 3, 1, x0=300), mk("3,376,609", 4, 1, x0=460)],
        [mk("合计", 0, 2, x0=60), None,
         None, mk("5,499,852", 3, 2, x0=300), mk("5,445,927", 4, 2, x0=460)],
    ]
    ranges = [
        ColumnRange(50, 150, 0),
        ColumnRange(150, 210, 1),
        ColumnRange(210, 270, 2),
        ColumnRange(270, 380, 3),
        ColumnRange(380, 520, 4),
    ]
    table = StructuredTable(
        page=1, pages=[1], y0=0, y1=40, x0=50, x1=520,
        rows=rows,
        grid=ColumnGrid(ranges=ranges, layout_id="generic"),
        layout_id="generic",
    )
    fixed = prune_blank_rows_columns(realign_value_header_column_shift(table))
    dense = dense_rows(fixed)
    assert _col_count(fixed) == 3, dense
    hdr = [str(c or "").strip() for c in dense[0]]
    assert "2024" in hdr[1] and "2023" in hdr[2], hdr
    assert "3,155,167" in dense[1][1], dense[1]


if __name__ == "__main__":
    test_expense_table_three_columns_aligned()
    print("build_page 3-col OK")
    test_realign_on_misaligned_grid()
    print("grid realign OK")
