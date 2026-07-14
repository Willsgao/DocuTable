# -*- coding: utf-8 -*-
"""OV1 序号披露表：中间无表头不得拆成多张 TABLE。"""
from __future__ import annotations

from codes.table_engine.split.structure_split import (
    find_body_structure_break,
    find_structure_break_row,
)


def _ov1_rows() -> list[list[str]]:
    return [
        ["a", "b", "c"],
        ["（人民币百万元）", "", "", "风险加权资产", "最低资本要求"],
        ["", "", "2024 年", "2024 年", "2024 年"],
        ["", "", "12 月 31 日", "9 月 30 日", "12 月 31 日"],
        ["1", "信用风险", "19,814,943", "20,185,885", "1,585,195"],
        [
            "2",
            "信用风险（不包括交易对手信用风险、信用估值调整风险、"
            "银行账簿资产管理产品和银行账簿资产证券化）",
            "19,433,391",
            "19,818,263",
            "1,554,670",
        ],
        ["3", "其中：权重法 其中：证券、商品、外汇交易清", "5,820,738", "5,596,995", "465,658"],
        ["4", "其中：证券、商品、外汇交易清", "0", "0", "0"],
        ["", "算过程中形成的风险暴露", "", "", ""],
        ["5", "其中：门槛扣除项中未扣除部分", "363,177", "369,459", "29,054"],
        ["18", "银行账簿资产证券化 1", "17,647", "18,282", "1,412"],
        ["19 20", "其中：资产证券化内部评级法 其中：资产证券化外部评级法", "- 0", "- 0", "- 0"],
        ["20", "其中：资产证券化外部评级法", "0", "0", "0"],
        ["21", "其中：资产证券化标准法", "7,818", "7,813", "625"],
        ["29", "合计", "21,854,590", "22,150,555", "1,748,367"],
    ]


def test_ov1_no_mid_table_split():
    rows = _ov1_rows()
    assert find_body_structure_break(rows) < 0, find_body_structure_break(rows)
    assert find_structure_break_row(rows) < 0, find_structure_break_row(rows)
    assert any(str(r[0]).strip() == "29" for r in rows)


if __name__ == "__main__":
    test_ov1_no_mid_table_split()
    print("no mid split OK")
