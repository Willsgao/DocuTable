# -*- coding: utf-8 -*-
"""地区分布表：地区 | 营业收入 不得并入同一列。"""

from codes.table_engine.geometry.cell_builder import assign_rows_to_columns
from codes.table_engine.geometry.column_anchors import is_value_column_header_text
from codes.table_engine.geometry.grid_infer import (
    _ranges_from_numeric_gutters,
    refine_col_ranges_by_coordinates,
)


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"  OK  {name}")


def _region_dist_rows():
    """成都银行年报 P29 地区分布（liteparse 坐标）。"""
    return [
        {
            "row_phase": "header",
            "items": [
                {"text": "地区", "x0": 73.7, "x1": 94.6, "y0": 441.2, "y1": 451.6},
                {"text": "营业收入", "x0": 133.2, "x1": 175.1, "y0": 441.2, "y1": 451.6},
                {"text": "占比", "x0": 213.6, "x1": 234.5, "y0": 441.2, "y1": 451.6},
                {"text": "比去年增减", "x0": 272.8, "x1": 325.2, "y0": 441.2, "y1": 451.6},
                {"text": "营业利润", "x0": 351.5, "x1": 393.4, "y0": 441.2, "y1": 451.6},
                {"text": "占比", "x0": 427.2, "x1": 448.1, "y0": 441.2, "y1": 451.6},
                {"text": "比去年增减", "x0": 481.2, "x1": 533.6, "y0": 441.2, "y1": 451.6},
            ],
        },
        {
            "row_phase": "body",
            "items": [
                {"text": "成都", "x0": 54.6, "x1": 75.5, "y0": 464.4, "y1": 476.8},
                {"text": "19,079,642", "x0": 131.2, "x1": 183.7, "y0": 462.6, "y1": 476.8},
                {"text": "83.02%", "x0": 213.0, "x1": 250.0, "y0": 462.6, "y1": 476.8},
                {"text": "535,270", "x0": 280.0, "x1": 330.0, "y0": 462.6, "y1": 476.8},
                {"text": "12,260,416", "x0": 348.2, "x1": 400.7, "y0": 462.6, "y1": 476.8},
                {"text": "80.52%", "x0": 427.0, "x1": 465.0, "y0": 462.6, "y1": 476.8},
                {"text": "610,456", "x0": 490.0, "x1": 540.0, "y0": 462.6, "y1": 476.8},
            ],
        },
        {
            "row_phase": "body",
            "items": [
                {"text": "其他地区", "x0": 54.6, "x1": 96.5, "y0": 487.5, "y1": 500.0},
                {"text": "3,901,885", "x0": 137.0, "x1": 183.7, "y0": 485.8, "y1": 500.0},
                {"text": "16.98%", "x0": 213.0, "x1": 250.0, "y0": 485.8, "y1": 500.0},
                {"text": "744,068", "x0": 280.0, "x1": 330.0, "y0": 485.8, "y1": 500.0},
                {"text": "2,966,143", "x0": 348.2, "x1": 400.7, "y0": 485.8, "y1": 500.0},
                {"text": "19.48%", "x0": 427.0, "x1": 465.0, "y0": 485.8, "y1": 500.0},
                {"text": "710,935", "x0": 490.0, "x1": 540.0, "y0": 485.8, "y1": 500.0},
            ],
        },
    ]


if __name__ == "__main__":
    print("=== region / revenue column split ===")
    check("营业收入是数值列表头", is_value_column_header_text("营业收入"))
    rows = _region_dist_rows()
    all_items = [it for r in rows for it in r["items"]]
    x_lo, x_hi = 39.6, 555.29
    gutter = _ranges_from_numeric_gutters(rows, all_items, x_lo, x_hi)
    check("numeric_gutter 产出列界", bool(gutter) and len(gutter) >= 6)
    # 地区与营业收入必须分列：切点应在 ~100–130
    check(
        "标签列右界在营业收入左侧",
        gutter[0][1] < 133.0 and gutter[0][1] > 90.0,
    )

    # 旧坏列界（用户缓存）：应被 refine 切成 7 列
    bad = [
        (39.6, 210.16),
        (210.16, 274.525),
        (274.525, 340.765),
        (340.765, 414.445),
        (414.445, 483.025),
        (483.025, 555.29),
    ]
    refined = refine_col_ranges_by_coordinates(rows, bad, x_lo, x_hi)
    check("refine 后列数>=7", len(refined) >= 7)
    check("refine 切开地区/收入", refined[0][1] < 133.0)

    matrix = assign_rows_to_columns(rows, refined, "generic", 29)
    hdr = [
        ((matrix[0][c].text if matrix[0][c] else "") or "").strip()
        for c in range(len(refined))
    ]
    body0 = [
        ((matrix[1][c].text if matrix[1][c] else "") or "").strip()
        for c in range(len(refined))
    ]
    print("  header:", hdr)
    print("  body0:", body0)
    check("表头无「地区 营业收入」粘连", not any("地区" in h and "营业收入" in h for h in hdr))
    check("地区单独一格", "地区" in hdr)
    check("营业收入单独一格", "营业收入" in hdr)
    check("成都在地区列", "成都" in body0)
    check("金额不与成都粘连", not any("成都" in c and "," in c for c in body0))
    check("金额单独", "19,079,642" in body0)
    print("ALL PASS")
