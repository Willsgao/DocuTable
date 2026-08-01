# -*- coding: utf-8 -*-
"""主要财务指标：项目|2024|2023|增减%|2022 五列不得丢 2022。"""

from codes.table_engine.geometry.cell_builder import assign_rows_to_columns
from codes.table_engine.geometry.grid_infer import (
    refine_col_ranges_by_coordinates,
    _ranges_from_numeric_gutters,
    _ranges_from_distinct_header_items,
)


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"  OK  {name}")


def _rows():
    """成都银行 P14 主要财务指标表（liteparse 坐标）。"""
    return [
        {
            "row_phase": "header",
            "items": [
                {"text": "单位", "x0": 63.8, "x1": 84.7, "y0": 593.0, "y1": 604},
                {"text": "2024 年", "x0": 204.5, "x1": 240.8, "y0": 606.0, "y1": 617},
                {"text": "2023 年", "x0": 286.4, "x1": 322.8, "y0": 606.0, "y1": 617},
                {"text": "本报告期末比上年度末增减", "x0": 349.7, "x1": 444.1, "y0": 607.9, "y1": 630},
                {"text": "2022 年", "x0": 471.0, "x1": 507.4, "y0": 606.0, "y1": 617},
                {"text": "项目", "x0": 112.8, "x1": 133.7, "y0": 614.6, "y1": 626},
                {"text": "12 月 31 日", "x0": 196.6, "x1": 248.8, "y0": 619.5, "y1": 631},
                {"text": "12 月 31 日", "x0": 278.6, "x1": 330.7, "y0": 619.5, "y1": 631},
                {"text": "末增减", "x0": 381.2, "x1": 412.7, "y0": 621.4, "y1": 633},
                {"text": "12 月 31 日", "x0": 463.1, "x1": 515.3, "y0": 619.5, "y1": 631},
            ],
        },
        {
            "row_phase": "body",
            "items": [
                {"text": "总资产", "x0": 63.8, "x1": 95.3, "y0": 640.9, "y1": 652},
                {"text": "1,250,116,154", "x0": 194.2, "x1": 261.2, "y0": 639.0, "y1": 651},
                {"text": "1,091,243,069", "x0": 279.5, "x1": 346.5, "y0": 639.0, "y1": 651},
                {"text": "14.56%", "x0": 410.3, "x1": 445.7, "y0": 639.0, "y1": 651},
                {"text": "917,650,305", "x0": 472.7, "x1": 530.9, "y0": 639.0, "y1": 651},
            ],
        },
        {
            "row_phase": "body",
            "items": [
                {"text": "发放贷款和垫款总额", "x0": 63.8, "x1": 158.3, "y0": 664.8, "y1": 676},
                {"text": "742,568,225", "x0": 202.9, "x1": 261.2, "y0": 663.0, "y1": 675},
                {"text": "625,742,219", "x0": 288.2, "x1": 346.5, "y0": 663.0, "y1": 675},
                {"text": "18.67%", "x0": 410.3, "x1": 445.7, "y0": 663.0, "y1": 675},
                {"text": "487,826,670", "x0": 472.7, "x1": 530.9, "y0": 663.0, "y1": 675},
            ],
        },
    ]


if __name__ == "__main__":
    print("=== P14 five-column metrics ===")
    rows = _rows()
    all_items = [it for r in rows for it in r["items"]]
    x_lo, x_hi = 48.84, 545.0
    gutter = _ranges_from_numeric_gutters(rows, all_items, x_lo, x_hi)
    hdr = _ranges_from_distinct_header_items(rows, x_lo, x_hi)
    print("  gutter", gutter)
    print("  hdr", hdr)

    bad = [
        (48.84, 186.725),
        (186.725, 348.085),
        (348.085, 375.445),
        (375.445, 531.15),
    ]
    refined = refine_col_ranges_by_coordinates(rows, bad, x_lo, x_hi)
    print("  refined", refined)
    check("列数>=5", len(refined) >= 5)

    matrix = assign_rows_to_columns(rows, refined, "generic", 14)
    # body row index 1 (after header band)
    body = None
    for ri in range(len(matrix)):
        cells = [
            ((matrix[ri][c].text if matrix[ri][c] else "") or "").strip()
            for c in range(len(refined))
        ]
        if "总资产" in cells:
            body = cells
            break
    print("  body", body)
    check("有总资产行", body is not None)
    check("含2024金额", "1,250,116,154" in body)
    check("含2023金额", "1,091,243,069" in body)
    check("含增减%", "14.56%" in body)
    check("含2022金额(不丢列)", "917,650,305" in body)
    check("五值同在", len([c for c in body if c]) >= 5)
    print("ALL PASS")
