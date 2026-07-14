# -*- coding: utf-8 -*-
"""Table Engine：缩进标签不得拆到第 2 列，须保留在 col0 并带前导空格。"""
from __future__ import annotations

from codes.table_engine.geometry.cell_builder import assign_rows_to_columns
from codes.table_engine.geometry.grid_infer import infer_constraint_grid


def _interest_rows_with_indent():
    def row(label: str, y: float, vals, *, x0: float = 54.0):
        items = [{"text": label, "x0": x0, "x1": x0 + len(label) * 11, "y0": y, "y1": y + 10}]
        for v, x1 in vals:
            x0v = x1 - len(v) * 5
            items.append({
                "text": v, "x0": x0v, "x1": x1, "y0": y, "y1": y + 10,
            })
        return {"row_phase": "body", "items": items}

    return [
        row("债券及其他投资", 120, [("8,218,359", 248), ("19.25%", 310), ("9,651,182", 388), ("24.57%", 450)]),
        row("利息收入小计", 140, [("42,697,378", 248), ("100.00%", 310), ("39,287,897", 388), ("100.00%", 450)]),
        {"row_phase": "body", "items": [{"text": "利息支出", "x0": 54, "x1": 100, "y0": 155, "y1": 165}]},
        row("向中央银行借款", 170, [("1,284,079", 248), ("5.30%", 310), ("811,272", 388), ("3.75%", 450)], x0=68),
        row("卖出回购金融资产款", 190, [("482,673", 248), ("1.99%", 310), ("561,794", 388), ("2.60%", 450)], x0=68),
        row("吸收存款", 210, [("17,883,231", 248), ("73.79%", 310), ("15,954,375", 388), ("73.75%", 450)], x0=68),
    ]


def test_grid_not_split_indent_label_column():
    rows = _interest_rows_with_indent()
    grid = infer_constraint_grid(rows, 40, 520)
    assert grid is not None
    assert grid.col_count <= 5, grid.col_ranges


def test_indented_labels_in_col0_with_spaces():
    rows = _interest_rows_with_indent()
    grid = infer_constraint_grid(rows, 40, 520)
    assert grid is not None
    matrix = assign_rows_to_columns(rows, grid.col_ranges, "generic", page=30)
    flat = [[c.text if c else "" for c in row] for row in matrix]

    sub = next(r for r in flat if r and "向中央银行借款" in str(r[0]))
    assert str(sub[0]).startswith("  "), repr(sub[0])
    assert sub[1] == "1,284,079"

    repo = next(r for r in flat if r and "卖出回购金融资产款" in str(r[0]))
    assert repo[0].lstrip().startswith("卖出回购金融资产款")
    assert repo[1] == "482,673"
    assert repo[3] == "561,794"


if __name__ == "__main__":
    test_grid_not_split_indent_label_column()
    print("grid cols OK")
    test_indented_labels_in_col0_with_spaces()
    print("indent col0 OK")
