# -*- coding: utf-8 -*-
"""数值+百分点粘连 → 右侧空列 spill。"""
from __future__ import annotations

from codes.v2_steps.table_glue_repair import (
    repair_table_percent_point_spill,
    repair_tables_numeric_text_glue,
    split_glue_cell,
)


def test_percent_point_not_numeric_text_glue():
    assert split_glue_cell("1.28 下降0.09个百分点") is None


def test_percent_point_spill_into_next_empty():
    table = {
        "page": 14,
        "data": [
            ["项目", "2024", "2023", "增减", "2022"],
            ["平均总资产收益率", "1.19", "1.28 下降0.09个百分点", "", "1.39"],
            ["加权平均净资产收益率", "13.44", "14.49 下降1.05个百分点", "", "16.22"],
            ["净息差", "1.50", "1.66 下降0.11个百分点", "0.01", "1.70"],
        ],
    }
    notes = repair_table_percent_point_spill(table)
    assert notes
    assert table["data"][1][2] == "1.28"
    assert table["data"][1][3] == "下降0.09个百分点"
    assert table["data"][2][2] == "14.49"
    assert table["data"][2][3] == "下降1.05个百分点"
    # 右侧非空则不覆盖
    assert table["data"][3][2] == "1.66 下降0.11个百分点"
    assert table["data"][3][3] == "0.01"


def test_repair_tables_runs_spill_first():
    tables = [
        {
            "page": 1,
            "data": [
                ["a", "b", "c", "d"],
                ["x", "1.28 下降0.09个百分点", "", "1.39"],
            ],
        }
    ]
    _, notes = repair_tables_numeric_text_glue(tables)
    assert any("百分点" in n for n in notes)
    assert tables[0]["data"][1][1] == "1.28"
    assert tables[0]["data"][1][2] == "下降0.09个百分点"
