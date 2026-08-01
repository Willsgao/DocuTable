# -*- coding: utf-8 -*-
"""缓存表数值+文本粘连就地拆列。"""

from codes.v2_steps.table_glue_repair import (
    repair_table_numeric_text_glue,
    split_glue_cell,
)


def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print(f"  OK  {name}")


if __name__ == "__main__":
    print("=== table glue repair ===")
    check("双表头", split_glue_cell("地区 营业收入") == ("地区", "营业收入"))
    check("金额+地区", split_glue_cell("19,079,642 成都") == ("成都", "19,079,642"))
    check("其他地区", split_glue_cell("3,901,885 其他地区") == ("其他地区", "3,901,885"))

    table = {
        "type": "table",
        "page": 29,
        "data": [
            ["", "", "", "", "单位：千元", ""],
            ["地区 营业收入", "占比", "比去年增减", "营业利润", "占比", "比去年增减"],
            ["19,079,642 成都", "83.02%", "535,270", "12,260,416", "80.52%", "610,456"],
            ["3,901,885 其他地区", "16.98%", "744,068", "2,966,143", "19.48%", "710,935"],
        ],
    }
    notes = repair_table_numeric_text_glue(table)
    check("有修复说明", bool(notes))
    check("列数变7", table["cols"] == 7)
    check("表头拆开", table["data"][1][0] == "地区" and table["data"][1][1] == "营业收入")
    check("成都独立", table["data"][2][0] == "成都")
    check("金额独立", table["data"][2][1] == "19,079,642")
    check("其他地区独立", table["data"][3][0] == "其他地区")
    print("ALL PASS")
