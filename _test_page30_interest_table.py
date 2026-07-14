# -*- coding: utf-8 -*-
"""Page30 利息表：缩进标签不拆列；标签在行首不跑到行末。"""
from __future__ import annotations

from codes.v2_steps.column_align_utils import (
    assign_words_to_grid,
    col_index_for_word,
    detect_bilateral_column_boundaries,
    estimate_value_x_min,
)


def _interest_table_words():
    """模拟成都银行 P30 利息收支表（坐标近似）。"""
    words = []
    y = 120.0

    def row(label, v24, p24, v23, p23, *, indent=0):
        nonlocal y
        lx0 = 54 + indent
        words.append({
            "text": label, "x0": lx0, "x1": lx0 + len(label) * 11,
            "y0": y, "y1": y + 10,
        })
        for val, x1 in ((v24, 248), (p24, 310), (v23, 388), (p23, 450)):
            if not val:
                continue
            x0 = x1 - len(val) * 5
            words.append({
                "text": val, "x0": x0, "x1": x1,
                "y0": y, "y1": y + 10,
            })
        y += 14

    row("债券及其他投资", "8,218,359", "19.25%", "9,651,182", "24.57%")
    row("利息收入小计", "42,697,378", "100.00%", "39,287,897", "100.00%")
    y += 2
    words.append({
        "text": "利息支出", "x0": 54, "x1": 100,
        "y0": y, "y1": y + 10,
    })
    y += 14
    row("向中央银行借款", "1,284,079", "5.30%", "811,272", "3.75%", indent=14)
    row("吸收存款", "17,883,231", "73.79%", "15,954,375", "73.75%", indent=14)
    row("卖出回购金融资产款", "482,673", "1.99%", "561,794", "2.60%", indent=14)
    row("利息支出小计", "24,236,654", "100.00%", "21,634,346", "100.00%")
    return words


def test_indent_labels_single_column():
    words = _interest_table_words()
    cfg = {"align_tolerance": 8.0}
    bounds = detect_bilateral_column_boundaries(words, 541.0, cfg)
    assert bounds
    n_cols = len(bounds) - 1
    assert n_cols == 5, bounds
    vx = estimate_value_x_min(words)
    top = {"text": "债券及其他投资", "x0": 54, "x1": 120}
    sub = {"text": "向中央银行借款", "x0": 68, "x1": 180}
    assert col_index_for_word(top, bounds, value_x_min=vx) == col_index_for_word(
        sub, bounds, value_x_min=vx
    )


def _grid(words, rows, bounds, cfg):
    out = assign_words_to_grid(words, rows, bounds, cfg)
    if isinstance(out, tuple):
        return out[0], out[1]
    return out, {}


def test_label_stays_at_row_start():
    words = _interest_table_words()
    cfg = {"align_tolerance": 8.0, "row_margin_factor": 0.15, "preserve_label_indent": True}
    bounds = detect_bilateral_column_boundaries(words, 541.0, cfg)
    rows = []
    y = 120.0
    for _ in range(7):
        rows.append((y, y + 10))
        y += 14
    rows.insert(2, (148, 158))
    grid, _ = _grid(words, rows, bounds, cfg)
    repo = next(r for r in grid if r and "卖出回购金融资产款" in r[0])
    assert repo[0].lstrip().startswith("卖出回购金融资产款")
    assert repo[1] == "482,673"
    assert repo[2] == "1.99%"
    assert repo[3] == "561,794"
    assert repo[4] == "2.60%"


def test_interest_expense_own_row():
    words = _interest_table_words()
    cfg = {"align_tolerance": 8.0, "row_margin_factor": 0.15, "preserve_label_indent": True}
    bounds = detect_bilateral_column_boundaries(words, 541.0, cfg)
    rows = []
    y = 120.0
    for _ in range(7):
        rows.append((y, y + 10))
        y += 14
    rows.insert(2, (148, 158))
    grid, _ = _grid(words, rows, bounds, cfg)
    subtotal_row = next(r for r in grid if r and r[0] == "利息收入小计")
    assert subtotal_row[1] == "42,697,378"
    expense_row = next(r for r in grid if r and r[0] == "利息支出")
    assert expense_row[1] == ""
    assert "利息支出" not in subtotal_row[0]


def test_indent_on_sub_items():
    words = _interest_table_words()
    cfg = {
        "align_tolerance": 8.0,
        "row_margin_factor": 0.15,
        "preserve_label_indent": True,
        "indent_step_pt": 12.0,
        "indent_spaces_per_level": 2,
    }
    bounds = detect_bilateral_column_boundaries(words, 541.0, cfg)
    rows = []
    y = 120.0
    for _ in range(8):
        rows.append((y, y + 10))
        y += 14
    grid, meta = _grid(words, rows, bounds, cfg)
    sub = next(r for r in grid if r and "向中央银行借款" in r[0])
    assert sub[0].startswith("  向中央银行借款"), repr(sub[0])
    assert any(v.get("indent_level", 0) >= 1 for v in meta.values())


if __name__ == "__main__":
    test_indent_labels_single_column()
    print("single label col OK")
    test_label_stays_at_row_start()
    print("row order OK")
    test_interest_expense_own_row()
    print("expense row OK")
    test_indent_on_sub_items()
    print("indent OK")
