# -*- coding: utf-8 -*-
"""对外股权投资表：报告期损益与会计科目不得粘在同一格。"""
from __future__ import annotations

from codes.table_engine.geometry.cell_builder import assign_rows_to_columns
from codes.table_engine.geometry.numeric import split_value_trailing_text_label
from codes.table_engine.models import BBox, Cell, ColumnGrid, ColumnRange, StructuredTable
from codes.table_engine.split.structure_split import repair_glued_percent_reason_cells
from codes.table_engine.table_access import dense_rows


def test_split_value_trailing_text_label():
    assert split_value_trailing_text_label("5,780 交易性金融资产") == (
        "5,780", "交易性金融资产",
    )
    assert split_value_trailing_text_label("- 交易性金融资产") == (
        "-", "交易性金融资产",
    )
    assert split_value_trailing_text_label("77,720 长期股权投资") == (
        "77,720", "长期股权投资",
    )
    assert split_value_trailing_text_label("30.69% 拆放同业款项增加") is None


def test_seven_col_matrix_decompose():
    """第6列损益 + 第7列科目：粘连须拆到倒数两列。"""

    def _cell(text: str, col: int, y: float) -> Cell:
        xs = [50, 150, 230, 310, 390, 460, 530]
        x0 = xs[col] if col < len(xs) else 530
        return Cell(
            text=text,
            bbox=BBox(x0, y, x0 + 60, y + 10),
            row=int(y / 16),
            col=col,
            source_items=[],
        )

    y = 80.0
    rows = [
        [
            _cell("被投资企业", 0, 60),
            _cell("初始投资金额", 1, 60),
            _cell("持股数量", 2, 60),
            _cell("股权比例", 3, 60),
            _cell("期末账面值", 4, 60),
            _cell("报告期损益", 5, 60),
            _cell("会计科目", 6, 60),
        ],
        [
            _cell("某银行", 0, y),
            _cell("1,000", 1, y),
            _cell("100", 2, y),
            _cell("5%", 3, y),
            _cell("9,999", 4, y),
            None,
            _cell("5,780 交易性金融资产", 6, y),
        ],
        [
            _cell("某公司", 0, y + 16),
            _cell("2,000", 1, y + 16),
            _cell("200", 2, y + 16),
            _cell("10%", 3, y + 16),
            _cell("8,888", 4, y + 16),
            None,
            _cell("- 交易性金融资产", 6, y + 16),
        ],
        [
            _cell("某集团", 0, y + 32),
            _cell("3,000", 1, y + 32),
            _cell("300", 2, y + 32),
            _cell("15%", 3, y + 32),
            _cell("7,777", 4, y + 32),
            None,
            _cell("77,720 长期股权投资", 6, y + 32),
        ],
    ]
    grid = ColumnGrid(
        ranges=[
            ColumnRange(50, 140, 0),
            ColumnRange(150, 220, 1),
            ColumnRange(230, 300, 2),
            ColumnRange(310, 380, 3),
            ColumnRange(390, 450, 4),
            ColumnRange(460, 520, 5),
            ColumnRange(530, 590, 6),
        ],
        layout_id="test",
    )
    table = StructuredTable(
        page=1,
        pages=[1],
        y0=60,
        y1=120,
        x0=50,
        x1=590,
        rows=rows,
        grid=grid,
    )
    fixed = repair_glued_percent_reason_cells(table)
    body = dense_rows(fixed)[1:]
    assert body[0][5] == "5,780", body[0]
    assert body[0][6] == "交易性金融资产", body[0]
    assert body[1][5] == "-", body[1]
    assert body[1][6] == "交易性金融资产", body[1]
    assert body[2][5] == "77,720", body[2]
    assert body[2][6] == "长期股权投资", body[2]


def test_assign_rows_splits_glued_item():
    col_ranges = [
        (50, 140), (150, 220), (230, 300), (310, 380),
        (390, 450), (460, 520), (530, 590),
    ]
    y = 80.0
    row = {
        "row_phase": "body",
        "items": [
            {"text": "某银行", "x0": 60, "x1": 120, "y0": y, "y1": y + 10, "item_index": "a"},
            {"text": "5,780", "x0": 400, "x1": 450, "y0": y, "y1": y + 10, "item_index": "b"},
            {"text": "5,780 交易性金融资产", "x0": 540, "x1": 580, "y0": y, "y1": y + 10, "item_index": "c"},
        ],
    }
    matrix = assign_rows_to_columns([row], col_ranges, "", page=1)
    cells = matrix[0]
    assert cells[5] and cells[5].text == "5,780", [c.text if c else None for c in cells]
    assert cells[6] and cells[6].text == "交易性金融资产"


if __name__ == "__main__":
    test_split_value_trailing_text_label()
    print("split value+label OK")
    test_seven_col_matrix_decompose()
    print("matrix decompose OK")
    test_assign_rows_splits_glued_item()
    print("assign rows OK")
