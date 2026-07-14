# -*- coding: utf-8 -*-
"""投资状况表：多行会计科目标签须留在第一列，不得因含「变动」落到末列。"""
from __future__ import annotations

from codes.table_engine.geometry.cell_builder import assign_rows_to_columns
from codes.table_engine.geometry.numeric import looks_like_change_reason_description_not_label
from codes.table_engine.models import BBox, PageSource, RegionBox, SourceItem
from codes.table_engine.pipeline import build_page
from codes.table_engine.split.structure_split import repair_wrapped_label_suffix_rows
from codes.table_engine.table_access import dense_rows
from codes.table_engine.models import Cell, ColumnGrid, ColumnRange, StructuredTable


def _item(text: str, x0: float, y0: float, x1: float | None = None) -> SourceItem:
    x1 = x1 if x1 is not None else x0 + len(text) * 4.5
    return SourceItem(
        text=text,
        bbox=BBox(x0, y0, x1, y0 + 10),
        page=1,
        item_index=f"i_{y0}_{x0}",
        y_mid=y0 + 5,
    )


def test_statement_label_not_treated_as_change_reason():
    t = "以公允价值计量且其变动计入其他综合收益的发放贷款和垫款"
    assert not looks_like_change_reason_description_not_label(t)
    assert not looks_like_change_reason_description_not_label("以公允价值计量且其变动计")


def test_wrapped_fair_value_label_stays_in_column_one():
    """三行折行标签 + 数值列：标签全文在第一列，数值顺序不变。"""
    col_ranges = [(50, 200), (210, 310), (320, 420), (430, 530)]
    y_mid = 200.0
    rows = [
        {
            "row_phase": "header",
            "items": [
                {"text": "项目", "x0": 60, "x1": 90, "y0": 160, "y1": 170, "item_index": "h0"},
                {"text": "期初余额", "x0": 220, "x1": 280, "y0": 160, "y1": 170, "item_index": "h1"},
                {"text": "期末余额", "x0": 330, "x1": 390, "y0": 160, "y1": 170, "item_index": "h2"},
                {"text": "当期变动", "x0": 440, "x1": 500, "y0": 160, "y1": 170, "item_index": "h3"},
            ],
        },
        {
            "row_phase": "body",
            "items": [
                {"text": "以公允价值计量且其变动计", "x0": 60, "x1": 195, "y0": 188, "y1": 198, "item_index": "l1"},
            ],
        },
        {
            "row_phase": "body",
            "items": [
                {"text": "入其他综合收益的发放贷款", "x0": 60, "x1": 195, "y0": y_mid, "y1": y_mid + 10, "item_index": "l2"},
                {"text": "337,393", "x0": 220, "x1": 275, "y0": y_mid, "y1": y_mid + 10, "item_index": "v1"},
                {"text": "1,452,480", "x0": 330, "x1": 395, "y0": y_mid, "y1": y_mid + 10, "item_index": "v2"},
                {"text": "1,115,087", "x0": 440, "x1": 505, "y0": y_mid, "y1": y_mid + 10, "item_index": "v3"},
            ],
        },
        {
            "row_phase": "body",
            "items": [
                {"text": "和垫款", "x0": 60, "x1": 100, "y0": 212, "y1": 222, "item_index": "l3"},
            ],
        },
    ]
    matrix = assign_rows_to_columns(rows, col_ranges, "", page=1)
    line1 = matrix[1][0]
    assert line1 is not None and line1.col == 0, (line1.col if line1 else None, line1.text if line1 else None)
    assert "以公允价值" in line1.text
    body = matrix[2]
    assert body[0] and body[0].col == 0
    assert body[1] and body[1].text == "337,393"
    assert body[2] and body[2].text == "1,452,480"
    assert body[3] and body[3].text == "1,115,087"
    for ci in range(1, 4):
        for row_cells in matrix:
            for c in row_cells:
                if c and c.col == ci:
                    assert "公允价值" not in str(c.text or "")


def test_repair_merges_wrapped_label_head_and_tail():
    def _cell(text: str, col: int, row: int) -> Cell:
        return Cell(text=text, bbox=BBox(60, row * 16, 190, row * 16 + 10), row=row, col=col, source_items=[])

    rows = [
        [_cell("以公允价值计量且其变动计", 0, 0), None, None, None],
        [_cell("入其他综合收益的发放贷款", 0, 1), _cell("337,393", 1, 1), _cell("1,452,480", 2, 1), _cell("1,115,087", 3, 1)],
        [_cell("和垫款", 0, 2), None, None, None],
    ]
    table = StructuredTable(
        page=1,
        pages=[1],
        y0=0,
        y1=50,
        x0=50,
        x1=530,
        rows=rows,
        grid=ColumnGrid(
            ranges=[
                ColumnRange(50, 200, 0),
                ColumnRange(210, 310, 1),
                ColumnRange(320, 420, 2),
                ColumnRange(430, 530, 3),
            ],
            layout_id="test",
        ),
    )
    fixed = repair_wrapped_label_suffix_rows(table)
    out = dense_rows(fixed)
    assert len(out) == 1, out
    label = str(out[0][0] or "")
    assert "以公允价值计量且其变动计入其他综合收益的发放贷款和垫款" == label, label
    assert out[0][1] == "337,393"
    assert out[0][2] == "1,452,480"
    assert out[0][3] == "1,115,087"


def test_build_page_fair_value_investment_row():
    y = 200.0
    items = [
        _item("项目", 60, 160),
        _item("期初余额", 220, 160),
        _item("期末余额", 330, 160),
        _item("当期变动", 440, 160),
        _item("衍生金融资产", 60, 180),
        _item("271,967", 220, 180),
        _item("324,633", 330, 180),
        _item("52,666", 440, 180),
        _item("以公允价值计量且其变动计", 60, 188),
        _item("入其他综合收益的发放贷款", 60, y),
        _item("337,393", 220, y),
        _item("1,452,480", 330, y),
        _item("1,115,087", 440, y),
        _item("和垫款", 60, 212),
    ]
    page = PageSource(
        page_number=1,
        page_width=595,
        page_height=842,
        items=items,
        table_regions=[RegionBox(29, 140, 566, 280, 1, 0)],
        is_table_page=True,
    )
    result = build_page(page)
    table = next(e.table for e in result.entries if e.table)
    row = next(
        r for r in dense_rows(table)
        if any("337,393" in str(c or "") for c in r)
    )
    label = str(row[0] or "")
    assert "以公允价值" in label and "和垫款" in label, row
    assert str(row[1] or "") == "337,393"
    assert str(row[2] or "") == "1,452,480"
    assert str(row[3] or "") == "1,115,087"
    assert not any("公允价值" in str(c or "") for c in row[1:] if c), row


if __name__ == "__main__":
    test_statement_label_not_treated_as_change_reason()
    print("statement label detection OK")
    test_wrapped_fair_value_label_stays_in_column_one()
    print("assign columns OK")
    test_repair_merges_wrapped_label_head_and_tail()
    print("wrap merge OK")
    test_build_page_fair_value_investment_row()
    print("build page OK")
