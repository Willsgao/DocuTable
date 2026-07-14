# -*- coding: utf-8 -*-
"""利润表变化原因表：百分比与说明文字须分列。"""
from __future__ import annotations

from codes.table_engine.geometry.cell_builder import assign_rows_to_columns
from codes.table_engine.geometry.numeric import (
    is_percent_glued_to_reason_text,
    split_percent_amount_reason_text,
    split_percent_trailing_text,
)


def test_split_percent_trailing_text():
    assert split_percent_trailing_text("-41.49%代理业务支出减少") == (
        "-41.49%",
        "代理业务支出减少",
    )
    assert split_percent_trailing_text("338.63%基金估值变动") == (
        "338.63%",
        "基金估值变动",
    )
    assert split_percent_trailing_text("手续费及佣金支出") is None
    # % 与中文之间有空格也必须识别
    assert split_percent_trailing_text("30.69% 拆放同业款项增加") == (
        "30.69%",
        "拆放同业款项增加",
    )
    assert split_percent_trailing_text("-62.26% 中期借贷便利减少") == (
        "-62.26%",
        "中期借贷便利减少",
    )


def test_split_percent_amount_reason_text():
    assert split_percent_amount_reason_text(
        "30.69% 68,823,341 拆放同业款项增加",
    ) == ("30.69%", "68,823,341", "拆放同业款项增加")
    assert split_percent_amount_reason_text(
        "85.75% 2,285,652 清算款项增加及预付购房",
    ) == ("85.75%", "2,285,652", "清算款项增加及预付购房")


def test_misplaced_reason_relocated_to_last_column():
    """主要原因说明不得占项目列；须保持金额/百分比列顺序不变。"""
    from codes.table_engine.geometry.cell_decomposition import relocate_misplaced_reason_labels
    from codes.table_engine.models import BBox, Cell, ColumnGrid, ColumnRange, StructuredTable

    def _cell(text: str, col: int, x0: float = 0) -> Cell:
        return Cell(text=text, bbox=BBox(x0, 0, x0 + 80, 10), row=0, col=col, source_items=[])

    row = [
        _cell("表外业务预期损失计提减", 0, x0=420),
        _cell("221,082", 1, x0=200),
        _cell("352,667", 2, x0=300),
        _cell("-37.31%", 3, x0=400),
        _cell("", 4, x0=480),
    ]
    table = StructuredTable(
        page=1,
        pages=[1],
        rows=[row],
        grid=ColumnGrid(
            ranges=[
                ColumnRange(50, 180, 0),
                ColumnRange(190, 280, 1),
                ColumnRange(290, 380, 2),
                ColumnRange(390, 460, 3),
                ColumnRange(470, 540, 4),
            ],
            layout_id="test",
        ),
        x0=0,
        y0=0,
        x1=1,
        y1=1,
    )
    fixed = relocate_misplaced_reason_labels(table)
    out = [c.text for c in fixed.rows[0]]
    assert out[0] == ""
    assert out[1] == "221,082"
    assert out[2] == "352,667"
    assert out[3] == "-37.31%"
    assert "表外业务" in out[4] and "计提" in out[4]


def test_do_not_split_label_embedded_percent():
    """% 为标签短语一部分时不拆。"""
    assert split_percent_trailing_text("适用1250%风险权重") is None
    assert split_percent_trailing_text("1250%风险权重") is None
    assert split_percent_trailing_text("100%并表") is None
    assert split_percent_trailing_text("除项目19外适用1250%权重") is None
    assert not is_percent_glued_to_reason_text("适用1250%风险权重")


def test_assign_rows_splits_percent_and_reason_columns():
    col_ranges = [
        (54, 150),
        (150, 230),
        (230, 310),
        (310, 390),
        (390, 520),
    ]
    y = 400.0
    row = {
        "row_phase": "body",
        "items": [
            {"text": "手续费及佣金支出", "x0": 60, "x1": 140, "y0": y, "y1": y + 10, "item_index": "a"},
            {"text": "51,968", "x0": 170, "x1": 220, "y0": y, "y1": y + 10, "item_index": "b"},
            {"text": "88,822", "x0": 250, "x1": 300, "y0": y, "y1": y + 10, "item_index": "c"},
            {
                "text": "-41.49%代理业务支出减少",
                "x0": 410,
                "x1": 500,
                "y0": y,
                "y1": y + 10,
                "item_index": "d",
            },
        ],
    }
    matrix = assign_rows_to_columns([row], col_ranges, "", page=1)
    assert len(matrix) == 1
    cells = matrix[0]
    assert cells[0] and cells[0].text == "手续费及佣金支出"
    assert cells[1] and cells[1].text == "51,968"
    assert cells[2] and cells[2].text == "88,822"
    assert cells[3] and cells[3].text == "-41.49%", cells[3].text
    assert cells[4] and cells[4].text == "代理业务支出减少", cells[4].text


if __name__ == "__main__":
    test_split_percent_trailing_text()
    print("split percent text OK")
    test_split_percent_amount_reason_text()
    print("split percent amount reason OK")
    test_misplaced_reason_relocated_to_last_column()
    print("relocate misplaced reason OK")
    test_do_not_split_label_embedded_percent()
    print("label embedded percent kept OK")
    test_assign_rows_splits_percent_and_reason_columns()
    print("assign columns OK")
