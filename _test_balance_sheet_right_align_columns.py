# -*- coding: utf-8 -*-
"""资产负债表类：右对齐数值列不得按 x0 拆成多列。"""
from __future__ import annotations

from codes.table_engine.geometry.cell_builder import assign_rows_to_columns
from codes.table_engine.geometry.column_anchors import (
    col_index_by_x0,
    col_index_by_x1,
    infer_numeric_data_column_splits,
    item_column_anchor,
)
from codes.table_engine.geometry.data_column_assign import assign_data_value_column
from codes.table_engine.geometry.grid_infer import infer_constraint_grid
from codes.table_engine.table_access import dense_rows


def _row(label: str, v2024: str, v2023: str, pct: str, y: float) -> dict:
    items = [{"text": label, "x0": 51, "x1": 180, "y0": y, "y1": y + 10, "item_index": f"l_{y}"}]
    if v2024:
        x1 = 260 if len(v2024) >= 9 else 248
        x0 = x1 - len(v2024) * 5.5
        items.append({
            "text": v2024, "x0": x0, "x1": x1,
            "y0": y, "y1": y + 10, "item_index": f"a_{y}",
        })
    if v2023:
        x1 = 340 if len(v2023) >= 9 else 328
        x0 = x1 - len(v2023) * 5.5
        items.append({
            "text": v2023, "x0": x0, "x1": x1,
            "y0": y, "y1": y + 10, "item_index": f"b_{y}",
        })
    if pct:
        x1 = 420
        x0 = x1 - len(pct) * 4.5
        items.append({
            "text": pct, "x0": x0, "x1": x1,
            "y0": y, "y1": y + 10, "item_index": f"p_{y}",
        })
    return {"row_phase": "body", "items": items}


def _make_rows() -> list[dict]:
    rows = [
        _row("吸收存款", "885,859,340", "780,421,289", "13.51%", 400),
        _row("-公司客户", "405,454,910", "390,646,696", "3.79%", 416),
        _row("-个人客户", "438,415,512", "354,151,998", "23.79%", 432),
        _row("-保证金存款", "21,627,146", "19,469,813", "11.08%", 448),
        _row("-财政性存款", "130,259", "135,404", "-3.80%", 464),
        _row("-汇出汇款、应解汇款", "79,310", "382,956", "-79.29%", 480),
        _row("-应计利息", "20,152,203", "15,634,422", "28.90%", 496),
        _row("向中央银行借款", "20,818,661", "55,160,650", "-62.26%", 512),
        _row("同业及货币市场融入", "42,538,885", "34,983,372", "21.60%", 528),
        _row("应付债券", "204,933,888", "140,251,078", "46.12%", 544),
        _row("负债总计", "1,164,211,707", "1,019,923,459", "14.15%", 560),
        _row("股东权益合计", "85,904,447", "71,319,610", "20.45%", 576),
        _row("负债及股东权益合计", "1,250,116,154", "1,091,243,069", "14.56%", 592),
    ]
    rows.insert(0, {
        "row_phase": "header",
        "items": [
            {"text": "项目", "x0": 60, "x1": 90, "y0": 380, "y1": 390, "item_index": "h0"},
            {"text": "2024年12月31日", "x0": 200, "x1": 260, "y0": 380, "y1": 390, "item_index": "h1"},
            {"text": "2023年12月31日", "x0": 280, "x1": 340, "y0": 380, "y1": 390, "item_index": "h2"},
            {"text": "增减幅度", "x0": 380, "x1": 420, "y0": 380, "y1": 390, "item_index": "h3"},
        ],
    })
    return rows


def test_numeric_anchor_uses_x1_not_x0():
    assert item_column_anchor({"text": "885,859,340", "x0": 198, "x1": 260}) == 260
    assert item_column_anchor({"text": "130,259", "x0": 230, "x1": 248}) == 248
    assert item_column_anchor({"text": "吸收存款", "x0": 51, "x1": 120}) == 51


def test_infer_three_value_column_gutters_not_six():
    rows = _make_rows()
    splits = infer_numeric_data_column_splits(rows, min_clusters=3)
    assert splits is not None
    assert len(splits) == 2, splits


def test_assign_small_and_large_amounts_same_column():
    rows = _make_rows()
    grid = infer_constraint_grid(rows, 40, 520)
    assert grid is not None, "grid inference failed"
    assert len(grid.col_ranges) <= 5, grid.col_ranges

    col_ranges = grid.col_ranges
    large = {"text": "885,859,340", "x0": 198, "x1": 260}
    small = {"text": "130,259", "x0": 230, "x1": 248}
    assert assign_data_value_column(large, col_ranges) == assign_data_value_column(small, col_ranges)
    assert col_index_by_x1(260, col_ranges) == col_index_by_x1(248, col_ranges)


def test_assign_rows_no_phantom_empty_columns():
    rows = _make_rows()
    grid = infer_constraint_grid(rows, 40, 520)
    assert grid is not None
    matrix = assign_rows_to_columns(rows[1:], grid.col_ranges, "", page=1)
    fiscal = next(r for r in matrix if r and r[0] and r[0].text == "-财政性存款")
    nonempty = [i for i, c in enumerate(fiscal) if c and str(c.text).strip()]
    assert len(nonempty) == 4, [c.text if c else "" for c in fiscal]
    assert fiscal[1].text == "130,259"
    assert fiscal[2].text == "135,404"
    assert fiscal[3].text == "-3.80%"


if __name__ == "__main__":
    test_numeric_anchor_uses_x1_not_x0()
    print("anchor x1 OK")
    test_infer_three_value_column_gutters_not_six()
    print("gutter count OK")
    test_assign_small_and_large_amounts_same_column()
    print("same column assign OK")
    test_assign_rows_no_phantom_empty_columns()
    print("row assign OK")
