# -*- coding: utf-8 -*-
"""双边列对齐：右对齐数值不得按 x0 拆列；落格顺序与锚点正确。"""
from __future__ import annotations

from codes.v2_steps.column_align_utils import (
    assign_words_to_grid,
    col_index_for_word,
    detect_bilateral_column_boundaries,
    estimate_value_x_min,
    word_alignment_anchor,
)


def _balance_sheet_words():
    rows_y = [400, 416, 432, 448, 464, 480]
    words = [
        {"text": "项目", "x0": 60, "x1": 90, "y0": 380, "y1": 390},
        {"text": "2024年12月31日", "x0": 200, "x1": 260, "y0": 380, "y1": 390},
        {"text": "2023年12月31日", "x0": 280, "x1": 340, "y0": 380, "y1": 390},
        {"text": "增减幅度", "x0": 380, "x1": 420, "y0": 380, "y1": 390},
    ]
    specs = [
        ("吸收存款", "885,859,340", "780,421,289", "13.51%"),
        ("-财政性存款", "130,259", "135,404", "-3.80%"),
        ("负债总计", "1,164,211,707", "1,019,923,459", "14.15%"),
    ]
    for label, v24, v23, pct, y in [
        (s[0], s[1], s[2], s[3], rows_y[i]) for i, s in enumerate(specs)
    ]:
        words.append({"text": label, "x0": 51, "x1": 160, "y0": y, "y1": y + 10})
        for val, x1 in ((v24, 260 if len(v24) >= 9 else 248),
                        (v23, 340 if len(v23) >= 9 else 328),
                        (pct, 420)):
            x0 = x1 - len(val) * 5.0
            words.append({"text": val, "x0": x0, "x1": x1, "y0": y, "y1": y + 10})
    return words


def test_value_anchor_uses_x1():
    w = {"text": "130,259", "x0": 230, "x1": 248}
    anchor, mode = word_alignment_anchor(w, value_x_min=180)
    assert mode == "right"
    assert anchor == 248


def test_bilateral_boundaries_three_value_cols_not_six():
    words = _balance_sheet_words()
    cfg = {"align_tolerance": 8.0, "gap_factor": 0.3, "gap_min": 10.0}
    bounds = detect_bilateral_column_boundaries(words, 520.0, cfg)
    assert bounds is not None
    n_cols = len(bounds) - 1
    assert n_cols <= 5, bounds
    assert n_cols >= 4, bounds


def test_small_large_amount_same_column_index():
    words = _balance_sheet_words()
    cfg = {"align_tolerance": 8.0}
    bounds = detect_bilateral_column_boundaries(words, 520.0, cfg)
    assert bounds
    vx = estimate_value_x_min(words)
    large = {"text": "885,859,340", "x0": 198, "x1": 260}
    small = {"text": "130,259", "x0": 230, "x1": 248}
    assert col_index_for_word(large, bounds, value_x_min=vx) == col_index_for_word(
        small, bounds, value_x_min=vx
    )


def test_assign_grid_preserves_order_and_data():
    words = _balance_sheet_words()
    cfg = {"align_tolerance": 8.0, "row_margin_factor": 0.3, "preserve_label_indent": True}
    col_bounds = detect_bilateral_column_boundaries(words, 520.0, cfg)
    assert col_bounds
    row_bounds = [(378, 392), (398, 412), (414, 428), (430, 444), (446, 460), (462, 476)]
    out = assign_words_to_grid(words, row_bounds, col_bounds, cfg)
    grid = out[0] if isinstance(out, tuple) else out
    fiscal = next(r for r in grid if r and r[0] == "-财政性存款")
    assert fiscal[1] == "130,259"
    assert fiscal[2] == "135,404"
    assert fiscal[3] == "-3.80%"


if __name__ == "__main__":
    test_value_anchor_uses_x1()
    print("anchor OK")
    test_bilateral_boundaries_three_value_cols_not_six()
    print("boundaries OK")
    test_small_large_amount_same_column_index()
    print("same col OK")
    test_assign_grid_preserves_order_and_data()
    print("grid OK")
